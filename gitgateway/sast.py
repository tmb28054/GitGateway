"""Built-in SAST pipeline for GitGateway.

Orchestrates security scanning using Semgrep, Bandit, pip-audit,
Trivy, Checkov, yamllint, and check-jsonschema. Parses findings
into PR comments and status checks.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum

LOG = logging.getLogger(__name__)

BUILDSPEC_FILE = "sast_buildspec.yaml"


class Severity(Enum):
    """Finding severity levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class Finding:
    """A single SAST finding.

    Attributes:
        tool: The tool that produced the finding.
        file_path: File where the issue was found.
        line: Line number of the issue.
        severity: Severity level.
        message: Description of the issue.
        rule_id: The rule or check identifier.
    """

    tool: str
    file_path: str
    line: int
    severity: Severity
    message: str
    rule_id: str = ""

    def to_dict(self) -> dict:
        """Serialize to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "tool": self.tool,
            "file_path": self.file_path,
            "line": self.line,
            "severity": self.severity.value,
            "message": self.message,
            "rule_id": self.rule_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Finding:
        """Deserialize from dictionary.

        Args:
            data: Dictionary.

        Returns:
            A Finding instance.
        """
        return cls(
            tool=data.get("tool", ""),
            file_path=data.get("file_path", ""),
            line=int(data.get("line", 0)),
            severity=Severity(data.get("severity", "info")),
            message=data.get("message", ""),
            rule_id=data.get("rule_id", ""),
        )


@dataclass
class SastResult:
    """Aggregated SAST scan result.

    Attributes:
        findings: List of all findings.
        passed: Whether the scan passed (no high/critical).
        summary: Human-readable summary.
    """

    findings: list[Finding] = field(default_factory=list)
    passed: bool = True
    summary: str = ""

    def to_dict(self) -> dict:
        """Serialize to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "findings": [f.to_dict() for f in self.findings],
            "passed": self.passed,
            "summary": self.summary,
        }


def generate_buildspec(tools: list[str] | None = None) -> str:
    """Generate a CodeBuild buildspec for the SAST pipeline.

    Args:
        tools: Optional list of tools to include. Defaults to all.

    Returns:
        YAML buildspec content as a string.
    """
    all_tools = tools or [
        "semgrep", "bandit", "pip-audit",
        "trivy", "checkov", "yamllint", "check-jsonschema",
    ]

    install_commands = [
        "pip install semgrep bandit pip-audit checkov yamllint "
        "check-jsonschema",
        "curl -sfL https://raw.githubusercontent.com/aquasecurity/"
        "trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin",
    ]

    build_commands = []
    for tool in all_tools:
        cmd = _tool_command(tool)
        if cmd:
            build_commands.append(cmd)

    buildspec = (
        "version: 0.2\n"
        "phases:\n"
        "  install:\n"
        "    commands:\n"
    )
    buildspec += "".join(f"      - {cmd}\n" for cmd in install_commands)

    buildspec += "  build:\n    commands:\n"
    buildspec += "".join(f"      - {cmd}\n" for cmd in build_commands)

    buildspec += (
        "artifacts:\n"
        "  files:\n"
        "    - sast-results.json\n"
    )

    return buildspec


def _tool_command(tool: str) -> str:
    """Get the shell command for a specific SAST tool.

    Args:
        tool: Tool name.

    Returns:
        Shell command string, or empty if unknown tool.
    """
    commands = {
        "semgrep": (
            "semgrep scan --config=auto --json "
            "--output=semgrep-results.json . || true"
        ),
        "bandit": (
            "bandit -r . -f json -o bandit-results.json "
            "--exclude ./.venv || true"
        ),
        "pip-audit": (
            "pip-audit --format=json --output=pip-audit-results.json "
            "|| true"
        ),
        "trivy": (
            "trivy fs --format json --output trivy-results.json . "
            "|| true"
        ),
        "checkov": (
            "checkov -d . --output-file-path . "
            "--output json || true"
        ),
        "yamllint": (
            "yamllint -f parsable . > yamllint-results.txt 2>&1 || true"
        ),
        "check-jsonschema": (
            "find . -name '*.json' -exec check-jsonschema {} \\; "
            "> jsonschema-results.txt 2>&1 || true"
        ),
    }
    return commands.get(tool, "")


def parse_semgrep_results(raw_json: str) -> list[Finding]:
    """Parse Semgrep JSON output into findings.

    Args:
        raw_json: Raw JSON output from semgrep.

    Returns:
        List of Finding objects.
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        LOG.warning("Failed to parse semgrep results")
        return []

    findings = []
    for result in data.get("results", []):
        severity_str = result.get("extra", {}).get("severity", "INFO")
        findings.append(Finding(
            tool="semgrep",
            file_path=result.get("path", ""),
            line=int(result.get("start", {}).get("line", 0)),
            severity=_map_severity(severity_str),
            message=result.get("extra", {}).get("message", ""),
            rule_id=result.get("check_id", ""),
        ))

    return findings


def parse_bandit_results(raw_json: str) -> list[Finding]:
    """Parse Bandit JSON output into findings.

    Args:
        raw_json: Raw JSON output from bandit.

    Returns:
        List of Finding objects.
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        LOG.warning("Failed to parse bandit results")
        return []

    findings = []
    for result in data.get("results", []):
        findings.append(Finding(
            tool="bandit",
            file_path=result.get("filename", ""),
            line=int(result.get("line_number", 0)),
            severity=_map_severity(result.get("issue_severity", "LOW")),
            message=result.get("issue_text", ""),
            rule_id=result.get("test_id", ""),
        ))

    return findings


def parse_trivy_results(raw_json: str) -> list[Finding]:
    """Parse Trivy JSON output into findings.

    Args:
        raw_json: Raw JSON output from trivy.

    Returns:
        List of Finding objects.
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        LOG.warning("Failed to parse trivy results")
        return []

    findings = []
    results = data.get("Results", [])
    for result in results:
        target = result.get("Target", "")
        for vuln in result.get("Vulnerabilities", []) or []:
            findings.append(Finding(
                tool="trivy",
                file_path=target,
                line=0,
                severity=_map_severity(vuln.get("Severity", "LOW")),
                message=(
                    f"{vuln.get('VulnerabilityID', '')}: "
                    f"{vuln.get('Title', '')}"
                ),
                rule_id=vuln.get("VulnerabilityID", ""),
            ))

    return findings


def parse_pip_audit_results(raw_json: str) -> list[Finding]:
    """Parse pip-audit JSON output into findings.

    Args:
        raw_json: Raw JSON output from pip-audit.

    Returns:
        List of Finding objects.
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        LOG.warning("Failed to parse pip-audit results")
        return []

    findings = []
    dependencies = data if isinstance(data, list) else data.get("dependencies", [])
    for dep in dependencies:
        for vuln in dep.get("vulns", []):
            findings.append(Finding(
                tool="pip-audit",
                file_path="requirements",
                line=0,
                severity=Severity.HIGH,
                message=(
                    f"{dep.get('name', '')} {dep.get('version', '')}: "
                    f"{vuln.get('id', '')} - {vuln.get('description', '')}"
                ),
                rule_id=vuln.get("id", ""),
            ))

    return findings


def evaluate_results(findings: list[Finding]) -> SastResult:
    """Evaluate findings and determine pass/fail.

    Fails if any HIGH or CRITICAL severity findings exist.

    Args:
        findings: List of all findings from all tools.

    Returns:
        An aggregated SastResult.
    """
    blocking_severities = {Severity.HIGH, Severity.CRITICAL}
    blocking = [
        f for f in findings if f.severity in blocking_severities
    ]

    passed = len(blocking) == 0
    total = len(findings)
    blocking_count = len(blocking)

    summary = (
        f"{total} finding(s): {blocking_count} blocking, "
        f"{total - blocking_count} non-blocking. "
        f"{'PASSED' if passed else 'FAILED'}"
    )

    return SastResult(
        findings=findings,
        passed=passed,
        summary=summary,
    )


def _map_severity(raw: str) -> Severity:
    """Map a raw severity string to the Severity enum.

    Args:
        raw: Raw severity string from various tools.

    Returns:
        A Severity enum value.
    """
    normalized = raw.upper().strip()
    mapping = {
        "CRITICAL": Severity.CRITICAL,
        "HIGH": Severity.HIGH,
        "MEDIUM": Severity.MEDIUM,
        "WARNING": Severity.MEDIUM,
        "LOW": Severity.LOW,
        "INFO": Severity.INFO,
        "NOTE": Severity.INFO,
        "ERROR": Severity.HIGH,
    }
    return mapping.get(normalized, Severity.INFO)
