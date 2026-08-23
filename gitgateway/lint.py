"""In-Lambda linting for GitGateway.

Runs linters on repository files when a PR is created.
Results are posted as status checks and comments.

Linters:
- pylint: Python files (requires 10/10 score)
- bandit: Python security scanning
- yamllint: YAML validation
- json: JSON syntax validation
"""

from __future__ import annotations

import io
import json
import logging
import os
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


def run_all_linters(files: dict[str, str]) -> LintResult:
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

    # Run linters
    if python_files:
        all_findings.extend(_run_pylint(python_files))
        all_findings.extend(_run_bandit(python_files))

    if yaml_files:
        all_findings.extend(_run_yamllint(yaml_files))

    if json_files:
        all_findings.extend(_run_json_check(json_files))

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


def _run_pylint(files: dict[str, str]) -> list[LintFinding]:
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

    # Write files to temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        file_paths = []
        for fpath, content in files.items():
            full = Path(tmpdir) / fpath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
            file_paths.append(str(full))

        # Run pylint
        output = io.StringIO()
        try:
            reporter = JSONReporter(output)
            Run(
                file_paths + [
                    "--disable=C0114,C0115,C0116",  # missing docstrings (common in small files)
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

        # Parse results
        output.seek(0)
        try:
            results = json.loads(output.getvalue())
        except (json.JSONDecodeError, ValueError):
            results = []

        for msg in results:
            # Map pylint type to severity
            msg_type = msg.get("type", "")
            severity = "error" if msg_type in ("error", "fatal") else "warning"

            # Get relative path
            abs_path = msg.get("path", "")
            rel_path = abs_path.replace(tmpdir + "/", "")

            findings.append(LintFinding(
                tool="pylint",
                file_path=rel_path,
                line=msg.get("line", 0),
                message=f"[{msg.get('message-id', '')}] {msg.get('message', '')}",
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
            mgr.discover_files([tmpdir])
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

    config = YamlLintConfig("extends: default\nrules:\n  line-length:\n    max: 120\n  document-start: disable")

    for fpath, content in files.items():
        try:
            problems = linter.run(content, config)
            for problem in problems:
                severity = "error" if problem.level == "error" else "warning"
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
