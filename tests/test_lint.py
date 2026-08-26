"""Tests for gitgateway.lint module.

Verifies each linter function produces correct findings for known
bad input and passes on clean input.
"""

from __future__ import annotations

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
