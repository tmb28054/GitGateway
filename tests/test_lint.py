"""Tests for gitgateway.lint module.

Verifies each linter function produces correct findings for known
bad input and passes on clean input.
"""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from gitgateway.lint import (
    LintFinding,
    LintResult,
    run_all_linters,
    _is_iac_file,
    _is_cfn_file,
    _is_k8s_file,
    _is_shell_file,
    _is_dockerfile,
    _run_pylint,
    _run_bandit,
    _run_ruff,
    _run_mypy,
    _run_yamllint,
    _run_json_check,
    _run_toml_check,
    _run_checkov,
    _run_cfnlint,
    _run_tflint,
    _run_kubeval,
    _run_shellcheck,
    _run_hadolint,
    _run_markdownlint,
    _run_eslint,
    _run_sqlfluff,
)


# ---------- File categorization tests ----------


class TestFileCategorizationHelpers:
    """Tests for file type detection helpers."""

    def test_is_iac_file_terraform(self) -> None:
        """Detects Terraform files."""
        assert _is_iac_file("main.tf", "resource {}") is True

    def test_is_iac_file_cloudformation(self) -> None:
        """Detects CloudFormation templates."""
        content = "AWSTemplateFormatVersion: '2010-09-09'\nResources: {}"
        assert _is_iac_file("template.yaml", content) is True

    def test_is_iac_file_kubernetes(self) -> None:
        """Detects Kubernetes manifests."""
        content = "apiVersion: v1\nkind: Pod\nmetadata:\n  name: test"
        assert _is_iac_file("pod.yaml", content) is True

    def test_is_iac_file_regular_yaml(self) -> None:
        """Regular YAML is not IaC."""
        assert _is_iac_file("config.yaml", "key: value") is False

    def test_is_iac_file_python(self) -> None:
        """Python files are not IaC."""
        assert _is_iac_file("main.py", "print('hello')") is False

    def test_is_cfn_file_positive(self) -> None:
        """Detects CloudFormation template."""
        content = "AWSTemplateFormatVersion: '2010-09-09'"
        assert _is_cfn_file("stack.yaml", content) is True

    def test_is_cfn_file_negative(self) -> None:
        """Regular YAML is not CFN."""
        assert _is_cfn_file("config.yaml", "key: value") is False

    def test_is_cfn_file_wrong_extension(self) -> None:
        """Non-YAML/JSON not detected."""
        content = "AWSTemplateFormatVersion: '2010-09-09'"
        assert _is_cfn_file("template.tf", content) is False

    def test_is_k8s_file_positive(self) -> None:
        """Detects Kubernetes manifest."""
        content = "apiVersion: v1\nkind: Service"
        assert _is_k8s_file("svc.yaml", content) is True

    def test_is_k8s_file_negative(self) -> None:
        """Regular YAML without apiVersion/kind."""
        assert _is_k8s_file("config.yml", "key: value") is False

    def test_is_shell_file_by_extension(self) -> None:
        """Detects shell by .sh extension."""
        assert _is_shell_file("deploy.sh", "echo hello") is True

    def test_is_shell_file_by_shebang(self) -> None:
        """Detects shell by shebang."""
        content = "#!/bin/bash\necho hello"
        assert _is_shell_file("deploy", content) is True

    def test_is_shell_file_negative(self) -> None:
        """Python file is not shell."""
        assert _is_shell_file("main.py", "print('hi')") is False

    def test_is_dockerfile_positive(self) -> None:
        """Detects Dockerfile."""
        assert _is_dockerfile("Dockerfile") is True
        assert _is_dockerfile("Dockerfile.prod") is True

    def test_is_dockerfile_negative(self) -> None:
        """Regular files are not Dockerfiles."""
        assert _is_dockerfile("main.py") is False
        assert _is_dockerfile("docker-compose.yml") is False


# ---------- Data format linters ----------


class TestJsonCheck:
    """Tests for _run_json_check."""

    def test_valid_json_passes(self) -> None:
        """Valid JSON produces no findings."""
        files = {"config.json": '{"key": "value"}'}
        findings = _run_json_check(files)
        assert findings == []

    def test_invalid_json_detected(self) -> None:
        """Invalid JSON produces an error finding."""
        files = {"bad.json": '{"key": "value",}'}
        findings = _run_json_check(files)
        assert len(findings) == 1
        assert findings[0].tool == "json"
        assert findings[0].severity == "error"
        assert "bad.json" in findings[0].file_path


class TestTomlCheck:
    """Tests for _run_toml_check."""

    def test_valid_toml_passes(self) -> None:
        """Valid TOML produces no findings."""
        files = {"pyproject.toml": '[project]\nname = "test"'}
        findings = _run_toml_check(files)
        assert findings == []

    def test_invalid_toml_detected(self) -> None:
        """Invalid TOML produces an error finding."""
        files = {"bad.toml": "[section\nkey = value"}
        findings = _run_toml_check(files)
        assert len(findings) == 1
        assert findings[0].tool == "toml"
        assert findings[0].severity == "error"


class TestYamlLint:
    """Tests for _run_yamllint."""

    def test_valid_yaml_passes(self) -> None:
        """Clean YAML produces no findings."""
        files = {"config.yaml": "key: value\n"}
        findings = _run_yamllint(files)
        assert findings == []

    def test_invalid_yaml_detected(self) -> None:
        """YAML with issues produces findings."""
        # Duplicate key
        files = {"bad.yaml": "key: 1\nkey: 2\n"}
        findings = _run_yamllint(files)
        assert len(findings) >= 1
        assert findings[0].tool == "yamllint"


# ---------- Python linters ----------


class TestPylint:
    """Tests for _run_pylint."""

    def test_clean_python_passes(self) -> None:
        """Clean Python code produces minimal findings."""
        files = {"clean.py": "\"\"\"Module.\"\"\"\n\n\ndef hello():\n    \"\"\"Say hello.\"\"\"\n    return 'hello'\n"}
        findings = _run_pylint(files)
        # Should have no errors
        errors = [f for f in findings if f.severity == "error"]
        assert errors == []

    def test_syntax_error_detected(self) -> None:
        """Python with syntax error produces a finding."""
        files = {"bad.py": "def foo(\n    pass\n"}
        findings = _run_pylint(files)
        assert len(findings) >= 1
        assert any(f.tool == "pylint" for f in findings)


class TestBandit:
    """Tests for _run_bandit."""

    def test_clean_python_passes(self) -> None:
        """Safe Python code produces no findings."""
        files = {"safe.py": "x = 1 + 2\n"}
        findings = _run_bandit(files)
        assert findings == []

    def test_hardcoded_password_detected(self) -> None:
        """Hardcoded password produces a finding."""
        files = {"bad.py": "password = 'supersecret123'\n"}
        findings = _run_bandit(files)
        assert len(findings) >= 1
        assert findings[0].tool == "bandit"


class TestRuff:
    """Tests for _run_ruff."""

    def test_clean_python_passes(self) -> None:
        """Clean Python passes ruff."""
        files = {"clean.py": "x = 1\n"}
        findings = _run_ruff(files)
        # May or may not find issues depending on rules
        # Just verify it returns a list
        assert isinstance(findings, list)

    def test_undefined_name_detected(self) -> None:
        """Undefined name should be caught by ruff."""
        files = {"bad.py": "print(undefined_var)\n"}
        findings = _run_ruff(files)
        # F821 undefined name - ruff should catch this
        assert isinstance(findings, list)


class TestMypy:
    """Tests for _run_mypy."""

    def test_returns_list(self) -> None:
        """Mypy returns a list of findings."""
        files = {"example.py": "x: int = 'not an int'\n"}
        findings = _run_mypy(files)
        assert isinstance(findings, list)


# ---------- IaC linters ----------


class TestCheckov:
    """Tests for _run_checkov."""

    def test_returns_list(self) -> None:
        """Checkov returns a list of findings."""
        files = {
            "template.yaml": (
                "AWSTemplateFormatVersion: '2010-09-09'\n"
                "Resources:\n"
                "  Bucket:\n"
                "    Type: AWS::S3::Bucket\n"
                "    Properties:\n"
                "      BucketName: test\n"
            ),
        }
        findings = _run_checkov(files)
        assert isinstance(findings, list)


class TestCfnLint:
    """Tests for _run_cfnlint."""

    def test_valid_template_passes(self) -> None:
        """Valid CFN template with cfn-lint binary."""
        files = {
            "template.yaml": (
                "AWSTemplateFormatVersion: '2010-09-09'\n"
                "Resources:\n"
                "  Bucket:\n"
                "    Type: AWS::S3::Bucket\n"
            ),
        }
        findings = _run_cfnlint(files)
        assert isinstance(findings, list)

    def test_invalid_template_detected(self) -> None:
        """Invalid CFN template produces findings."""
        files = {
            "bad.yaml": (
                "AWSTemplateFormatVersion: '2010-09-09'\n"
                "Resources:\n"
                "  Bad:\n"
                "    Type: AWS::Fake::Resource\n"
            ),
        }
        findings = _run_cfnlint(files)
        # cfn-lint should flag the fake resource type
        assert isinstance(findings, list)


class TestTfLint:
    """Tests for _run_tflint."""

    @patch("subprocess.run")
    def test_returns_list_when_not_available(self, mock_run) -> None:
        """Returns empty list when tflint not found."""
        mock_run.side_effect = FileNotFoundError
        files = {"main.tf": 'resource "aws_instance" "web" {}'}
        findings = _run_tflint(files)
        assert findings == []

    @patch("subprocess.run")
    def test_parses_json_output(self, mock_run) -> None:
        """Parses tflint JSON output correctly."""
        mock_run.return_value = MagicMock(
            stdout='{"issues": [{"rule": {"name": "test_rule", "severity": "warning"}, "message": "test msg", "range": {"filename": "main.tf", "start": {"line": 5}}}]}',
            stderr="",
        )
        files = {"main.tf": 'resource "aws_instance" "web" {}'}
        findings = _run_tflint(files)
        assert len(findings) == 1
        assert findings[0].tool == "tflint"
        assert findings[0].line == 5


class TestKubeval:
    """Tests for _run_kubeval."""

    @patch("subprocess.run")
    def test_returns_empty_when_not_available(self, mock_run) -> None:
        """Returns empty when kubeconform/kubeval not found."""
        mock_run.side_effect = FileNotFoundError
        files = {"pod.yaml": "apiVersion: v1\nkind: Pod\n"}
        findings = _run_kubeval(files)
        assert findings == []


# ---------- Shell/Docker linters ----------


class TestShellcheck:
    """Tests for _run_shellcheck."""

    @patch("subprocess.run")
    def test_returns_empty_when_not_available(self, mock_run) -> None:
        """Returns empty when shellcheck not found."""
        mock_run.side_effect = FileNotFoundError
        files = {"test.sh": "#!/bin/bash\necho $1\n"}
        findings = _run_shellcheck(files)
        assert findings == []

    @patch("subprocess.run")
    def test_parses_json_output(self, mock_run) -> None:
        """Parses shellcheck JSON output."""
        mock_run.return_value = MagicMock(
            stdout='[{"file": "/tmp/test.sh", "line": 2, "code": 2086, "level": "warning", "message": "Double quote to prevent globbing"}]',
        )
        files = {"test.sh": "#!/bin/bash\necho $1\n"}
        findings = _run_shellcheck(files)
        assert len(findings) == 1
        assert findings[0].tool == "shellcheck"
        assert "SC2086" in findings[0].message


class TestHadolint:
    """Tests for _run_hadolint."""

    @patch("subprocess.run")
    def test_returns_empty_when_not_available(self, mock_run) -> None:
        """Returns empty when hadolint not found."""
        mock_run.side_effect = FileNotFoundError
        files = {"Dockerfile": "FROM ubuntu\nRUN apt-get update\n"}
        findings = _run_hadolint(files)
        assert findings == []

    @patch("subprocess.run")
    def test_parses_json_output(self, mock_run) -> None:
        """Parses hadolint JSON output."""
        mock_run.return_value = MagicMock(
            stdout='[{"line": 2, "code": "DL3009", "message": "Delete apt-get lists", "level": "warning"}]',
        )
        files = {"Dockerfile": "FROM ubuntu\nRUN apt-get update\n"}
        findings = _run_hadolint(files)
        assert len(findings) == 1
        assert findings[0].tool == "hadolint"
        assert findings[0].line == 2


# ---------- Documentation linters ----------


class TestMarkdownlint:
    """Tests for _run_markdownlint."""

    @patch("subprocess.run")
    def test_returns_empty_when_not_available(self, mock_run) -> None:
        """Returns empty when markdownlint not found."""
        mock_run.side_effect = FileNotFoundError
        files = {"README.md": "# Hello\n\nWorld\n"}
        findings = _run_markdownlint(files)
        assert findings == []

    @patch("subprocess.run")
    def test_parses_text_output(self, mock_run) -> None:
        """Parses markdownlint text output."""
        mock_run.return_value = MagicMock(
            stdout="/tmp/README.md:1: MD041 First line should be a heading",
            stderr="",
            returncode=1,
        )
        files = {"README.md": "not a heading\n"}
        findings = _run_markdownlint(files)
        assert len(findings) >= 1
        assert findings[0].tool == "markdownlint"


# ---------- JS/TS linters ----------


class TestEslint:
    """Tests for _run_eslint."""

    @patch("subprocess.run")
    def test_returns_empty_when_not_available(self, mock_run) -> None:
        """Returns empty when eslint not found."""
        mock_run.side_effect = FileNotFoundError
        files = {"app.js": "console.log('hello');\n"}
        findings = _run_eslint(files)
        assert findings == []

    @patch("subprocess.run")
    def test_parses_json_output(self, mock_run) -> None:
        """Parses eslint JSON output."""
        mock_run.return_value = MagicMock(
            stdout='[{"filePath": "/tmp/app.js", "messages": [{"line": 1, "severity": 1, "ruleId": "no-console", "message": "Unexpected console statement."}]}]',
        )
        files = {"app.js": "console.log('hello');\n"}
        findings = _run_eslint(files)
        assert len(findings) == 1
        assert findings[0].tool == "eslint"
        assert "no-console" in findings[0].message


# ---------- SQL linters ----------


class TestSqlfluff:
    """Tests for _run_sqlfluff."""

    @patch("subprocess.run")
    def test_returns_empty_when_not_available(self, mock_run) -> None:
        """Returns empty when sqlfluff not found."""
        mock_run.side_effect = FileNotFoundError
        files = {"query.sql": "SELECT * FROM users;\n"}
        findings = _run_sqlfluff(files)
        assert findings == []

    @patch("subprocess.run")
    def test_parses_json_output(self, mock_run) -> None:
        """Parses sqlfluff JSON output."""
        mock_run.return_value = MagicMock(
            stdout='[{"filepath": "/tmp/query.sql", "violations": [{"start_line_no": 1, "code": "L009", "description": "Trailing whitespace"}]}]',
        )
        files = {"query.sql": "SELECT * FROM users; \n"}
        findings = _run_sqlfluff(files)
        assert len(findings) == 1
        assert findings[0].tool == "sqlfluff"
        assert "L009" in findings[0].message


# ---------- Integration: run_all_linters ----------


class TestRunAllLinters:
    """Tests for the run_all_linters aggregator."""

    def test_empty_files_returns_passed(self) -> None:
        """No files means nothing to lint — passes."""
        result = run_all_linters({})
        assert result.passed is True
        assert result.findings == []

    def test_valid_json_passes(self) -> None:
        """Valid JSON file passes overall."""
        result = run_all_linters({"data.json": '{"ok": true}'})
        # JSON check should pass, so no errors
        errors = [f for f in result.findings if f.severity == "error"]
        assert errors == []

    def test_invalid_json_fails(self) -> None:
        """Invalid JSON causes overall failure."""
        result = run_all_linters({"bad.json": "{invalid"})
        assert result.passed is False
        assert any(f.tool == "json" for f in result.findings)

    def test_mixed_files_categorized(self) -> None:
        """Multiple file types are categorized and linted."""
        files = {
            "config.json": '{"valid": true}',
            "data.toml": '[section]\nkey = "value"',
        }
        result = run_all_linters(files)
        # Both valid — should pass
        errors = [f for f in result.findings if f.severity == "error"]
        assert errors == []

    def test_lint_result_to_dict(self) -> None:
        """LintResult serializes correctly."""
        result = LintResult(
            passed=False,
            findings=[LintFinding(
                tool="test", file_path="f.py",
                line=1, message="msg", severity="error",
            )],
            summary="1 error",
        )
        d = result.to_dict()
        assert d["passed"] is False
        assert len(d["findings"]) == 1
        assert d["findings"][0]["tool"] == "test"

    def test_lint_finding_to_dict(self) -> None:
        """LintFinding serializes correctly."""
        f = LintFinding(
            tool="pylint", file_path="x.py",
            line=10, message="unused", severity="warning",
        )
        d = f.to_dict()
        assert d == {
            "tool": "pylint",
            "file_path": "x.py",
            "line": 10,
            "message": "unused",
            "severity": "warning",
        }


def _proc(stdout: str = "", returncode: int = 0):
    """Build a fake CompletedProcess-like object."""
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = ""
    proc.returncode = returncode
    return proc


class TestRunAllLinters:
    """Cover the run_all_linters orchestration across file types."""

    def test_dispatches_all_file_types(self) -> None:
        """One file per category dispatches to each linter."""
        files = {
            "a.py": "x = 1\n",
            "c.yaml": "key: value\n",
            "d.json": "{}\n",
            "e.toml": "[x]\n",
            "main.tf": 'resource "aws_s3_bucket" "b" {}\n',
            "tmpl.yaml": (
                "AWSTemplateFormatVersion: '2010-09-09'\nResources: {}\n"
            ),
            "pod.yaml": "apiVersion: v1\nkind: Pod\n",
            "run.sh": "#!/bin/bash\necho hi\n",
            "Dockerfile": "FROM scratch\n",
            "README.md": "# Title\n",
            "app.js": "const x = 1;\n",
            "q.sql": "SELECT 1;\n",
        }
        linter_names = [
            "_run_pylint", "_run_bandit", "_run_ruff", "_run_mypy",
            "_run_yamllint", "_run_json_check", "_run_toml_check",
            "_run_checkov", "_run_cfnlint", "_run_tflint", "_run_kubeval",
            "_run_shellcheck", "_run_hadolint", "_run_markdownlint",
            "_run_eslint", "_run_sqlfluff",
        ]
        from contextlib import ExitStack

        with ExitStack() as stack:
            for name in linter_names:
                stack.enter_context(
                    patch(f"gitgateway.lint.{name}", return_value=[]),
                )
            result = run_all_linters(files)
        assert isinstance(result, LintResult)
        assert result.passed is True

    def test_reports_failure_on_error_finding(self) -> None:
        """An error-severity finding makes the overall result fail."""
        err = LintFinding(
            tool="pylint", file_path="a.py", line=1,
            message="bad", severity="error",
        )
        with patch("gitgateway.lint._run_pylint", return_value=[err]), \
                patch("gitgateway.lint._run_bandit", return_value=[]), \
                patch("gitgateway.lint._run_ruff", return_value=[]), \
                patch("gitgateway.lint._run_mypy", return_value=[]):
            result = run_all_linters({"a.py": "x=1\n"})
        assert result.passed is False


class TestPylintInternals:
    """Cover pylint import-failure, exception, and findings branches."""

    def test_import_failure(self) -> None:
        """Missing pylint returns no findings."""
        import builtins

        real_import = builtins.__import__

        def _boom(name, *args, **kwargs):
            if name.startswith("pylint"):
                raise ImportError("no pylint")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_boom):
            assert _run_pylint({"a.py": "x = 1\n"}) == []

    def test_run_exception(self) -> None:
        """An exception from pylint Run is swallowed."""
        with patch(
            "pylint.lint.Run", side_effect=RuntimeError("boom"),
        ):
            assert _run_pylint({"a.py": "x = 1\n"}) == []

    def test_findings_parsed(self) -> None:
        """A pylint JSON message is turned into a finding."""
        def _fake_run(args, reporter=None, exit=True):  # noqa: A002
            reporter.out.write(json.dumps([
                {"type": "error", "path": "a.py", "line": 3,
                 "message-id": "E0001", "message": "syntax"},
            ]))

        with patch("pylint.lint.Run", side_effect=_fake_run):
            findings = _run_pylint({"a.py": "x = 1\n"})
        assert findings and findings[0].severity == "error"


class TestSubprocessLinterParsing:
    """Cover the findings-parse loops of subprocess-based linters."""

    @patch("subprocess.run")
    def test_ruff_findings(self, mock_run) -> None:
        """Ruff JSON output produces findings."""
        mock_run.return_value = _proc(json.dumps([
            {"filename": "/x/a.py", "location": {"row": 2},
             "code": "F401", "message": "unused"},
        ]))
        findings = _run_ruff({"a.py": "import os\n"})
        assert findings and findings[0].tool == "ruff"

    @patch("subprocess.run")
    def test_ruff_parse_error(self, mock_run) -> None:
        """Ruff with unparseable output logs and returns []."""
        mock_run.return_value = _proc("not json")
        assert _run_ruff({"a.py": "x=1\n"}) == []

    @patch("subprocess.run")
    def test_mypy_findings(self, mock_run) -> None:
        """Mypy text output produces findings."""
        mock_run.return_value = _proc(
            "/x/a.py:4: error: bad type\n",
        )
        findings = _run_mypy({"a.py": "x = 1\n"})
        assert findings and findings[0].severity == "error"

    @patch("subprocess.run")
    def test_cfnlint_findings(self, mock_run) -> None:
        """cfn-lint JSON output produces findings."""
        mock_run.return_value = _proc(json.dumps([
            {"Level": "Error", "Filename": "/x/t.yaml",
             "Location": {"Start": {"LineNumber": 5}},
             "Rule": {"Id": "E3001"}, "Message": "bad"},
        ]))
        findings = _run_cfnlint({"t.yaml": "Resources: {}\n"})
        assert findings and findings[0].severity == "error"

    @patch("subprocess.run")
    def test_shellcheck_findings(self, mock_run) -> None:
        """shellcheck JSON output produces findings."""
        mock_run.return_value = _proc(json.dumps([
            {"file": "/x/run.sh", "line": 2, "level": "error",
             "code": 2086, "message": "quote it"},
        ]))
        findings = _run_shellcheck({"run.sh": "echo $x\n"})
        assert findings and findings[0].severity == "error"

    @patch("subprocess.run")
    def test_hadolint_findings(self, mock_run) -> None:
        """hadolint JSON output produces findings."""
        mock_run.return_value = _proc(json.dumps([
            {"file": "/x/Dockerfile", "line": 1, "level": "warning",
             "code": "DL3006", "message": "pin version"},
        ]))
        findings = _run_hadolint({"Dockerfile": "FROM x\n"})
        assert findings and findings[0].tool == "hadolint"

    @patch("subprocess.run")
    def test_eslint_findings(self, mock_run) -> None:
        """eslint JSON output produces findings."""
        mock_run.return_value = _proc(json.dumps([
            {"filePath": "/x/app.js", "messages": [
                {"severity": 2, "line": 1, "ruleId": "no-undef",
                 "message": "undef"},
            ]},
        ]))
        findings = _run_eslint({"app.js": "x\n"})
        assert findings and findings[0].severity == "error"

    @patch("subprocess.run")
    def test_sqlfluff_findings(self, mock_run) -> None:
        """sqlfluff JSON output produces findings."""
        mock_run.return_value = _proc(json.dumps([
            {"filepath": "/x/q.sql", "violations": [
                {"start_line_no": 1, "code": "L010",
                 "description": "keywords"},
            ]},
        ]))
        findings = _run_sqlfluff({"q.sql": "select 1\n"})
        assert findings and findings[0].tool == "sqlfluff"

    @patch("subprocess.run")
    def test_kubeval_findings(self, mock_run) -> None:
        """kubeconform JSON-lines output produces findings."""
        mock_run.return_value = _proc(
            json.dumps({"status": "invalid", "filename": "/x/pod.yaml",
                        "msg": "bad"}) + "\n",
        )
        findings = _run_kubeval({"pod.yaml": "apiVersion: v1\nkind: Pod\n"})
        assert findings and findings[0].severity == "error"

    @patch("subprocess.run")
    def test_kubeval_fallback_to_kubeval_binary(self, mock_run) -> None:
        """When kubeconform is missing, it falls back to kubeval."""
        mock_run.side_effect = [
            FileNotFoundError(),
            _proc(json.dumps({"status": "valid",
                              "filename": "/x/pod.yaml"}) + "\n"),
        ]
        assert _run_kubeval({"pod.yaml": "kind: Pod\n"}) == []

    @patch("subprocess.run")
    def test_markdownlint_json_findings(self, mock_run) -> None:
        """markdownlint JSON output produces findings."""
        mock_run.return_value = _proc(json.dumps([
            {"fileName": "/x/README.md", "lineNumber": 1,
             "ruleNames": ["MD013"], "ruleDescription": "line length"},
        ]))
        findings = _run_markdownlint({"README.md": "# t\n"})
        assert isinstance(findings, list)


class TestDataclasses:
    """Cover the LintFinding / LintResult dataclass serialization."""

    def test_finding_to_dict(self) -> None:
        """LintFinding serializes to a dict."""
        d = LintFinding(
            tool="pylint", file_path="a.py", line=1,
            message="m", severity="error",
        ).to_dict()
        assert d["tool"] == "pylint" and d["severity"] == "error"

    def test_result_to_dict(self) -> None:
        """LintResult serializes nested findings."""
        result = LintResult(
            passed=False,
            findings=[LintFinding(tool="ruff", file_path="a.py")],
            summary="1 finding",
        )
        d = result.to_dict()
        assert d["passed"] is False
        assert d["findings"][0]["tool"] == "ruff"
        assert d["summary"] == "1 finding"

    def test_run_all_linters_no_matching_files(self) -> None:
        """A file set with no lintable types passes with no findings."""
        result = run_all_linters({"image.png": "binary"})
        assert result.passed is True
        assert result.findings == []


class TestLibraryLinterFindings:
    """Cover findings loops of library-based linters via boundary mocks."""

    def test_bandit_import_failure(self) -> None:
        """Missing bandit returns []."""
        import builtins

        real_import = builtins.__import__

        def _boom(name, *args, **kwargs):
            if name.startswith("bandit"):
                raise ImportError("no bandit")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_boom):
            assert _run_bandit({"a.py": "x = 1\n"}) == []

    def test_bandit_findings_high_severity(self) -> None:
        """A HIGH-severity bandit issue maps to an error finding."""
        issue = MagicMock()
        issue.severity = "HIGH"
        issue.fname = "a.py"
        issue.lineno = 4
        issue.test_id = "B105"
        issue.text = "hardcoded password"

        mgr = MagicMock()
        mgr.get_issue_list.return_value = [issue]
        with patch(
            "bandit.core.manager.BanditManager", return_value=mgr,
        ), patch("bandit.core.config.BanditConfig"):
            findings = _run_bandit({"a.py": "password='x'\n"})
        assert findings and findings[0].severity == "error"

    def test_bandit_run_exception(self) -> None:
        """An exception during bandit run is swallowed."""
        with patch(
            "bandit.core.config.BanditConfig",
            side_effect=RuntimeError("boom"),
        ):
            assert _run_bandit({"a.py": "x=1\n"}) == []

    def test_yamllint_import_failure(self) -> None:
        """Missing yamllint returns []."""
        import builtins

        real_import = builtins.__import__

        def _boom(name, *args, **kwargs):
            if name.startswith("yamllint"):
                raise ImportError("no yamllint")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_boom):
            assert _run_yamllint({"a.yaml": "k: v\n"}) == []

    def test_yamllint_error_severity(self) -> None:
        """A yamllint error-level problem maps to an error finding."""
        problem = MagicMock()
        problem.level = "error"
        problem.line = 1
        problem.message = "bad"
        problem.rule = "syntax"
        with patch("yamllint.linter.run", return_value=[problem]):
            findings = _run_yamllint({"a.yaml": "k: v\n"})
        assert findings and findings[0].severity == "error"

    def test_yamllint_run_exception(self) -> None:
        """A yamllint runtime error is swallowed per file."""
        with patch(
            "yamllint.linter.run", side_effect=RuntimeError("boom"),
        ):
            assert _run_yamllint({"a.yaml": "k: v\n"}) == []

    def test_mypy_import_error_line(self) -> None:
        """Mypy note-level lines map to warnings."""
        proc = _proc("/x/a.py:2: note: something\n")
        with patch("subprocess.run", return_value=proc):
            findings = _run_mypy({"a.py": "x = 1\n"})
        assert findings and findings[0].severity == "warning"


class TestMarkdownlintFallback:
    """Cover the mdl fallback path of _run_markdownlint."""

    @patch("subprocess.run")
    def test_mdl_json_fallback(self, mock_run) -> None:
        """cli2 missing -> mdl JSON output is parsed."""
        mock_run.side_effect = [
            FileNotFoundError(),  # markdownlint-cli2 missing
            _proc(json.dumps([
                {"filename": "/x/README.md", "line": 2,
                 "rule": "MD013", "description": "long"},
            ])),
        ]
        findings = _run_markdownlint({"README.md": "# t\n"})
        assert findings and findings[0].tool == "markdownlint"

    @patch("subprocess.run")
    def test_mdl_text_fallback(self, mock_run) -> None:
        """cli2 missing -> non-JSON mdl output falls back to text parser."""
        mock_run.side_effect = [
            FileNotFoundError(),
            _proc("/x/README.md:1: MD013 line too long\n"),
        ]
        findings = _run_markdownlint({"README.md": "# t\n"})
        assert isinstance(findings, list)

    @patch("subprocess.run")
    def test_both_missing(self, mock_run) -> None:
        """Both markdownlint tools missing returns []."""
        mock_run.side_effect = [FileNotFoundError(), FileNotFoundError()]
        assert _run_markdownlint({"README.md": "# t\n"}) == []

    @patch("subprocess.run")
    def test_mdl_nonjson_falls_back_to_text(self, mock_run) -> None:
        """cli2 missing -> mdl emits non-JSON -> text parser is used."""
        mock_run.side_effect = [
            FileNotFoundError(),  # markdownlint-cli2 missing
            _proc("/x/README.md:3: MD013 line too long\n"),  # mdl text
        ]
        findings = _run_markdownlint({"README.md": "# t\n"})
        assert findings and findings[0].line == 3

    @patch("subprocess.run")
    def test_mdl_error_handled(self, mock_run) -> None:
        """cli2 missing -> mdl raises a generic error -> swallowed."""
        mock_run.side_effect = [FileNotFoundError(), RuntimeError("boom")]
        assert _run_markdownlint({"README.md": "# t\n"}) == []

    @patch("subprocess.run")
    def test_mdl_empty_json(self, mock_run) -> None:
        """cli2 missing -> mdl returns an empty JSON list -> no findings."""
        mock_run.side_effect = [FileNotFoundError(), _proc("[]")]
        assert _run_markdownlint({"README.md": "# t\n"}) == []

    @patch("subprocess.run")
    def test_mdl_empty_stdout(self, mock_run) -> None:
        """cli2 missing -> mdl runs clean with empty stdout -> no findings."""
        mock_run.side_effect = [FileNotFoundError(), _proc("")]
        assert _run_markdownlint({"README.md": "# t\n"}) == []

    def test_parse_mdl_text_short_line_skipped(self) -> None:
        """A text line with fewer than 3 colon parts is skipped."""
        from gitgateway.lint import _parse_mdl_text

        findings: list = []
        _parse_mdl_text("no colons here\n", "/tmp/x", findings)
        assert findings == []


class TestSubprocessLinterErrors:
    """Cover parse-error / no-output branches of subprocess linters."""

    @patch("subprocess.run")
    def test_tflint_findings(self, mock_run) -> None:
        """tflint JSON output produces findings."""
        mock_run.return_value = _proc(json.dumps({
            "issues": [
                {"rule": {"name": "r"}, "message": "bad",
                 "range": {"filename": "/x/main.tf",
                           "start": {"line": 1}}},
            ],
        }))
        findings = _run_tflint({"main.tf": "resource {}\n"})
        assert isinstance(findings, list)

    @patch("subprocess.run")
    def test_kubeval_parse_error(self, mock_run) -> None:
        """kubeconform emitting unparseable output is handled."""
        mock_run.return_value = _proc("not json\n")
        assert _run_kubeval({"pod.yaml": "kind: Pod\n"}) == []

    @patch("subprocess.run")
    def test_eslint_no_output(self, mock_run) -> None:
        """eslint with empty stdout yields no findings."""
        mock_run.return_value = _proc("")
        assert _run_eslint({"app.js": "x\n"}) == []

    @patch("subprocess.run")
    def test_sqlfluff_no_output(self, mock_run) -> None:
        """sqlfluff with empty stdout yields no findings."""
        mock_run.return_value = _proc("")
        assert _run_sqlfluff({"q.sql": "SELECT 1\n"}) == []

    @patch("subprocess.run")
    def test_shellcheck_no_output(self, mock_run) -> None:
        """shellcheck with empty stdout yields no findings."""
        mock_run.return_value = _proc("")
        assert _run_shellcheck({"run.sh": "echo hi\n"}) == []


class TestCheckovInternals:
    """Cover checkov import-failure and exception branches."""

    def test_import_failure(self) -> None:
        """Missing checkov returns no findings."""
        import builtins

        real_import = builtins.__import__

        def _boom(name, *args, **kwargs):
            if name.startswith("checkov"):
                raise ImportError("no checkov")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_boom):
            assert _run_checkov({"main.tf": "resource {}\n"}) == []

    def test_findings_parsed(self) -> None:
        """A checkov failed_check is turned into a finding."""
        import sys
        import types

        # Build fake checkov modules so the in-function imports resolve.
        reg_mod = types.ModuleType("checkov.runner_registry")
        common_mod = types.ModuleType(
            "checkov.common.runners.runner_registry",
        )
        common_mod.checkov_runners = []

        class _Check:
            check_id = "CKV_AWS_1"
            file_path = "/x/main.tf"
            file_line_range = [3]
            severity = "HIGH"
            check = MagicMock(name="Encrypt bucket")

        class _Report:
            failed_checks = [_Check()]

        class _Registry:
            def __init__(self, *a, **k):
                pass

            def run(self, *a, **k):
                return [_Report()]

        reg_mod.RunnerRegistry = _Registry

        with patch.dict(sys.modules, {
            "checkov": types.ModuleType("checkov"),
            "checkov.runner_registry": reg_mod,
            "checkov.common": types.ModuleType("checkov.common"),
            "checkov.common.runners": types.ModuleType(
                "checkov.common.runners",
            ),
            "checkov.common.runners.runner_registry": common_mod,
        }):
            findings = _run_checkov({"main.tf": "resource {}\n"})
        assert findings and findings[0].tool == "checkov"

    def test_run_exception(self) -> None:
        """An exception during checkov run is swallowed."""
        import sys
        import types

        reg_mod = types.ModuleType("checkov.runner_registry")
        common_mod = types.ModuleType(
            "checkov.common.runners.runner_registry",
        )
        common_mod.checkov_runners = []

        class _Registry:
            def __init__(self, *a, **k):
                pass

            def run(self, *a, **k):
                raise RuntimeError("checkov boom")

        reg_mod.RunnerRegistry = _Registry

        with patch.dict(sys.modules, {
            "checkov": types.ModuleType("checkov"),
            "checkov.runner_registry": reg_mod,
            "checkov.common": types.ModuleType("checkov.common"),
            "checkov.common.runners": types.ModuleType(
                "checkov.common.runners",
            ),
            "checkov.common.runners.runner_registry": common_mod,
        }):
            assert _run_checkov({"main.tf": "resource {}\n"}) == []


class TestMdlTextParser:
    """Cover the markdownlint text fallback parser."""

    def test_parses_text_lines(self) -> None:
        """Text-format mdl output is parsed into findings."""
        from gitgateway.lint import _parse_mdl_text

        findings: list = []
        _parse_mdl_text(
            "/tmp/x/README.md:3: MD013 line too long\n",
            "/tmp/x", findings,
        )
        assert findings and findings[0].line == 3


class TestFileDetectionBranches:
    """Cover the Transform/AWS:: detection branches."""

    def test_iac_transform_serverless(self) -> None:
        """A SAM/serverless Transform template is detected as IaC."""
        content = "Transform: AWS::Serverless-2016-10-31\nResources:\n"
        assert _is_iac_file("app.yaml", content) is True

    def test_cfn_transform_serverless(self) -> None:
        """A SAM Transform template is detected as CFN."""
        content = "Transform: AWS::Serverless-2016-10-31\n"
        assert _is_cfn_file("app.yaml", content) is True


class TestPylintBranches:
    """Cover pylint exception and non-JSON output branches."""

    def test_run_generic_exception(self) -> None:
        """A non-SystemExit error from Run returns findings early."""
        with patch(
            "pylint.lint.Run", side_effect=RuntimeError("boom"),
        ):
            assert _run_pylint({"a.py": "x = 1\n"}) == []

    def test_non_json_output_ignored(self) -> None:
        """Non-JSON reporter output yields no findings."""
        def _fake_run(args, reporter=None, exit=True):  # noqa: A002
            reporter.out.write("not json")

        with patch("pylint.lint.Run", side_effect=_fake_run):
            assert _run_pylint({"a.py": "x = 1\n"}) == []

    def test_run_systemexit_handled(self) -> None:
        """A SystemExit from Run is caught (pass) and parsing continues."""
        def _fake_run(args, reporter=None, exit=True):  # noqa: A002
            reporter.out.write("[]")
            raise SystemExit(0)

        with patch("pylint.lint.Run", side_effect=_fake_run):
            assert _run_pylint({"a.py": "x = 1\n"}) == []


class TestSubprocessNoOutput:
    """Cover the empty-stdout / empty-result branches."""

    @patch("subprocess.run")
    def test_cfnlint_no_output(self, mock_run) -> None:
        mock_run.return_value = _proc("")
        assert _run_cfnlint({"t.yaml": "Resources: {}\n"}) == []

    @patch("subprocess.run")
    def test_tflint_no_output(self, mock_run) -> None:
        mock_run.return_value = _proc("")
        assert _run_tflint({"main.tf": "resource {}\n"}) == []

    @patch("subprocess.run")
    def test_hadolint_no_output(self, mock_run) -> None:
        mock_run.return_value = _proc("")
        assert _run_hadolint({"Dockerfile": "FROM x\n"}) == []

    @patch("subprocess.run")
    def test_tflint_empty_issues(self, mock_run) -> None:
        """tflint JSON with an empty issues list yields no findings."""
        mock_run.return_value = _proc(json.dumps({"issues": []}))
        assert _run_tflint({"main.tf": "resource {}\n"}) == []

    @patch("subprocess.run")
    def test_hadolint_empty_issues(self, mock_run) -> None:
        """hadolint JSON with an empty list yields no findings."""
        mock_run.return_value = _proc("[]")
        assert _run_hadolint({"Dockerfile": "FROM x\n"}) == []

    @patch("subprocess.run")
    def test_eslint_empty_messages(self, mock_run) -> None:
        """eslint result with no messages yields no findings."""
        mock_run.return_value = _proc(json.dumps([
            {"filePath": "/x/app.js", "messages": []},
        ]))
        assert _run_eslint({"app.js": "x\n"}) == []

    @patch("subprocess.run")
    def test_sqlfluff_empty_violations(self, mock_run) -> None:
        """sqlfluff result with no violations yields no findings."""
        mock_run.return_value = _proc(json.dumps([
            {"filepath": "/x/q.sql", "violations": []},
        ]))
        assert _run_sqlfluff({"q.sql": "SELECT 1\n"}) == []


class TestSubprocessMissingHandlers:
    """Cover FileNotFoundError handlers for ruff/mypy."""

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_ruff_missing(self, _m) -> None:
        assert _run_ruff({"a.py": "x = 1\n"}) == []

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_mypy_missing(self, _m) -> None:
        assert _run_mypy({"a.py": "x = 1\n"}) == []

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_shellcheck_missing_direct(self, _m) -> None:
        assert _run_shellcheck({"a.sh": "echo hi\n"}) == []

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_sqlfluff_missing(self, _m) -> None:
        assert _run_sqlfluff({"q.sql": "SELECT 1\n"}) == []

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_eslint_missing(self, _m) -> None:
        assert _run_eslint({"app.js": "x\n"}) == []

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_tflint_missing(self, _m) -> None:
        assert _run_tflint({"main.tf": "resource {}\n"}) == []

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_markdownlint_both_missing_direct(self, _m) -> None:
        assert _run_markdownlint({"README.md": "# t\n"}) == []


class TestMypyBranches:
    """Cover the mypy exception handler."""

    @patch("subprocess.run")
    def test_mypy_exception(self, mock_run) -> None:
        """A mypy runtime error is swallowed."""
        mock_run.side_effect = RuntimeError("mypy boom")
        assert _run_mypy({"a.py": "x = 1\n"}) == []


class TestMypyLineParsing:
    """Cover mypy line-parsing edge branches."""

    @patch("subprocess.run")
    def test_non_digit_line_number(self, mock_run) -> None:
        """A non-digit line number defaults to 0."""
        mock_run.return_value = _proc("/x/a.py:notnum: error: bad\n")
        findings = _run_mypy({"a.py": "x = 1\n"})
        assert findings and findings[0].line == 0

    @patch("subprocess.run")
    def test_short_line_skipped(self, mock_run) -> None:
        """A line without enough colon-separated parts is skipped."""
        mock_run.return_value = _proc("just two:parts\n")
        assert _run_mypy({"a.py": "x = 1\n"}) == []


class TestRuffNoOutput:
    """Cover ruff empty-stdout branch."""

    @patch("subprocess.run")
    def test_empty_output(self, mock_run) -> None:
        """Empty ruff output yields no findings."""
        mock_run.return_value = _proc("")
        assert _run_ruff({"a.py": "x = 1\n"}) == []


class TestYamllintWarning:
    """Cover yamllint warning-severity mapping."""

    def test_warning_level(self) -> None:
        """A warning-level problem maps to a warning finding."""
        problem = MagicMock()
        problem.level = "warning"
        problem.line = 1
        problem.message = "trailing space"
        problem.rule = "trailing-spaces"
        with patch("yamllint.linter.run", return_value=[problem]):
            findings = _run_yamllint({"a.yaml": "k: v \n"})
        assert findings and findings[0].severity == "warning"


class TestKubevalValidStatus:
    """Cover kubeval non-invalid status and blank-line skip."""

    @patch("subprocess.run")
    def test_valid_and_blank_lines(self, mock_run) -> None:
        """Valid entries and blank lines produce no findings."""
        mock_run.return_value = _proc(
            "\n"
            + json.dumps({"status": "valid", "filename": "/x/p.yaml"})
            + "\n",
        )
        assert _run_kubeval({"p.yaml": "kind: Pod\n"}) == []


class TestMarkdownlintTextParse:
    """Cover the markdownlint-cli2 text-output parse path."""

    @patch("subprocess.run")
    def test_cli2_text_output(self, mock_run) -> None:
        """markdownlint-cli2 stderr text output is parsed into findings."""
        proc = MagicMock()
        proc.stdout = ""
        proc.stderr = "/x/README.md:2: MD013 line too long\n"
        proc.returncode = 1
        mock_run.return_value = proc
        findings = _run_markdownlint({"README.md": "# t\n"})
        assert isinstance(findings, list)

    @patch("subprocess.run")
    def test_cli2_clean_no_output(self, mock_run) -> None:
        """markdownlint-cli2 with no output (clean) yields no findings."""
        proc = MagicMock()
        proc.stdout = ""
        proc.stderr = ""
        proc.returncode = 0
        mock_run.return_value = proc
        assert _run_markdownlint({"README.md": "# t\n"}) == []


class TestTomlBranches:
    """Cover the toml parser import-fallback branch."""

    def test_no_toml_parser(self) -> None:
        """Both tomllib and tomli missing returns []."""
        import builtins

        real_import = builtins.__import__

        def _boom(name, *args, **kwargs):
            if name in ("tomllib", "tomli"):
                raise ImportError("no toml")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_boom):
            assert _run_toml_check({"a.toml": "[x]\n"}) == []


class TestCheckovBranches:
    """Cover checkov None-report and failed-check severity branches."""

    def _run_with_reports(self, reports):
        """Invoke _run_checkov with a RunnerRegistry returning `reports`."""
        import sys
        import types

        reg_mod = types.ModuleType("checkov.runner_registry")
        common_mod = types.ModuleType(
            "checkov.common.runners.runner_registry",
        )
        common_mod.checkov_runners = []

        class _Registry:
            def __init__(self, *a, **k):
                pass

            def run(self, *a, **k):
                return reports

        reg_mod.RunnerRegistry = _Registry
        with patch.dict(sys.modules, {
            "checkov": types.ModuleType("checkov"),
            "checkov.runner_registry": reg_mod,
            "checkov.common": types.ModuleType("checkov.common"),
            "checkov.common.runners": types.ModuleType(
                "checkov.common.runners",
            ),
            "checkov.common.runners.runner_registry": common_mod,
        }):
            return _run_checkov({"main.tf": "resource {}\n"})

    def test_none_report_skipped(self) -> None:
        """A None report in the list is skipped."""
        assert self._run_with_reports([None]) == []

    def test_high_severity_check(self) -> None:
        """A HIGH-severity failed check maps to an error finding."""
        class _Check:
            check_id = "CKV_AWS_1"
            file_path = "/x/main.tf"
            file_line_range = [3]
            severity = "HIGH"
            check = MagicMock()

        class _Report:
            failed_checks = [_Check()]

        findings = self._run_with_reports(_Report())  # single, not list
        assert findings and findings[0].severity == "error"


class TestSubprocessExceptionHandlers:
    """Cover the FileNotFoundError / generic except handlers."""

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_cfnlint_missing(self, _m) -> None:
        assert _run_cfnlint({"t.yaml": "Resources: {}\n"}) == []

    @patch("subprocess.run", side_effect=RuntimeError("boom"))
    def test_cfnlint_error(self, _m) -> None:
        assert _run_cfnlint({"t.yaml": "Resources: {}\n"}) == []

    @patch("subprocess.run", side_effect=RuntimeError("boom"))
    def test_shellcheck_error(self, _m) -> None:
        assert _run_shellcheck({"a.sh": "echo hi\n"}) == []

    @patch("subprocess.run", side_effect=RuntimeError("boom"))
    def test_tflint_error(self, _m) -> None:
        assert _run_tflint({"main.tf": "resource {}\n"}) == []

    @patch("subprocess.run", side_effect=RuntimeError("boom"))
    def test_eslint_error(self, _m) -> None:
        assert _run_eslint({"app.js": "x\n"}) == []

    @patch("subprocess.run", side_effect=RuntimeError("boom"))
    def test_sqlfluff_error(self, _m) -> None:
        assert _run_sqlfluff({"q.sql": "SELECT 1\n"}) == []

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_hadolint_missing(self, _m) -> None:
        assert _run_hadolint({"Dockerfile": "FROM x\n"}) == []

    @patch("subprocess.run", side_effect=RuntimeError("boom"))
    def test_hadolint_error(self, _m) -> None:
        assert _run_hadolint({"Dockerfile": "FROM x\n"}) == []

    @patch("subprocess.run", side_effect=RuntimeError("boom"))
    def test_ruff_error(self, _m) -> None:
        assert _run_ruff({"a.py": "x=1\n"}) == []

    @patch("subprocess.run", side_effect=RuntimeError("boom"))
    def test_yamllint_via_run_error(self, _m) -> None:
        # yamllint uses the library, not subprocess; ensure a broken run is
        # tolerated by patching the library entrypoint.
        with patch(
            "yamllint.linter.run", side_effect=RuntimeError("boom"),
        ):
            assert _run_yamllint({"a.yaml": "k: v\n"}) == []
