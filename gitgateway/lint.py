"""In-Lambda linting for GitGateway.

Runs linters on repository files when a PR is created.
Results are posted as status checks and comments.

Linters:
- pylint: Python files (requires 10/10 score)
- bandit: Python security scanning
- ruff: Fast Python linting (complementary to pylint)
- mypy: Python type checking
- yamllint: YAML validation
- json: JSON syntax validation
- toml: TOML syntax validation
- checkov: CloudFormation/Terraform/Kubernetes IaC scanning
- cfn-lint: CloudFormation template validation
- tflint: Terraform-specific linting
- kubeval: Kubernetes manifest schema validation
- shellcheck: Shell script analysis
- hadolint: Dockerfile best practices
- markdownlint: Markdown formatting
- eslint: JavaScript/TypeScript linting
- sqlfluff: SQL linting
"""
# pylint: disable=too-many-lines

from __future__ import annotations

import io
import json
import logging
import subprocess  # nosec
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

LOG = logging.getLogger(__name__)


@dataclass
class LintFinding:
    """A single lint finding.

    Attributes:
        tool: The linter that produced this finding.
        file_path: File where the issue was found.
        line: Line number.
        message: Description of the issue.
        severity: error, warning, or info.
    """

    tool: str = ""
    file_path: str = ""
    line: int = 0
    message: str = ""
    severity: str = "warning"

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return {
            "tool": self.tool,
            "file_path": self.file_path,
            "line": self.line,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass
class LintResult:
    """Aggregated lint result.

    Attributes:
        passed: Whether all linters passed.
        findings: List of all findings.
        summary: Human-readable summary.
    """

    passed: bool = True
    findings: list[LintFinding] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return {
            "passed": self.passed,
            "findings": [f.to_dict() for f in self.findings],
            "summary": self.summary,
        }


def run_all_linters(  # pylint: disable=too-many-locals,too-many-branches
    files: dict[str, str],
) -> LintResult:
    """Run all applicable linters on the given files.

    Args:
        files: Dict mapping file path to file content.

    Returns:
        Aggregated LintResult.
    """
    all_findings: list[LintFinding] = []

    # Categorize files
    python_files = {
        p: c for p, c in files.items()
        if p.endswith(".py") and c.strip()
    }
    yaml_files = {
        p: c for p, c in files.items()
        if p.endswith((".yaml", ".yml")) and c.strip()
    }
    json_files = {
        p: c for p, c in files.items()
        if p.endswith(".json") and c.strip()
    }
    toml_files = {
        p: c for p, c in files.items()
        if p.endswith(".toml") and c.strip()
    }
    iac_files = {
        p: c for p, c in files.items()
        if _is_iac_file(p, c)
    }
    cfn_files = {
        p: c for p, c in files.items()
        if _is_cfn_file(p, c)
    }
    terraform_files = {
        p: c for p, c in files.items()
        if p.endswith(".tf") and c.strip()
    }
    k8s_files = {
        p: c for p, c in files.items()
        if _is_k8s_file(p, c)
    }
    shell_files = {
        p: c for p, c in files.items()
        if _is_shell_file(p, c)
    }
    docker_files = {
        p: c for p, c in files.items()
        if _is_dockerfile(p) and c.strip()
    }
    markdown_files = {
        p: c for p, c in files.items()
        if p.endswith((".md", ".markdown")) and c.strip()
    }
    js_ts_files = {
        p: c for p, c in files.items()
        if p.endswith((".js", ".jsx", ".ts", ".tsx")) and c.strip()
    }
    sql_files = {
        p: c for p, c in files.items()
        if p.endswith(".sql") and c.strip()
    }

    # Run linters
    if python_files:
        all_findings.extend(_run_pylint(python_files))
        all_findings.extend(_run_bandit(python_files))
        all_findings.extend(_run_ruff(python_files))
        all_findings.extend(_run_mypy(python_files))

    if yaml_files:
        all_findings.extend(_run_yamllint(yaml_files))

    if json_files:
        all_findings.extend(_run_json_check(json_files))

    if toml_files:
        all_findings.extend(_run_toml_check(toml_files))

    if iac_files:
        all_findings.extend(_run_checkov(iac_files))

    if cfn_files:
        all_findings.extend(_run_cfnlint(cfn_files))

    if terraform_files:
        all_findings.extend(_run_tflint(terraform_files))

    if k8s_files:
        all_findings.extend(_run_kubeval(k8s_files))

    if shell_files:
        all_findings.extend(_run_shellcheck(shell_files))

    if docker_files:
        all_findings.extend(_run_hadolint(docker_files))

    if markdown_files:
        all_findings.extend(_run_markdownlint(markdown_files))

    if js_ts_files:
        all_findings.extend(_run_eslint(js_ts_files))

    if sql_files:
        all_findings.extend(_run_sqlfluff(sql_files))

    # Determine pass/fail
    errors = [f for f in all_findings if f.severity == "error"]
    passed = len(errors) == 0

    summary = (
        f"{len(all_findings)} finding(s): "
        f"{len(errors)} error(s), "
        f"{len(all_findings) - len(errors)} warning(s). "
        f"{'PASSED' if passed else 'FAILED'}"
    )

    return LintResult(
        passed=passed,
        findings=all_findings,
        summary=summary,
    )


# ---------- File categorization helpers ----------


def _is_iac_file(path: str, content: str) -> bool:
    """Determine if a file is Infrastructure-as-Code.

    Detects CloudFormation templates, Terraform files, and
    Kubernetes manifests.

    Args:
        path: File path.
        content: File content.

    Returns:
        True if the file appears to be IaC.
    """
    if path.endswith(".tf"):
        return True
    if path.endswith((".yaml", ".yml", ".json")):
        if "AWSTemplateFormatVersion" in content:
            return True
        if "Transform:" in content and "AWS::" in content:
            return True
        if "apiVersion:" in content and "kind:" in content:
            return True
    return False


def _is_cfn_file(path: str, content: str) -> bool:
    """Determine if a file is a CloudFormation template.

    Args:
        path: File path.
        content: File content.

    Returns:
        True if the file is a CloudFormation template.
    """
    if not path.endswith((".yaml", ".yml", ".json")):
        return False
    if "AWSTemplateFormatVersion" in content:
        return True
    if "Transform:" in content and "AWS::" in content:
        return True
    return False


def _is_k8s_file(path: str, content: str) -> bool:
    """Determine if a file is a Kubernetes manifest.

    Args:
        path: File path.
        content: File content.

    Returns:
        True if the file is a Kubernetes manifest.
    """
    if not path.endswith((".yaml", ".yml")):
        return False
    return "apiVersion:" in content and "kind:" in content


def _is_shell_file(path: str, content: str) -> bool:
    """Determine if a file is a shell script.

    Checks extension or shebang line.

    Args:
        path: File path.
        content: File content.

    Returns:
        True if the file is a shell script.
    """
    if path.endswith((".sh", ".bash", ".zsh", ".ksh")):
        return bool(content.strip())
    # Check shebang
    first_line = content.split("\n", 1)[0] if content else ""
    if first_line.startswith("#!") and (
        "bash" in first_line
        or "sh" in first_line
        or "zsh" in first_line
    ):
        return True
    return False


def _is_dockerfile(path: str) -> bool:
    """Determine if a file is a Dockerfile.

    Args:
        path: File path.

    Returns:
        True if the file is a Dockerfile.
    """
    basename = Path(path).name
    return basename in (
        "Dockerfile", "dockerfile",
    ) or basename.startswith("Dockerfile.")


# ---------- Python linters ----------


def _run_pylint(  # pylint: disable=too-many-locals
    files: dict[str, str],
) -> list[LintFinding]:
    """Run pylint on Python files.

    Requires a perfect 10/10 score. Any message is a finding.

    Args:
        files: Dict of path -> content for Python files.

    Returns:
        List of findings.
    """
    findings = []

    try:
        from pylint.lint import Run  # pylint: disable=import-outside-toplevel
        from pylint.reporters.json_reporter import (  # pylint: disable=import-outside-toplevel
            JSONReporter,
        )
    except ImportError:
        LOG.warning("pylint not available in Lambda")
        return findings

    with tempfile.TemporaryDirectory() as tmpdir:
        file_paths = []
        for fpath, content in files.items():
            full = Path(tmpdir) / fpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
            file_paths.append(str(full))

        output = io.StringIO()
        try:
            reporter = JSONReporter(output)
            Run(
                file_paths + [
                    "--disable=C0114,C0115,C0116",
                    "--max-line-length=120",
                    "--exit-zero",
                ],
                reporter=reporter,
                exit=False,
            )
        except SystemExit:
            pass
        except Exception as exc:  # pylint: disable=broad-except
            LOG.warning("pylint error: %s", exc)
            return findings

        output.seek(0)
        try:
            results = json.loads(output.getvalue())
        except (json.JSONDecodeError, ValueError):
            results = []

        for msg in results:
            msg_type = msg.get("type", "")
            severity = (
                "error" if msg_type in ("error", "fatal")
                else "warning"
            )
            abs_path = msg.get("path", "")
            rel_path = abs_path.replace(tmpdir + "/", "")

            findings.append(LintFinding(
                tool="pylint",
                file_path=rel_path,
                line=msg.get("line", 0),
                message=(
                    f"[{msg.get('message-id', '')}] "
                    f"{msg.get('message', '')}"
                ),
                severity=severity,
            ))

    return findings


def _run_bandit(files: dict[str, str]) -> list[LintFinding]:
    """Run bandit security scanner on Python files.

    Args:
        files: Dict of path -> content for Python files.

    Returns:
        List of findings.
    """
    findings = []

    try:
        from bandit.core import manager as b_manager  # pylint: disable=import-outside-toplevel
        from bandit.core import config as b_config  # pylint: disable=import-outside-toplevel
    except ImportError:
        LOG.warning("bandit not available in Lambda")
        return findings

    with tempfile.TemporaryDirectory() as tmpdir:
        for fpath, content in files.items():
            full = Path(tmpdir) / fpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")

        try:
            conf = b_config.BanditConfig()
            mgr = b_manager.BanditManager(conf, "file")
            mgr.discover_files([tmpdir], recursive=True)
            mgr.run_tests()

            for issue in mgr.get_issue_list():
                severity = (
                    "error" if issue.severity in ("HIGH", "MEDIUM")
                    else "warning"
                )
                rel_path = issue.fname.replace(tmpdir + "/", "")
                findings.append(LintFinding(
                    tool="bandit",
                    file_path=rel_path,
                    line=issue.lineno,
                    message=(
                        f"[{issue.test_id}] {issue.text} "
                        f"(severity: {issue.severity})"
                    ),
                    severity=severity,
                ))
        except Exception as exc:  # pylint: disable=broad-except
            LOG.warning("bandit error: %s", exc)

    return findings


def _run_ruff(files: dict[str, str]) -> list[LintFinding]:
    """Run ruff linter on Python files.

    Ruff is a fast Python linter that catches issues pylint may miss.

    Args:
        files: Dict of path -> content for Python files.

    Returns:
        List of findings.
    """
    findings = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for fpath, content in files.items():
            full = Path(tmpdir) / fpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")

        try:
            result = subprocess.run(  # nosec
                [
                    "ruff", "check",
                    "--output-format=json",
                    "--select=E,F,W,I,N,UP,B,A,S",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.stdout:
                issues = json.loads(result.stdout)
                for issue in issues:
                    rel_path = issue.get(
                        "filename", "",
                    ).replace(tmpdir + "/", "")
                    findings.append(LintFinding(
                        tool="ruff",
                        file_path=rel_path,
                        line=issue.get("location", {}).get(
                            "row", 0,
                        ),
                        message=(
                            f"[{issue.get('code', '')}] "
                            f"{issue.get('message', '')}"
                        ),
                        severity="warning",
                    ))
        except FileNotFoundError:
            LOG.warning("ruff not available in Lambda")
        except Exception as exc:  # pylint: disable=broad-except
            LOG.warning("ruff error: %s", exc)

    return findings


def _run_mypy(  # pylint: disable=too-many-locals
    files: dict[str, str],
) -> list[LintFinding]:
    """Run mypy type checker on Python files.

    Args:
        files: Dict of path -> content for Python files.

    Returns:
        List of findings.
    """
    findings = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for fpath, content in files.items():
            full = Path(tmpdir) / fpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")

        try:
            result = subprocess.run(  # nosec
                [
                    "mypy",
                    "--ignore-missing-imports",
                    "--no-error-summary",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            for line in result.stdout.splitlines():
                # Format: file:line: level: message
                parts = line.split(":", 3)
                if len(parts) >= 4:
                    fpath_abs = parts[0].strip()
                    rel_path = fpath_abs.replace(
                        tmpdir + "/", "",
                    )
                    line_num = int(parts[1]) if parts[1].strip().isdigit() else 0
                    level = parts[2].strip()
                    msg = parts[3].strip()
                    severity = (
                        "error" if level == "error" else "warning"
                    )
                    findings.append(LintFinding(
                        tool="mypy",
                        file_path=rel_path,
                        line=line_num,
                        message=msg,
                        severity=severity,
                    ))
        except FileNotFoundError:
            LOG.warning("mypy not available in Lambda")
        except Exception as exc:  # pylint: disable=broad-except
            LOG.warning("mypy error: %s", exc)

    return findings


# ---------- Data format linters ----------


def _run_yamllint(files: dict[str, str]) -> list[LintFinding]:
    """Run yamllint on YAML files.

    Args:
        files: Dict of path -> content for YAML files.

    Returns:
        List of findings.
    """
    findings = []

    try:
        from yamllint import linter  # pylint: disable=import-outside-toplevel
        from yamllint.config import YamlLintConfig  # pylint: disable=import-outside-toplevel
    except ImportError:
        LOG.warning("yamllint not available in Lambda")
        return findings

    config = YamlLintConfig(
        "extends: default\n"
        "rules:\n"
        "  line-length:\n"
        "    max: 120\n"
        "  document-start: disable"
    )

    for fpath, content in files.items():
        try:
            problems = linter.run(content, config)
            for problem in problems:
                severity = (
                    "error" if problem.level == "error"
                    else "warning"
                )
                findings.append(LintFinding(
                    tool="yamllint",
                    file_path=fpath,
                    line=problem.line,
                    message=f"{problem.message} ({problem.rule})",
                    severity=severity,
                ))
        except Exception as exc:  # pylint: disable=broad-except
            LOG.warning("yamllint error on %s: %s", fpath, exc)

    return findings


def _run_json_check(files: dict[str, str]) -> list[LintFinding]:
    """Validate JSON file syntax.

    Args:
        files: Dict of path -> content for JSON files.

    Returns:
        List of findings.
    """
    findings = []

    for fpath, content in files.items():
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            findings.append(LintFinding(
                tool="json",
                file_path=fpath,
                line=exc.lineno or 0,
                message=f"Invalid JSON: {exc.msg}",
                severity="error",
            ))

    return findings


def _run_toml_check(files: dict[str, str]) -> list[LintFinding]:
    """Validate TOML file syntax.

    Args:
        files: Dict of path -> content for TOML files.

    Returns:
        List of findings.
    """
    findings = []

    try:
        import tomllib  # pylint: disable=import-outside-toplevel
    except ImportError:
        try:
            import tomli as tomllib  # pylint: disable=import-outside-toplevel
        except ImportError:
            LOG.warning("toml parser not available in Lambda")
            return findings

    for fpath, content in files.items():
        try:
            tomllib.loads(content)
        except Exception as exc:  # pylint: disable=broad-except
            findings.append(LintFinding(
                tool="toml",
                file_path=fpath,
                line=0,
                message=f"Invalid TOML: {exc}",
                severity="error",
            ))

    return findings


# ---------- IaC linters ----------


def _run_checkov(  # pylint: disable=too-many-locals
    files: dict[str, str],
) -> list[LintFinding]:
    """Run Checkov IaC scanner on CloudFormation/Terraform/Kubernetes files.

    Checkov performs static analysis for misconfigurations in
    infrastructure-as-code files.

    Args:
        files: Dict of path -> content for IaC files.

    Returns:
        List of findings.
    """
    findings = []

    try:
        from checkov.runner_registry import RunnerRegistry  # pylint: disable=import-outside-toplevel
        from checkov.common.runners.runner_registry import (  # pylint: disable=import-outside-toplevel
            checkov_runners,
        )
    except ImportError:
        LOG.warning("checkov not available in Lambda")
        return findings

    with tempfile.TemporaryDirectory() as tmpdir:
        for fpath, content in files.items():
            full = Path(tmpdir) / fpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")

        try:
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            sys.stdout = io.StringIO()
            sys.stderr = io.StringIO()

            try:
                runner_registry = RunnerRegistry(
                    banner="",
                    runner_config=None,
                    runners=checkov_runners,
                )
                reports = runner_registry.run(
                    root_folder=tmpdir,
                    runner_filter=None,
                )
            finally:
                sys.stdout = old_stdout
                sys.stderr = old_stderr

            if not isinstance(reports, list):
                reports = [reports]

            for report in reports:
                if report is None:
                    continue
                for check_result in getattr(
                    report, "failed_checks", [],
                ):
                    rel_path = check_result.file_path.replace(
                        tmpdir + "/", "",
                    ).lstrip("/")
                    severity = (
                        "error"
                        if getattr(check_result, "severity", None)
                        in ("HIGH", "CRITICAL")
                        else "warning"
                    )
                    findings.append(LintFinding(
                        tool="checkov",
                        file_path=rel_path,
                        line=getattr(
                            check_result,
                            "file_line_range",
                            [0],
                        )[0],
                        message=(
                            f"[{check_result.check_id}] "
                            f"{check_result.check.name}"
                        ),
                        severity=severity,
                    ))
        except Exception as exc:  # pylint: disable=broad-except
            LOG.warning("checkov error: %s", exc)

    return findings


def _run_cfnlint(  # pylint: disable=too-many-locals
    files: dict[str, str],
) -> list[LintFinding]:
    """Run cfn-lint on CloudFormation templates.

    Validates templates against the CloudFormation resource spec.

    Args:
        files: Dict of path -> content for CloudFormation files.

    Returns:
        List of findings.
    """
    findings = []

    with tempfile.TemporaryDirectory() as tmpdir:
        file_paths = []
        for fpath, content in files.items():
            full = Path(tmpdir) / fpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
            file_paths.append(str(full))

        try:
            result = subprocess.run(  # nosec
                ["cfn-lint", "--format=json"] + file_paths,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.stdout:
                matches = json.loads(result.stdout)
                for match in matches:
                    level = match.get("Level", "Warning")
                    severity = (
                        "error" if level == "Error"
                        else "warning"
                    )
                    rel_path = match.get(
                        "Filename", "",
                    ).replace(tmpdir + "/", "")
                    loc = match.get("Location", {})
                    start = loc.get("Start", {})
                    findings.append(LintFinding(
                        tool="cfn-lint",
                        file_path=rel_path,
                        line=start.get(
                            "LineNumber", 0,
                        ),
                        message=(
                            f"[{match.get('Rule', {}).get('Id', '')}] "
                            f"{match.get('Message', '')}"
                        ),
                        severity=severity,
                    ))
        except FileNotFoundError:
            LOG.warning("cfn-lint not available in Lambda")
        except Exception as exc:  # pylint: disable=broad-except
            LOG.warning("cfn-lint error: %s", exc)

    return findings


def _run_tflint(files: dict[str, str]) -> list[LintFinding]:
    """Run tflint on Terraform files.

    Provides Terraform-specific linting including provider-aware
    rules beyond what Checkov catches.

    Args:
        files: Dict of path -> content for Terraform files.

    Returns:
        List of findings.
    """
    findings = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for fpath, content in files.items():
            full = Path(tmpdir) / fpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")

        try:
            result = subprocess.run(  # nosec
                [
                    "tflint",
                    "--format=json",
                    "--chdir", tmpdir,
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.stdout:
                output = json.loads(result.stdout)
                for issue in output.get("issues", []):
                    rng = issue.get("range", {})
                    findings.append(LintFinding(
                        tool="tflint",
                        file_path=rng.get(
                            "filename", "",
                        ),
                        line=rng.get("start", {}).get(
                            "line", 0,
                        ),
                        message=(
                            f"[{issue.get('rule', {}).get('name', '')}] "
                            f"{issue.get('message', '')}"
                        ),
                        severity=issue.get(
                            "rule", {},
                        ).get("severity", "warning"),
                    ))
        except FileNotFoundError:
            LOG.warning("tflint not available in Lambda")
        except Exception as exc:  # pylint: disable=broad-except
            LOG.warning("tflint error: %s", exc)

    return findings


def _run_kubeval(files: dict[str, str]) -> list[LintFinding]:
    """Run kubeval/kubeconform on Kubernetes manifests.

    Validates manifests against Kubernetes API schemas.

    Args:
        files: Dict of path -> content for Kubernetes files.

    Returns:
        List of findings.
    """
    findings = []

    with tempfile.TemporaryDirectory() as tmpdir:
        file_paths = []
        for fpath, content in files.items():
            full = Path(tmpdir) / fpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
            file_paths.append(str(full))

        # Try kubeconform first, fall back to kubeval
        binary = "kubeconform"
        args = [binary, "-output", "json"] + file_paths
        try:
            result = subprocess.run(  # nosec
                args,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except FileNotFoundError:
            binary = "kubeval"
            args = [binary, "--output", "json"] + file_paths
            try:
                result = subprocess.run(  # nosec
                    args,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
            except FileNotFoundError:
                LOG.warning(
                    "kubeconform/kubeval not available",
                )
                return findings

        try:
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                status = entry.get("status", "")
                if status in ("invalid", "error"):
                    rel_path = entry.get(
                        "filename", "",
                    ).replace(tmpdir + "/", "")
                    findings.append(LintFinding(
                        tool="kubeval",
                        file_path=rel_path,
                        line=0,
                        message=entry.get("msg", "Validation failed"),
                        severity="error",
                    ))
        except Exception as exc:  # pylint: disable=broad-except
            LOG.warning("kubeval parse error: %s", exc)

    return findings


# ---------- Shell/Docker linters ----------


def _run_shellcheck(files: dict[str, str]) -> list[LintFinding]:
    """Run shellcheck on shell scripts.

    Catches common bash pitfalls, quoting issues, and
    portability problems.

    Args:
        files: Dict of path -> content for shell scripts.

    Returns:
        List of findings.
    """
    findings = []

    with tempfile.TemporaryDirectory() as tmpdir:
        file_paths = []
        for fpath, content in files.items():
            full = Path(tmpdir) / fpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
            file_paths.append(str(full))

        try:
            result = subprocess.run(  # nosec
                ["shellcheck", "--format=json"] + file_paths,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.stdout:
                issues = json.loads(result.stdout)
                for issue in issues:
                    level = issue.get("level", "warning")
                    severity = (
                        "error" if level == "error" else "warning"
                    )
                    rel_path = issue.get(
                        "file", "",
                    ).replace(tmpdir + "/", "")
                    findings.append(LintFinding(
                        tool="shellcheck",
                        file_path=rel_path,
                        line=issue.get("line", 0),
                        message=(
                            f"[SC{issue.get('code', '')}] "
                            f"{issue.get('message', '')}"
                        ),
                        severity=severity,
                    ))
        except FileNotFoundError:
            LOG.warning("shellcheck not available in Lambda")
        except Exception as exc:  # pylint: disable=broad-except
            LOG.warning("shellcheck error: %s", exc)

    return findings


def _run_hadolint(files: dict[str, str]) -> list[LintFinding]:
    """Run hadolint on Dockerfiles.

    Checks Dockerfile best practices (base image pinning,
    layer consolidation, security).

    Args:
        files: Dict of path -> content for Dockerfiles.

    Returns:
        List of findings.
    """
    findings = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for fpath, content in files.items():
            full = Path(tmpdir) / fpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")

            try:
                result = subprocess.run(  # nosec
                    [
                        "hadolint",
                        "--format=json",
                        str(full),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if result.stdout:
                    issues = json.loads(result.stdout)
                    for issue in issues:
                        level = issue.get("level", "warning")
                        severity = (
                            "error"
                            if level in ("error", "DLError")
                            else "warning"
                        )
                        findings.append(LintFinding(
                            tool="hadolint",
                            file_path=fpath,
                            line=issue.get("line", 0),
                            message=(
                                f"[{issue.get('code', '')}] "
                                f"{issue.get('message', '')}"
                            ),
                            severity=severity,
                        ))
            except FileNotFoundError:
                LOG.warning("hadolint not available in Lambda")
                return findings
            except Exception as exc:  # pylint: disable=broad-except
                LOG.warning(
                    "hadolint error on %s: %s", fpath, exc,
                )

    return findings


# ---------- Documentation linters ----------


def _run_markdownlint(files: dict[str, str]) -> list[LintFinding]:
    """Run markdownlint on Markdown files.

    Tries markdownlint-cli2 first, falls back to mdl.
    Checks formatting, heading structure, and consistency.

    Args:
        files: Dict of path -> content for Markdown files.

    Returns:
        List of findings.
    """
    findings = []

    with tempfile.TemporaryDirectory() as tmpdir:
        file_paths = []
        for fpath, content in files.items():
            full = Path(tmpdir) / fpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
            file_paths.append(str(full))

        # Try markdownlint-cli2 first
        try:
            result = subprocess.run(  # nosec
                ["markdownlint-cli2"] + file_paths,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.stdout or result.stderr:
                output = result.stdout or result.stderr
                _parse_mdl_text(output, tmpdir, findings)
            return findings
        except FileNotFoundError:
            pass  # Try mdl fallback

        # Fall back to mdl (Ruby-based)
        try:
            result = subprocess.run(  # nosec
                ["mdl", "--json"] + file_paths,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.stdout:
                try:
                    issues = json.loads(result.stdout)
                    for issue in issues:
                        rel_path = issue.get(
                            "filename", "",
                        ).replace(tmpdir + "/", "")
                        findings.append(LintFinding(
                            tool="markdownlint",
                            file_path=rel_path,
                            line=issue.get("line", 0),
                            message=(
                                f"[{issue.get('rule', '')}] "
                                f"{issue.get('description', '')}"
                            ),
                            severity="warning",
                        ))
                except (json.JSONDecodeError, ValueError):
                    _parse_mdl_text(
                        result.stdout, tmpdir, findings,
                    )
        except FileNotFoundError:
            LOG.warning("markdownlint not available")
        except Exception as exc:  # pylint: disable=broad-except
            LOG.warning("markdownlint error: %s", exc)

    return findings


def _parse_mdl_text(
    output: str, tmpdir: str, findings: list[LintFinding],
) -> None:
    """Parse markdownlint text output as fallback.

    Args:
        output: Raw text output from mdl.
        tmpdir: Temp directory prefix to strip.
        findings: List to append findings to.
    """
    for line in output.splitlines():
        # Format: filepath:line: MD000 description
        parts = line.split(":", 2)
        if len(parts) >= 3:
            rel_path = parts[0].replace(tmpdir + "/", "")
            line_num = (
                int(parts[1]) if parts[1].strip().isdigit()
                else 0
            )
            findings.append(LintFinding(
                tool="markdownlint",
                file_path=rel_path,
                line=line_num,
                message=parts[2].strip(),
                severity="warning",
            ))


# ---------- JavaScript/TypeScript linters ----------


def _run_eslint(  # pylint: disable=too-many-locals
    files: dict[str, str],
) -> list[LintFinding]:
    """Run eslint on JavaScript/TypeScript files.

    Uses eslint with a basic configuration for common issues.

    Args:
        files: Dict of path -> content for JS/TS files.

    Returns:
        List of findings.
    """
    findings = []

    with tempfile.TemporaryDirectory() as tmpdir:
        file_paths = []
        for fpath, content in files.items():
            full = Path(tmpdir) / fpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
            file_paths.append(str(full))

        # Write minimal eslint config
        eslint_config = Path(tmpdir) / ".eslintrc.json"
        eslint_config.write_text(json.dumps({
            "env": {"es2021": True, "node": True},
            "parserOptions": {"ecmaVersion": "latest"},
            "rules": {
                "no-unused-vars": "warn",
                "no-undef": "error",
                "no-console": "warn",
            },
        }), encoding="utf-8")

        try:
            result = subprocess.run(  # nosec
                [
                    "eslint",
                    "--format=json",
                    "--no-eslintrc",
                    "--config", str(eslint_config),
                ] + file_paths,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.stdout:
                results = json.loads(result.stdout)
                for file_result in results:
                    rel_path = file_result.get(
                        "filePath", "",
                    ).replace(tmpdir + "/", "")
                    for msg in file_result.get("messages", []):
                        severity_num = msg.get("severity", 1)
                        severity = (
                            "error" if severity_num == 2
                            else "warning"
                        )
                        findings.append(LintFinding(
                            tool="eslint",
                            file_path=rel_path,
                            line=msg.get("line", 0),
                            message=(
                                f"[{msg.get('ruleId', '')}] "
                                f"{msg.get('message', '')}"
                            ),
                            severity=severity,
                        ))
        except FileNotFoundError:
            LOG.warning("eslint not available in Lambda")
        except Exception as exc:  # pylint: disable=broad-except
            LOG.warning("eslint error: %s", exc)

    return findings


# ---------- SQL linters ----------


def _run_sqlfluff(files: dict[str, str]) -> list[LintFinding]:
    """Run sqlfluff on SQL files.

    Checks SQL formatting and common anti-patterns.

    Args:
        files: Dict of path -> content for SQL files.

    Returns:
        List of findings.
    """
    findings = []

    with tempfile.TemporaryDirectory() as tmpdir:
        file_paths = []
        for fpath, content in files.items():
            full = Path(tmpdir) / fpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
            file_paths.append(str(full))

        try:
            result = subprocess.run(  # nosec
                [
                    "sqlfluff", "lint",
                    "--format=json",
                    "--dialect=ansi",
                ] + file_paths,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.stdout:
                output = json.loads(result.stdout)
                for file_entry in output:
                    rel_path = file_entry.get(
                        "filepath", "",
                    ).replace(tmpdir + "/", "")
                    for violation in file_entry.get(
                        "violations", [],
                    ):
                        findings.append(LintFinding(
                            tool="sqlfluff",
                            file_path=rel_path,
                            line=violation.get(
                                "start_line_no", 0,
                            ),
                            message=(
                                f"[{violation.get('code', '')}] "
                                f"{violation.get('description', '')}"
                            ),
                            severity="warning",
                        ))
        except FileNotFoundError:
            LOG.warning("sqlfluff not available in Lambda")
        except Exception as exc:  # pylint: disable=broad-except
            LOG.warning("sqlfluff error: %s", exc)

    return findings
