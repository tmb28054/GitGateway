"""Tests for gitgateway.deploy module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gitgateway.deploy import (
    DeployError,
    _build_parameters,
    _get_stack_outputs,
    _stack_exists,
    deploy_stack,
    load_template,
)


class TestLoadTemplate:
    """Tests for load_template function."""

    def test_loads_template(self) -> None:
        """Loads the bundled template."""
        template = load_template()
        assert "AWSTemplateFormatVersion" in template
        assert "DomainName" in template
        assert "ACMCertificateARN" in template

    def test_raises_when_missing(self, tmp_path) -> None:
        """Raises DeployError when template not found."""
        with patch(
            "gitgateway.deploy.TEMPLATE_PATH",
            tmp_path / "nonexistent.yaml",
        ):
            with pytest.raises(DeployError, match="not found"):
                load_template()


class TestBuildParameters:
    """Tests for _build_parameters function."""

    def test_builds_params(self) -> None:
        """Builds parameter list."""
        params = _build_parameters(
            "git.example.com",
            "arn:aws:acm:us-east-1:123:cert/abc",
            True, False,
            "my-bucket", "lambda-packages/abc.zip", "",
            core_layer_arn="arn:layer:core:1",
            linting_layer_arn="arn:layer:linting:1",
            binaries_layer_arn="arn:layer:binaries:1",
        )
        keys = {p["ParameterKey"] for p in params}
        assert "DomainName" in keys
        assert "ACMCertificateARN" in keys
        assert "EnableCodeBuild" in keys
        assert "EnableAI" in keys
        assert "CoreLayerArn" in keys
        assert "LintingLayerArn" in keys
        assert "BinariesLayerArn" in keys

        domain_param = next(
            p for p in params if p["ParameterKey"] == "DomainName"
        )
        assert domain_param["ParameterValue"] == "git.example.com"

        core_param = next(
            p for p in params if p["ParameterKey"] == "CoreLayerArn"
        )
        assert core_param["ParameterValue"] == "arn:layer:core:1"

    def test_boolean_values(self) -> None:
        """Converts booleans to lowercase strings."""
        params = _build_parameters(
            "d", "a", False, True, "b", "k", "",
        )
        cb = next(
            p for p in params if p["ParameterKey"] == "EnableCodeBuild"
        )
        ai = next(p for p in params if p["ParameterKey"] == "EnableAI")
        assert cb["ParameterValue"] == "false"
        assert ai["ParameterValue"] == "true"


class TestStackExists:
    """Tests for _stack_exists function."""

    def test_returns_true_for_existing(self) -> None:
        """Returns True for an existing stack."""
        client = MagicMock()
        client.describe_stacks.return_value = {
            "Stacks": [{"StackStatus": "CREATE_COMPLETE"}],
        }
        assert _stack_exists(client, "my-stack") is True

    def test_returns_false_for_review_in_progress(self) -> None:
        """Returns False for REVIEW_IN_PROGRESS status."""
        client = MagicMock()
        client.describe_stacks.return_value = {
            "Stacks": [{"StackStatus": "REVIEW_IN_PROGRESS"}],
        }
        assert _stack_exists(client, "my-stack") is False

    def test_returns_false_on_client_error(self) -> None:
        """Returns False when describe_stacks fails."""
        client = MagicMock()
        client.exceptions.ClientError = Exception
        client.describe_stacks.side_effect = Exception("not found")
        assert _stack_exists(client, "ghost") is False

    def test_returns_false_for_empty_stacks(self) -> None:
        """Returns False when no stacks returned."""
        client = MagicMock()
        client.describe_stacks.return_value = {"Stacks": []}
        assert _stack_exists(client, "empty") is False


class TestGetStackOutputs:
    """Tests for _get_stack_outputs function."""

    def test_maps_outputs(self) -> None:
        """Maps output keys to config-friendly names."""
        client = MagicMock()
        client.describe_stacks.return_value = {
            "Stacks": [{
                "Outputs": [
                    {"OutputKey": "ApiUrl", "OutputValue": "https://api.x"},
                    {"OutputKey": "RepoBucketName", "OutputValue": "my-bucket"},
                    {"OutputKey": "Unknown", "OutputValue": "val"},
                ],
            }],
        }
        result = _get_stack_outputs(client, "stack")
        assert result["api_url"] == "https://api.x"
        assert result["repo_bucket"] == "my-bucket"
        assert result["Unknown"] == "val"

    def test_empty_when_no_stacks(self) -> None:
        """Returns empty dict when no stacks."""
        client = MagicMock()
        client.describe_stacks.return_value = {"Stacks": []}
        result = _get_stack_outputs(client, "ghost")
        assert result == {}


class TestDeployStack:
    """Tests for deploy_stack function."""

    def test_missing_domain_raises(self) -> None:
        """Raises when domain_name is empty."""
        with pytest.raises(DeployError, match="DomainName"):
            deploy_stack("stack", "", "arn:cert")

    def test_missing_acm_raises(self) -> None:
        """Raises when acm_certificate_arn is empty."""
        with pytest.raises(DeployError, match="ACMCertificateARN"):
            deploy_stack("stack", "domain.com", "")

    @patch("gitgateway.deploy._publish_layer")
    @patch("gitgateway.deploy._package_binary_layer")
    @patch("gitgateway.deploy._package_layer")
    @patch("gitgateway.deploy.package_lambda")
    @patch("gitgateway.deploy.boto3.Session")
    def test_creates_new_stack(
        self, mock_session, mock_pkg, mock_layer, mock_binary, mock_publish,
    ) -> None:
        """Creates a new stack when it doesn't exist."""
        mock_client = MagicMock()
        mock_client.exceptions.ClientError = Exception

        # Stack doesn't exist
        mock_client.describe_stacks.side_effect = [
            Exception("not found"),  # _stack_exists check
            {"Stacks": [{"Outputs": [
                {"OutputKey": "ApiUrl", "OutputValue": "https://new"},
            ]}]},  # _get_stack_outputs
        ]
        mock_client.get_waiter.return_value.wait = MagicMock()

        mock_session.return_value.client.return_value = mock_client

        # Mock packaging
        from pathlib import Path
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        fake_zip = tmp / "lambda.zip"
        fake_zip.write_bytes(b"fake")
        mock_pkg.return_value = fake_zip
        mock_layer.return_value = fake_zip
        mock_binary.return_value = fake_zip
        mock_publish.return_value = "arn:aws:lambda:us-east-1:123:layer:test:1"

        result = deploy_stack(
            "gitgateway", "git.example.com",
            "arn:aws:acm:us-east-1:123:cert/abc",
        )

        mock_client.create_stack.assert_called_once()
        assert result["api_url"] == "https://new"

    @patch("gitgateway.deploy._publish_layer")
    @patch("gitgateway.deploy._package_binary_layer")
    @patch("gitgateway.deploy._package_layer")
    @patch("gitgateway.deploy.package_lambda")
    @patch("gitgateway.deploy.boto3.Session")
    def test_updates_existing_stack(
        self, mock_session, mock_pkg, mock_layer, mock_binary, mock_publish,
    ) -> None:
        """Updates an existing stack."""
        mock_client = MagicMock()
        mock_client.exceptions.ClientError = Exception

        mock_client.describe_stacks.side_effect = [
            {"Stacks": [{"StackStatus": "CREATE_COMPLETE"}]},
            {"Stacks": [{"Outputs": []}]},
        ]
        mock_client.get_waiter.return_value.wait = MagicMock()

        mock_session.return_value.client.return_value = mock_client

        from pathlib import Path
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        fake_zip = tmp / "lambda.zip"
        fake_zip.write_bytes(b"fake")
        mock_pkg.return_value = fake_zip
        mock_layer.return_value = fake_zip
        mock_binary.return_value = fake_zip
        mock_publish.return_value = "arn:aws:lambda:us-east-1:123:layer:test:1"

        deploy_stack(
            "gitgateway", "x.com", "arn:cert",
            profile="prod",
        )
        mock_client.update_stack.assert_called_once()

    @patch("gitgateway.deploy._publish_layer")
    @patch("gitgateway.deploy._package_binary_layer")
    @patch("gitgateway.deploy._package_layer")
    @patch("gitgateway.deploy.package_lambda")
    @patch("gitgateway.deploy.boto3.Session")
    def test_no_updates_needed(
        self, mock_session, mock_pkg, mock_layer, mock_binary, mock_publish,
    ) -> None:
        """Handles 'no updates' gracefully."""
        mock_client = MagicMock()
        mock_client.exceptions.ClientError = Exception

        mock_client.describe_stacks.side_effect = [
            {"Stacks": [{"StackStatus": "CREATE_COMPLETE"}]},
            {"Stacks": [{"Outputs": []}]},
        ]
        mock_client.update_stack.side_effect = Exception(
            "No updates are to be performed",
        )

        mock_session.return_value.client.return_value = mock_client

        from pathlib import Path
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        fake_zip = tmp / "lambda.zip"
        fake_zip.write_bytes(b"fake")
        mock_pkg.return_value = fake_zip
        mock_layer.return_value = fake_zip
        mock_binary.return_value = fake_zip
        mock_publish.return_value = "arn:aws:lambda:us-east-1:123:layer:test:1"

        result = deploy_stack("stack", "d.com", "arn:c")
        assert result == {}

    @patch("gitgateway.deploy._publish_layer")
    @patch("gitgateway.deploy._package_binary_layer")
    @patch("gitgateway.deploy._package_layer")
    @patch("gitgateway.deploy.package_lambda")
    @patch("gitgateway.deploy.boto3.Session")
    def test_deployment_failure_raises(
        self, mock_session, mock_pkg, mock_layer, mock_binary, mock_publish,
    ) -> None:
        """Raises DeployError on actual failure."""
        mock_client = MagicMock()
        mock_client.exceptions.ClientError = Exception

        mock_client.describe_stacks.side_effect = [
            {"Stacks": [{"StackStatus": "CREATE_COMPLETE"}]},
        ]
        mock_client.update_stack.side_effect = Exception(
            "ValidationError: template is invalid",
        )

        mock_session.return_value.client.return_value = mock_client

        from pathlib import Path
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        fake_zip = tmp / "lambda.zip"
        fake_zip.write_bytes(b"fake")
        mock_pkg.return_value = fake_zip
        mock_layer.return_value = fake_zip
        mock_binary.return_value = fake_zip
        mock_publish.return_value = "arn:aws:lambda:us-east-1:123:layer:test:1"

        with pytest.raises(DeployError, match="Deployment failed"):
            deploy_stack("stack", "d.com", "arn:c")
