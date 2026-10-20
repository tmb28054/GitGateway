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
        assert "JwtAudience" in keys
        assert "EnableKmsEncryption" in keys

        domain_param = next(
            p for p in params if p["ParameterKey"] == "DomainName"
        )
        assert domain_param["ParameterValue"] == "git.example.com"

        core_param = next(
            p for p in params if p["ParameterKey"] == "CoreLayerArn"
        )
        assert core_param["ParameterValue"] == "arn:layer:core:1"

        # Audience defaults to "git" to match the IdP.
        aud_param = next(
            p for p in params if p["ParameterKey"] == "JwtAudience"
        )
        assert aud_param["ParameterValue"] == "git"

    def test_custom_jwt_audience(self) -> None:
        """A custom jwt_audience is passed through to the parameter list."""
        params = _build_parameters(
            "d", "a", True, False, "b", "k", "",
            jwt_audience="gitgateway",
        )
        aud = next(p for p in params if p["ParameterKey"] == "JwtAudience")
        assert aud["ParameterValue"] == "gitgateway"

    def test_kms_enabled_by_default(self) -> None:
        """EnableKmsEncryption defaults to 'true'."""
        params = _build_parameters("d", "a", True, False, "b", "k", "")
        kms = next(
            p for p in params if p["ParameterKey"] == "EnableKmsEncryption"
        )
        assert kms["ParameterValue"] == "true"

    def test_kms_can_be_disabled(self) -> None:
        """enable_kms_encryption=False maps to 'false'."""
        params = _build_parameters(
            "d", "a", True, False, "b", "k", "",
            enable_kms_encryption=False,
        )
        kms = next(
            p for p in params if p["ParameterKey"] == "EnableKmsEncryption"
        )
        assert kms["ParameterValue"] == "false"

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


import io
import tarfile
import zipfile
from pathlib import Path

import boto3
from moto import mock_aws

from gitgateway.deploy import (
    _ensure_bucket,
    _extract_binary,
    _install_node_tools,
    _package_binary_layer,
    _package_layer,
    _publish_layer,
    package_backup_source,
    package_lambda,
    upload_backup_source,
    upload_lambda_package,
)


class TestPackageLambda:
    """Tests for package_lambda."""

    def test_creates_zip(self, tmp_path: Path) -> None:
        """Packages the gitgateway module into a ZIP."""
        zip_path = package_lambda(tmp_path)
        assert zip_path.exists()
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert any(n.startswith("gitgateway/") for n in names)

    def test_default_output_dir(self, tmp_path: Path) -> None:
        """Uses a temp dir when no output dir is given."""
        # Point mkdtemp at a real pytest-managed dir so package_lambda's
        # default-output branch writes there (not into a literal
        # "MagicMock/mkdtemp()" path derived from an un-set mock).
        with patch("tempfile.mkdtemp", return_value=str(tmp_path)):
            zip_path = package_lambda()
        assert zip_path.exists()

    def test_overwrites_existing_build(self, tmp_path: Path) -> None:
        """A pre-existing build/gitgateway dir is cleaned first."""
        stale = tmp_path / "build" / "gitgateway"
        stale.mkdir(parents=True)
        (stale / "stale.txt").write_text("old")
        zip_path = package_lambda(tmp_path)
        assert zip_path.exists()


class TestPackageLayer:
    """Tests for _package_layer."""

    def test_success(self, tmp_path: Path) -> None:
        """Runs pip install (mocked) and zips the layer."""
        def _fake_pip(*args, **kwargs):
            # Simulate pip writing a package into the target dir.
            target = None
            argv = args[0]
            if "--target" in argv:
                target = argv[argv.index("--target") + 1]
            if target:
                (Path(target) / "pkg").mkdir(parents=True, exist_ok=True)
                (Path(target) / "pkg" / "__init__.py").write_text("")
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_fake_pip):
            zip_path = _package_layer(["somepkg"], tmp_path, "core")
        assert zip_path.exists()
        with zipfile.ZipFile(zip_path) as zf:
            assert any("python/" in n for n in zf.namelist())

    def test_pip_failure_raises(self, tmp_path: Path) -> None:
        """A pip failure raises DeployError."""
        import subprocess

        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "pip"),
        ):
            with pytest.raises(DeployError, match="layer dependencies"):
                _package_layer(["x"], tmp_path, "core")

    def test_pip_missing_raises(self, tmp_path: Path) -> None:
        """pip not installed raises DeployError."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(DeployError):
                _package_layer(["x"], tmp_path, "core")


class TestExtractBinary:
    """Tests for _extract_binary."""

    def _make_targz(self, path: Path, member: str) -> None:
        with tarfile.open(path, "w:gz") as tar:
            data = b"#!/bin/sh\necho hi\n"
            info = tarfile.TarInfo(name=member)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    def test_targz(self, tmp_path: Path) -> None:
        """Extracts a binary from a .tar.gz archive."""
        archive = tmp_path / "tool.tar.gz"
        self._make_targz(archive, "shellcheck")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _extract_binary(archive, bin_dir, "shellcheck")
        assert (bin_dir / "shellcheck").exists()

    def test_zip(self, tmp_path: Path) -> None:
        """Extracts a binary from a .zip archive."""
        archive = tmp_path / "tool.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("tflint", "binary")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _extract_binary(archive, bin_dir, "tflint")
        assert (bin_dir / "tflint").exists()

    def test_unknown_format_raises(self, tmp_path: Path) -> None:
        """An unrecognized archive extension raises DeployError."""
        archive = tmp_path / "tool.rar"
        archive.write_bytes(b"x")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        with pytest.raises(DeployError, match="Unknown archive format"):
            _extract_binary(archive, bin_dir, "tool")

    def test_binary_not_found_raises(self, tmp_path: Path) -> None:
        """A binary missing from the archive raises DeployError."""
        archive = tmp_path / "tool.tar.gz"
        self._make_targz(archive, "somethingelse")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        with pytest.raises(DeployError, match="Could not find"):
            _extract_binary(archive, bin_dir, "tflint")


class TestPackageBinaryLayer:
    """Tests for _package_binary_layer."""

    def test_download_failure_is_tolerated(self, tmp_path: Path) -> None:
        """A failed tool download is logged but does not abort the layer."""
        with patch(
            "urllib.request.urlretrieve",
            side_effect=OSError("network down"),
        ), patch("gitgateway.deploy._install_node_tools"):
            zip_path = _package_binary_layer(tmp_path)
        assert zip_path.exists()

    def test_direct_binary_download(self, tmp_path: Path) -> None:
        """Non-archive tools are copied directly into bin/."""
        def _fake_retrieve(url, dest):
            Path(dest).write_bytes(b"binary")

        with patch(
            "urllib.request.urlretrieve", side_effect=_fake_retrieve,
        ), patch(
            "gitgateway.deploy._extract_binary",
        ), patch("gitgateway.deploy._install_node_tools"):
            zip_path = _package_binary_layer(tmp_path)
        assert zip_path.exists()


class TestInstallNodeTools:
    """Tests for _install_node_tools."""

    def test_download_failure_tolerated(self, tmp_path: Path) -> None:
        """A Node.js download failure is caught and logged."""
        layer_dir = tmp_path / "layer"
        (layer_dir / "bin").mkdir(parents=True)
        with patch(
            "urllib.request.urlretrieve",
            side_effect=OSError("no network"),
        ):
            # Should not raise.
            _install_node_tools(tmp_path, layer_dir)

    def test_success_path(self, tmp_path: Path) -> None:
        """Builds node/npm layout, runs npm (mocked), writes binstubs."""
        layer_dir = tmp_path / "layer"
        (layer_dir / "bin").mkdir(parents=True)

        def _fake_retrieve(url, dest):
            # Build a fake node tarball with bin/node and a `lib` dir whose
            # path contains "node_modules" (matching the copy condition),
            # holding node_modules/npm/bin/npm-cli.js.
            root = tmp_path / "node-src" / "node-v20-linux-x64"
            (root / "bin").mkdir(parents=True, exist_ok=True)
            (root / "bin" / "node").write_text("#!node\n")
            lib = root / "node_modules" / "corepack" / "lib"
            npm_bin = lib / "node_modules" / "npm" / "bin"
            npm_bin.mkdir(parents=True, exist_ok=True)
            (npm_bin / "npm-cli.js").write_text("// npm\n")
            with tarfile.open(dest, "w:xz") as tar:
                tar.add(root, arcname="node-v20-linux-x64")

        with patch(
            "urllib.request.urlretrieve", side_effect=_fake_retrieve,
        ), patch("subprocess.run", return_value=MagicMock(returncode=0)):
            _install_node_tools(tmp_path, layer_dir)

        # node binary and npm wrapper should be present in bin/.
        assert (layer_dir / "bin" / "node").exists()
        assert (layer_dir / "bin" / "npm").exists()

    def test_success_lib_without_npm(self, tmp_path: Path) -> None:
        """A matching lib dir lacking node_modules/npm skips the copy."""
        layer_dir = tmp_path / "layer"
        (layer_dir / "bin").mkdir(parents=True)

        def _fake_retrieve(url, dest):
            root = tmp_path / "node-src" / "node-v20-linux-x64"
            (root / "bin").mkdir(parents=True, exist_ok=True)
            (root / "bin" / "node").write_text("#!node\n")
            # A `lib` dir whose path contains node_modules but with NO
            # node_modules/npm inside it (exercises the false branch).
            lib = root / "node_modules" / "corepack" / "lib"
            lib.mkdir(parents=True, exist_ok=True)
            (lib / "placeholder.txt").write_text("x")
            with tarfile.open(dest, "w:xz") as tar:
                tar.add(root, arcname="node-v20-linux-x64")

        with patch(
            "urllib.request.urlretrieve", side_effect=_fake_retrieve,
        ), patch("subprocess.run", return_value=MagicMock(returncode=0)):
            _install_node_tools(tmp_path, layer_dir)
        assert (layer_dir / "bin" / "node").exists()

    def test_success_with_existing_binstub(self, tmp_path: Path) -> None:
        """When a package .bin stub exists, it is copied instead of wrapped."""
        layer_dir = tmp_path / "layer"
        (layer_dir / "bin").mkdir(parents=True)
        # Pre-create the eslint/.bin stub npm "install" would produce.
        bin_stub_dir = layer_dir / "node_modules" / ".bin"
        bin_stub_dir.mkdir(parents=True)
        (bin_stub_dir / "eslint").write_text("#!/bin/sh\n")
        (bin_stub_dir / "markdownlint-cli2").write_text("#!/bin/sh\n")

        def _fake_retrieve(url, dest):
            root = tmp_path / "node-src" / "node-v20-linux-x64"
            (root / "bin").mkdir(parents=True, exist_ok=True)
            (root / "bin" / "node").write_text("#!node\n")
            npm_dir = root / "lib" / "node_modules" / "npm" / "bin"
            npm_dir.mkdir(parents=True, exist_ok=True)
            (npm_dir / "npm-cli.js").write_text("// npm\n")
            with tarfile.open(dest, "w:xz") as tar:
                tar.add(root, arcname="node-v20-linux-x64")

        with patch(
            "urllib.request.urlretrieve", side_effect=_fake_retrieve,
        ), patch("subprocess.run", return_value=MagicMock(returncode=0)):
            _install_node_tools(tmp_path, layer_dir)
        assert (layer_dir / "bin" / "eslint").exists()


class TestEnsureBucket:
    """Tests for _ensure_bucket."""

    @mock_aws
    def test_creates_when_missing_us_east_1(self) -> None:
        """Creates the bucket in us-east-1 when absent."""
        session = boto3.Session(region_name="us-east-1")
        _ensure_bucket(session, "new-bucket", "us-east-1")
        assert session.client("s3").head_bucket(Bucket="new-bucket")

    @mock_aws
    def test_creates_with_location_constraint(self) -> None:
        """Creates the bucket with a location constraint outside us-east-1."""
        session = boto3.Session(region_name="eu-west-1")
        _ensure_bucket(session, "eu-bucket", "eu-west-1")
        assert session.client("s3").head_bucket(Bucket="eu-bucket")

    @mock_aws
    def test_noop_when_exists(self) -> None:
        """Does nothing when the bucket already exists."""
        session = boto3.Session(region_name="us-east-1")
        session.client("s3").create_bucket(Bucket="exists")
        _ensure_bucket(session, "exists", "us-east-1")


class TestPublishLayer:
    """Tests for _publish_layer."""

    @mock_aws
    def test_direct_upload_small(self, tmp_path: Path) -> None:
        """A small layer is published directly."""
        zip_path = tmp_path / "layer.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("python/x.py", "x = 1")
        session = boto3.Session(region_name="us-east-1")
        arn = _publish_layer(session, "gitgateway-core", zip_path)
        assert "gitgateway-core" in arn

    @mock_aws
    def test_large_layer_via_s3(self, tmp_path: Path) -> None:
        """A large layer is uploaded to S3 then referenced."""
        zip_path = tmp_path / "big.zip"
        zip_path.write_bytes(b"0")
        session = boto3.Session(region_name="us-east-1")
        session.client("s3").create_bucket(Bucket="code-bucket")
        with patch.object(Path, "stat") as st:
            st.return_value = MagicMock(st_size=60 * 1024 * 1024)
            # read_bytes not used on the large path; upload_file is.
            arn = _publish_layer(
                session, "gitgateway-core", zip_path, bucket="code-bucket",
            )
        assert "gitgateway-core" in arn

    def test_large_layer_without_bucket_raises(self, tmp_path: Path) -> None:
        """A large layer with no bucket raises DeployError."""
        zip_path = tmp_path / "big.zip"
        zip_path.write_bytes(b"0")
        session = MagicMock()
        with patch.object(Path, "stat") as st:
            st.return_value = MagicMock(st_size=60 * 1024 * 1024)
            with pytest.raises(DeployError, match="bucket required"):
                _publish_layer(session, "core", zip_path)

    def test_publish_failure_raises(self, tmp_path: Path) -> None:
        """A publish API failure raises DeployError."""
        zip_path = tmp_path / "layer.zip"
        zip_path.write_bytes(b"data")
        session = MagicMock()
        session.client.return_value.publish_layer_version.side_effect = (
            RuntimeError("api boom")
        )
        with pytest.raises(DeployError, match="Failed to publish"):
            _publish_layer(session, "core", zip_path)


class TestUploadLambdaPackage:
    """Tests for upload_lambda_package."""

    @mock_aws
    def test_uploads_and_returns_key(self, tmp_path: Path) -> None:
        """Uploads the ZIP to S3 and returns the key."""
        zip_path = tmp_path / "lambda.zip"
        zip_path.write_bytes(b"payload")
        session = boto3.Session(region_name="us-east-1")
        session.client("s3").create_bucket(Bucket="code")
        key = upload_lambda_package(zip_path, "code", session)
        assert key.endswith(".zip")

    def test_upload_failure_raises(self, tmp_path: Path) -> None:
        """An S3 upload failure raises DeployError."""
        zip_path = tmp_path / "lambda.zip"
        zip_path.write_bytes(b"payload")
        session = MagicMock()
        session.client.return_value.upload_file.side_effect = RuntimeError(
            "s3 down",
        )
        with pytest.raises(DeployError, match="Failed to upload"):
            upload_lambda_package(zip_path, "code", session)


class TestPackageBackupSource:
    """Tests for package_backup_source and upload_backup_source."""

    @staticmethod
    def _fake_project(root: Path) -> None:
        """Build a small fake project tree under root."""
        pkg = root / "gitgateway"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        cache = pkg / "__pycache__"
        cache.mkdir()
        (cache / "x.pyc").write_text("bytecode")
        (root / "pyproject.toml").write_text("[project]\nname='x'\n")
        # A listed include that does not exist is skipped silently.

    def test_creates_zip_with_dirs_and_files(self, tmp_path: Path) -> None:
        """Bundles dirs (skipping __pycache__) and single files."""
        root = tmp_path / "proj"
        root.mkdir()
        self._fake_project(root)
        out = tmp_path / "out"
        out.mkdir()
        with patch("gitgateway.deploy.PROJECT_ROOT", root), patch(
            "gitgateway.deploy.BACKUP_SOURCE_INCLUDES",
            ["gitgateway", "pyproject.toml", "does-not-exist"],
        ):
            zip_path = package_backup_source(out)
        assert zip_path.exists()
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert "gitgateway/__init__.py" in names
        assert "pyproject.toml" in names
        # __pycache__ contents are excluded.
        assert not any("__pycache__" in n for n in names)

    def test_default_output_dir(self, tmp_path: Path) -> None:
        """Uses a temp dir when no output dir is given."""
        root = tmp_path / "proj"
        root.mkdir()
        self._fake_project(root)
        out = tmp_path / "out"
        out.mkdir()
        with patch("gitgateway.deploy.PROJECT_ROOT", root), patch(
            "gitgateway.deploy.BACKUP_SOURCE_INCLUDES", ["pyproject.toml"],
        ), patch("tempfile.mkdtemp", return_value=str(out)):
            zip_path = package_backup_source()
        assert zip_path.exists()

    def test_upload_success(self, tmp_path: Path) -> None:
        """Uploads the source zip and returns the key."""
        zip_path = tmp_path / "backup-source.zip"
        zip_path.write_bytes(b"z")
        session = MagicMock()
        key = upload_backup_source(zip_path, "code", session, s3_key="k.zip")
        assert key == "k.zip"
        session.client.return_value.upload_file.assert_called_once()

    def test_upload_failure_raises(self, tmp_path: Path) -> None:
        """An upload failure raises DeployError."""
        zip_path = tmp_path / "backup-source.zip"
        zip_path.write_bytes(b"z")
        session = MagicMock()
        session.client.return_value.upload_file.side_effect = RuntimeError(
            "s3 down",
        )
        with pytest.raises(DeployError, match="Failed to upload backup"):
            upload_backup_source(zip_path, "code", session)
