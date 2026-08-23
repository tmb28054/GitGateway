"""Tests for gitgateway.sast module."""

from __future__ import annotations

import json

from gitgateway.sast import (
    Finding,
    SastResult,
    Severity,
    _map_severity,
    _tool_command,
    evaluate_results,
    generate_buildspec,
    parse_bandit_results,
    parse_pip_audit_results,
    parse_semgrep_results,
    parse_trivy_results,
)


class TestFinding:
    """Tests for Finding dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dictionary."""
        f = Finding(
            tool="bandit", file_path="app.py", line=10,
            severity=Severity.HIGH, message="Hardcoded password",
            rule_id="B105",
        )
        result = f.to_dict()
        assert result["tool"] == "bandit"
        assert result["severity"] == "high"

    def test_from_dict(self) -> None:
        """Deserializes from dictionary."""
        data = {
            "tool": "semgrep", "file_path": "x.py",
            "line": 5, "severity": "critical",
            "message": "SQL injection", "rule_id": "sql-inject",
        }
        f = Finding.from_dict(data)
        assert f.severity == Severity.CRITICAL
        assert f.rule_id == "sql-inject"

    def test_from_dict_defaults(self) -> None:
        """Handles missing keys."""
        f = Finding.from_dict({})
        assert f.tool == ""
        assert f.severity == Severity.INFO


class TestSastResult:
    """Tests for SastResult dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dictionary."""
        result = SastResult(
            findings=[
                Finding(
                    tool="bandit", file_path="x.py", line=1,
                    severity=Severity.LOW, message="test",
                ),
            ],
            passed=True,
            summary="1 finding, 0 blocking. PASSED",
        )
        d = result.to_dict()
        assert d["passed"] is True
        assert len(d["findings"]) == 1


class TestGenerateBuildspec:
    """Tests for generate_buildspec function."""

    def test_default_tools(self) -> None:
        """Generates buildspec with all default tools."""
        spec = generate_buildspec()
        assert "version: 0.2" in spec
        assert "semgrep" in spec
        assert "bandit" in spec
        assert "pip-audit" in spec
        assert "trivy" in spec
        assert "checkov" in spec
        assert "yamllint" in spec
        assert "check-jsonschema" in spec
        assert "sast-results.json" in spec

    def test_custom_tools(self) -> None:
        """Generates buildspec with only specified tools."""
        spec = generate_buildspec(["bandit", "semgrep"])
        assert "bandit -r" in spec
        assert "semgrep scan" in spec
        # checkov command should not appear in build commands
        assert "checkov -d" not in spec

    def test_unknown_tool_skipped(self) -> None:
        """Unknown tools produce no command."""
        spec = generate_buildspec(["bandit", "unknowntool"])
        assert "bandit" in spec
        assert "unknowntool" not in spec


class TestToolCommand:
    """Tests for _tool_command function."""

    def test_known_tools(self) -> None:
        """Returns commands for all known tools."""
        for tool in [
            "semgrep", "bandit", "pip-audit",
            "trivy", "checkov", "yamllint", "check-jsonschema",
        ]:
            assert _tool_command(tool) != ""

    def test_unknown_tool(self) -> None:
        """Returns empty string for unknown tool."""
        assert _tool_command("unknown") == ""


class TestParseSemgrepResults:
    """Tests for parse_semgrep_results function."""

    def test_parses_results(self) -> None:
        """Parses valid semgrep JSON output."""
        data = {
            "results": [
                {
                    "check_id": "python.security.sqli",
                    "path": "app.py",
                    "start": {"line": 15},
                    "extra": {
                        "severity": "ERROR",
                        "message": "SQL injection risk",
                    },
                },
                {
                    "check_id": "python.lang.unused",
                    "path": "utils.py",
                    "start": {"line": 3},
                    "extra": {
                        "severity": "WARNING",
                        "message": "Unused variable",
                    },
                },
            ],
        }
        findings = parse_semgrep_results(json.dumps(data))
        assert len(findings) == 2
        assert findings[0].tool == "semgrep"
        assert findings[0].severity == Severity.HIGH
        assert findings[0].line == 15
        assert findings[1].severity == Severity.MEDIUM

    def test_empty_results(self) -> None:
        """Returns empty list for no results."""
        findings = parse_semgrep_results(json.dumps({"results": []}))
        assert findings == []

    def test_invalid_json(self) -> None:
        """Returns empty list for invalid JSON."""
        findings = parse_semgrep_results("not json")
        assert findings == []


class TestParseBanditResults:
    """Tests for parse_bandit_results function."""

    def test_parses_results(self) -> None:
        """Parses valid bandit JSON output."""
        data = {
            "results": [
                {
                    "filename": "config.py",
                    "line_number": 22,
                    "issue_severity": "HIGH",
                    "issue_text": "Hardcoded password",
                    "test_id": "B105",
                },
            ],
        }
        findings = parse_bandit_results(json.dumps(data))
        assert len(findings) == 1
        assert findings[0].tool == "bandit"
        assert findings[0].severity == Severity.HIGH
        assert findings[0].rule_id == "B105"

    def test_empty_results(self) -> None:
        """Returns empty list for no results."""
        findings = parse_bandit_results(json.dumps({"results": []}))
        assert findings == []

    def test_invalid_json(self) -> None:
        """Returns empty list for invalid JSON."""
        findings = parse_bandit_results("broken {{{")
        assert findings == []


class TestParseTrivyResults:
    """Tests for parse_trivy_results function."""

    def test_parses_results(self) -> None:
        """Parses valid trivy JSON output."""
        data = {
            "Results": [
                {
                    "Target": "requirements.txt",
                    "Vulnerabilities": [
                        {
                            "VulnerabilityID": "CVE-2023-1234",
                            "Severity": "CRITICAL",
                            "Title": "Remote code execution",
                        },
                    ],
                },
            ],
        }
        findings = parse_trivy_results(json.dumps(data))
        assert len(findings) == 1
        assert findings[0].tool == "trivy"
        assert findings[0].severity == Severity.CRITICAL
        assert "CVE-2023-1234" in findings[0].message

    def test_no_vulnerabilities(self) -> None:
        """Returns empty list when no vulns found."""
        data = {
            "Results": [{"Target": "x.txt", "Vulnerabilities": None}],
        }
        # Trivy may return None for Vulnerabilities
        findings = parse_trivy_results(json.dumps(data))
        assert findings == []

    def test_invalid_json(self) -> None:
        """Returns empty list for invalid JSON."""
        findings = parse_trivy_results("!")
        assert findings == []


class TestParsePipAuditResults:
    """Tests for parse_pip_audit_results function."""

    def test_parses_list_format(self) -> None:
        """Parses pip-audit list format."""
        data = [
            {
                "name": "requests",
                "version": "2.25.0",
                "vulns": [
                    {
                        "id": "PYSEC-2023-001",
                        "description": "SSRF vulnerability",
                    },
                ],
            },
        ]
        findings = parse_pip_audit_results(json.dumps(data))
        assert len(findings) == 1
        assert findings[0].tool == "pip-audit"
        assert findings[0].severity == Severity.HIGH
        assert "requests" in findings[0].message

    def test_parses_dict_format(self) -> None:
        """Parses pip-audit dict format with dependencies key."""
        data = {
            "dependencies": [
                {
                    "name": "flask",
                    "version": "1.0",
                    "vulns": [{"id": "CVE-X", "description": "XSS"}],
                },
            ],
        }
        findings = parse_pip_audit_results(json.dumps(data))
        assert len(findings) == 1

    def test_no_vulns(self) -> None:
        """Returns empty list when packages have no vulns."""
        data = [{"name": "safe-pkg", "version": "1.0", "vulns": []}]
        findings = parse_pip_audit_results(json.dumps(data))
        assert findings == []

    def test_invalid_json(self) -> None:
        """Returns empty list for invalid JSON."""
        findings = parse_pip_audit_results("{bad")
        assert findings == []


class TestEvaluateResults:
    """Tests for evaluate_results function."""

    def test_passes_with_low_findings(self) -> None:
        """Passes when only low severity findings."""
        findings = [
            Finding(
                tool="x", file_path="y", line=1,
                severity=Severity.LOW, message="minor",
            ),
            Finding(
                tool="x", file_path="y", line=2,
                severity=Severity.MEDIUM, message="moderate",
            ),
        ]
        result = evaluate_results(findings)
        assert result.passed is True
        assert "PASSED" in result.summary
        assert "2 finding(s)" in result.summary

    def test_fails_with_high_finding(self) -> None:
        """Fails when a high severity finding exists."""
        findings = [
            Finding(
                tool="x", file_path="y", line=1,
                severity=Severity.HIGH, message="serious",
            ),
        ]
        result = evaluate_results(findings)
        assert result.passed is False
        assert "FAILED" in result.summary

    def test_fails_with_critical_finding(self) -> None:
        """Fails when a critical severity finding exists."""
        findings = [
            Finding(
                tool="x", file_path="y", line=1,
                severity=Severity.CRITICAL, message="critical",
            ),
        ]
        result = evaluate_results(findings)
        assert result.passed is False

    def test_passes_with_no_findings(self) -> None:
        """Passes when there are no findings."""
        result = evaluate_results([])
        assert result.passed is True
        assert "0 finding(s)" in result.summary


class TestMapSeverity:
    """Tests for _map_severity function."""

    def test_critical(self) -> None:
        """Maps CRITICAL."""
        assert _map_severity("CRITICAL") == Severity.CRITICAL

    def test_high(self) -> None:
        """Maps HIGH."""
        assert _map_severity("HIGH") == Severity.HIGH

    def test_medium(self) -> None:
        """Maps MEDIUM."""
        assert _map_severity("MEDIUM") == Severity.MEDIUM

    def test_warning(self) -> None:
        """Maps WARNING to MEDIUM."""
        assert _map_severity("WARNING") == Severity.MEDIUM

    def test_low(self) -> None:
        """Maps LOW."""
        assert _map_severity("LOW") == Severity.LOW

    def test_info(self) -> None:
        """Maps INFO."""
        assert _map_severity("INFO") == Severity.INFO

    def test_note(self) -> None:
        """Maps NOTE to INFO."""
        assert _map_severity("NOTE") == Severity.INFO

    def test_error(self) -> None:
        """Maps ERROR to HIGH."""
        assert _map_severity("ERROR") == Severity.HIGH

    def test_unknown(self) -> None:
        """Maps unknown to INFO."""
        assert _map_severity("UNKNOWN") == Severity.INFO

    def test_case_insensitive(self) -> None:
        """Handles lowercase input."""
        assert _map_severity("high") == Severity.HIGH
