"""CloudFormation deployment for GitGateway.

Deploys the full GitGateway infrastructure including Lambda code
packaging. Packages the gitgateway module with dependencies into
a ZIP, uploads to S3, then deploys the CloudFormation stack.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import stat
import subprocess  # nosec
import tempfile
import urllib.request  # nosec
import zipfile
from pathlib import Path

import boto3

LOG = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).parent / "template.yaml"
PACKAGE_DIR = Path(__file__).parent
PROJECT_ROOT = Path(__file__).parent.parent

# S3 key (in the repos bucket) for the backup CodeBuild source zip.
BACKUP_SOURCE_KEY = "codebuild/backup.zip"

# Project files the backup CodeBuild source needs so ``pip install .`` and
# ``python scripts/backup_repos.py`` both work inside the build container.
BACKUP_SOURCE_INCLUDES = [
    "gitgateway",
    "scripts",
    "pyproject.toml",
    "README.md",
    "CHANGELOG.md",
]

OUTPUT_KEY_MAP = {
    "ApiUrl": "api_url",
    "CloudFrontUrl": "cloudfront_url",
    "CustomDomainUrl": "domain_url",
    "RepoBucketName": "repo_bucket",
    "LfsBucketName": "lfs_bucket",
    "BackupBucketName": "backup_bucket",
    "AlertsTopicArn": "alerts_topic_arn",
    "BackupProjectName": "backup_project",
    "LambdaFunctionName": "lambda_function",
}

CORE_LAYER_DEPS = [
    "dulwich",
    "jinja2",
    "markdown",
    "pygments",
    "pyjwt[crypto]",
    "cryptography",
    "requests",
]

LINTING_LAYER_DEPS = [
    "bandit",
    "pylint",
    "ruff",
    "yamllint",
]

# Binary tools for the tools layer (Linux x86_64 builds)
# Each entry: (name, url, is_archive)
# These are precompiled static binaries placed in /opt/bin in the layer
BINARY_TOOLS = [
    (
        "shellcheck",
        "https://github.com/koalaman/shellcheck/releases/download/"
        "v0.10.0/shellcheck-v0.10.0.linux.x86_64.tar.xz",
        True,
    ),
    (
        "hadolint",
        "https://github.com/hadolint/hadolint/releases/download/"
        "v2.12.0/hadolint-Linux-x86_64",
        False,
    ),
    (
        "tflint",
        "https://github.com/terraform-linters/tflint/releases/download/"
        "v0.53.0/tflint_linux_amd64.zip",
        True,
    ),
    (
        "kubeconform",
        "https://github.com/yannh/kubeconform/releases/download/"
        "v0.6.7/kubeconform-linux-amd64.tar.gz",
        True,
    ),
]

# Node.js runtime for eslint and markdownlint
NODE_URL = (
    "https://nodejs.org/dist/v20.17.0/"
    "node-v20.17.0-linux-x64.tar.xz"
)

# npm packages to install globally into the layer
NODE_PACKAGES = [
    "eslint@9",
    "markdownlint-cli2@0.14",
]


class DeployError(Exception):
    """Raised when deployment fails."""


def load_template() -> str:
    """Load the CloudFormation template from package data.

    Returns:
        Template body as a string.

    Raises:
        DeployError: If template file cannot be read.
    """
    if not TEMPLATE_PATH.exists():
        raise DeployError(
            f"Template not found at {TEMPLATE_PATH}",
        )
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def package_lambda(output_dir: Path | None = None) -> Path:
    """Package the gitgateway module into a Lambda deployment ZIP.

    Only includes the gitgateway code (no deps - those go in layers).

    Args:
        output_dir: Directory to write the ZIP to. Defaults to tempdir.

    Returns:
        Path to the created ZIP file.

    Raises:
        DeployError: If packaging fails.
    """
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp())

    build_dir = output_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    # Copy gitgateway package only (deps go in layers)
    dest_pkg = build_dir / "gitgateway"
    if dest_pkg.exists():
        shutil.rmtree(dest_pkg)
    shutil.copytree(PACKAGE_DIR, dest_pkg, dirs_exist_ok=True)

    # Create ZIP
    zip_path = output_dir / "lambda.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(build_dir):
            for filename in files:
                file_path = Path(root) / filename
                arcname = file_path.relative_to(build_dir)
                zf.write(file_path, arcname)

    LOG.info("Lambda package created: %s (%d bytes)", zip_path, zip_path.stat().st_size)
    return zip_path


def package_backup_source(output_dir: Path | None = None) -> Path:
    """Package the backup CodeBuild source into a ZIP.

    Bundles the project files (``gitgateway/``, ``scripts/``,
    ``pyproject.toml``, ``README.md``, ``CHANGELOG.md``) so the backup
    build can ``pip install .`` and run ``scripts/backup_repos.py``.

    Args:
        output_dir: Directory to write the ZIP to. Defaults to a tempdir.

    Returns:
        Path to the created ZIP file.
    """
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp())

    zip_path = output_dir / "backup-source.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for include in BACKUP_SOURCE_INCLUDES:
            src = PROJECT_ROOT / include
            if src.is_dir():
                for root, _dirs, files in os.walk(src):
                    if "__pycache__" in root:
                        continue
                    for filename in files:
                        file_path = Path(root) / filename
                        arcname = file_path.relative_to(PROJECT_ROOT)
                        zf.write(file_path, arcname)
            elif src.exists():
                zf.write(src, src.relative_to(PROJECT_ROOT))

    LOG.info(
        "Backup source package created: %s (%d bytes)",
        zip_path, zip_path.stat().st_size,
    )
    return zip_path


def upload_backup_source(
    zip_path: Path,
    bucket: str,
    session: object,
    s3_key: str = BACKUP_SOURCE_KEY,
) -> str:
    """Upload the backup CodeBuild source ZIP to S3.

    Args:
        zip_path: Path to the ZIP file.
        bucket: S3 bucket name (the repos bucket).
        session: boto3 Session.
        s3_key: Destination S3 key.

    Returns:
        The S3 key where the package was uploaded.

    Raises:
        DeployError: If upload fails.
    """
    s3_client = session.client("s3")
    try:
        s3_client.upload_file(str(zip_path), bucket, s3_key)
    except Exception as exc:
        raise DeployError(
            f"Failed to upload backup source: {exc}",
        ) from exc

    LOG.info("Backup source uploaded: s3://%s/%s", bucket, s3_key)
    return s3_key


def _package_layer(
    requirements: list[str],
    output_dir: Path,
    name: str,
) -> Path:
    """Package a Lambda layer ZIP with pip dependencies.

    Lambda layers must have deps under python/ directory.

    Args:
        requirements: List of pip package names.
        output_dir: Directory to write the ZIP.
        name: Layer name (for the ZIP filename).

    Returns:
        Path to the layer ZIP.

    Raises:
        DeployError: If packaging fails.
    """
    layer_dir = output_dir / f"layer-{name}"
    python_dir = layer_dir / "python"
    python_dir.mkdir(parents=True, exist_ok=True)

    try:
        subprocess.run(  # nosec
            [
                "pip", "install",
                "--target", str(python_dir),
                "--quiet",
                "--platform", "manylinux2014_x86_64",
                "--implementation", "cp",
                "--python-version", "3.12",
                "--only-binary=:all:",
            ] + requirements,
            check=True,
            capture_output=True,
            timeout=300,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise DeployError(
            f"Failed to install layer dependencies for {name}: {exc}",
        ) from exc

    zip_path = output_dir / f"layer-{name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(layer_dir):
            for filename in files:
                file_path = Path(root) / filename
                arcname = file_path.relative_to(layer_dir)
                zf.write(file_path, arcname)

    LOG.info("Layer %s created: %s (%d bytes)", name, zip_path, zip_path.stat().st_size)
    return zip_path


def _package_binary_layer(  # pylint: disable=too-many-locals
    output_dir: Path,
) -> Path:
    """Package a Lambda layer ZIP with precompiled binary tools.

    Downloads static Linux x86_64 binaries and places them under
    bin/ in the layer. Lambda adds /opt/bin to PATH when layers
    are attached.

    Args:
        output_dir: Directory to write the ZIP.

    Returns:
        Path to the binary tools layer ZIP.

    Raises:
        DeployError: If downloading or packaging fails.
    """
    layer_dir = output_dir / "layer-binaries"
    bin_dir = layer_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    for tool_name, url, is_archive in BINARY_TOOLS:
        LOG.info("Downloading %s...", tool_name)
        try:
            tmp_download = output_dir / f"download-{tool_name}"
            urllib.request.urlretrieve(url, tmp_download)  # nosec

            if is_archive:
                _extract_binary(
                    tmp_download, bin_dir, tool_name,
                )
            else:
                # Direct binary download
                dest = bin_dir / tool_name
                shutil.copy2(tmp_download, dest)
                dest.chmod(
                    dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH,
                )

            tmp_download.unlink(missing_ok=True)
        except Exception as exc:  # pylint: disable=broad-except
            LOG.warning(
                "Failed to download %s (will be unavailable): %s",
                tool_name, exc,
            )

    # Install Node.js and npm packages (eslint, markdownlint-cli2)
    _install_node_tools(output_dir, layer_dir)

    zip_path = output_dir / "layer-binaries.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(layer_dir):
            for filename in files:
                file_path = Path(root) / filename
                arcname = file_path.relative_to(layer_dir)
                zf.write(file_path, arcname)

    LOG.info(
        "Binary layer created: %s (%d bytes)",
        zip_path, zip_path.stat().st_size,
    )
    return zip_path


def _extract_binary(
    archive_path: Path,
    bin_dir: Path,
    tool_name: str,
) -> None:
    """Extract a binary from an archive into bin_dir.

    Supports .tar.gz, .tar.xz, and .zip archives.

    Args:
        archive_path: Path to the downloaded archive.
        bin_dir: Directory to place the extracted binary.
        tool_name: Name of the tool (used as the output filename).
    """
    archive_str = str(archive_path)
    extract_dir = archive_path.parent / f"extract-{tool_name}"
    extract_dir.mkdir(parents=True, exist_ok=True)

    if archive_str.endswith((".tar.gz", ".tar.xz", ".tgz")):
        import tarfile  # pylint: disable=import-outside-toplevel
        with tarfile.open(archive_path) as tar:
            tar.extractall(extract_dir, filter="data")  # nosec
    elif archive_str.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(extract_dir)  # nosec
    else:
        raise DeployError(f"Unknown archive format: {archive_path}")

    # Find the binary in extracted contents
    binary_found = False
    for root, _dirs, files in os.walk(extract_dir):
        for fname in files:
            fpath = Path(root) / fname
            # Match the tool name or common patterns
            if fname == tool_name or fname.startswith(tool_name):
                dest = bin_dir / tool_name
                shutil.copy2(fpath, dest)
                dest.chmod(
                    dest.stat().st_mode
                    | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH,
                )
                binary_found = True
                break
        if binary_found:
            break

    if not binary_found:
        raise DeployError(
            f"Could not find {tool_name} binary in archive",
        )

    shutil.rmtree(extract_dir, ignore_errors=True)


def _install_node_tools(  # pylint: disable=too-many-locals
    output_dir: Path, layer_dir: Path,
) -> None:
    """Install Node.js runtime and npm packages into the layer.

    Downloads Node.js Linux x86_64 binary, then uses npm to install
    eslint and markdownlint-cli2 into the layer structure.

    Args:
        output_dir: Temp directory for downloads.
        layer_dir: Layer root directory.
    """
    bin_dir = layer_dir / "bin"
    node_modules_dir = layer_dir / "node_modules"

    LOG.info("Downloading Node.js runtime...")
    try:
        node_archive = output_dir / "node.tar.xz"
        urllib.request.urlretrieve(NODE_URL, node_archive)  # nosec

        # Extract node binary
        node_extract = output_dir / "node-extract"
        node_extract.mkdir(parents=True, exist_ok=True)

        import tarfile  # pylint: disable=import-outside-toplevel
        with tarfile.open(node_archive) as tar:
            tar.extractall(node_extract, filter="data")  # nosec

        # Find node and npm binaries in extracted tree
        for root, _dirs, files in os.walk(node_extract):
            for fname in files:
                fpath = Path(root) / fname
                if fname == "node" and "bin" in str(fpath):
                    dest = bin_dir / "node"
                    shutil.copy2(fpath, dest)
                    dest.chmod(
                        dest.stat().st_mode
                        | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH,
                    )
                    break

        # Copy npm/npx and lib for npm to work
        for root, _dirs, files in os.walk(node_extract):
            root_path = Path(root)
            if root_path.name == "lib" and "node_modules" in str(root_path):
                # Copy node's lib/node_modules/npm into the layer
                npm_src = root_path / "node_modules" / "npm"
                if npm_src.exists():
                    npm_dest = layer_dir / "lib" / "node_modules" / "npm"
                    shutil.copytree(npm_src, npm_dest, dirs_exist_ok=True)
                break

        # Create npm wrapper script
        npm_wrapper = bin_dir / "npm"
        npm_wrapper.write_text(
            "#!/opt/bin/node\n"
            "require('/opt/lib/node_modules/npm/bin/npm-cli.js');\n",
            encoding="utf-8",
        )
        npm_wrapper.chmod(
            npm_wrapper.stat().st_mode
            | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH,
        )

        # Install npm packages into the layer
        LOG.info("Installing Node.js linting tools...")
        node_bin = bin_dir / "node"
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(output_dir),
            "NODE_PATH": str(node_modules_dir),
        }
        subprocess.run(  # nosec
            [
                str(node_bin),
                str(layer_dir / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"),
                "install",
                "--prefix", str(layer_dir),
                "--no-save",
                "--no-audit",
                "--no-fund",
            ] + NODE_PACKAGES,
            capture_output=True,
            timeout=120,
            check=True,
            env=env,
        )

        # Create eslint and markdownlint-cli2 binstubs
        binstubs = [
            ("eslint", "eslint"),
            ("markdownlint-cli2", "markdownlint-cli2"),
        ]
        for pkg_name, cmd in binstubs:
            pkg_bin = layer_dir / "node_modules" / ".bin" / cmd
            if not pkg_bin.exists():
                # Create a wrapper that invokes the package
                wrapper = bin_dir / cmd
                wrapper.write_text(
                    f"#!/opt/bin/node\n"
                    f"require('/opt/node_modules/{pkg_name}/bin/{cmd}.js');\n",
                    encoding="utf-8",
                )
                wrapper.chmod(
                    wrapper.stat().st_mode
                    | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH,
                )
            else:
                shutil.copy2(pkg_bin, bin_dir / cmd)

        # Clean up downloads
        node_archive.unlink(missing_ok=True)
        shutil.rmtree(node_extract, ignore_errors=True)

    except Exception as exc:  # pylint: disable=broad-except
        LOG.warning(
            "Failed to install Node.js tools "
            "(eslint/markdownlint will be unavailable): %s",
            exc,
        )


def upload_lambda_package(
    zip_path: Path,
    bucket: str,
    session: object,
) -> str:
    """Upload Lambda ZIP to S3.

    Uses a content hash as the S3 key to enable change detection.

    Args:
        zip_path: Path to the ZIP file.
        bucket: S3 bucket name.
        session: boto3 Session.

    Returns:
        The S3 key where the package was uploaded.

    Raises:
        DeployError: If upload fails.
    """
    content_hash = hashlib.sha256(
        zip_path.read_bytes(),
    ).hexdigest()[:16]
    s3_key = f"lambda-packages/gitgateway-{content_hash}.zip"

    s3_client = session.client("s3")
    try:
        s3_client.upload_file(str(zip_path), bucket, s3_key)
    except Exception as exc:
        raise DeployError(f"Failed to upload Lambda package: {exc}") from exc

    LOG.info("Lambda package uploaded: s3://%s/%s", bucket, s3_key)
    return s3_key


def deploy_stack(  # pylint: disable=too-many-positional-arguments,too-many-locals
    stack_name: str,
    domain_name: str,
    acm_certificate_arn: str,
    region: str = "us-east-1",
    profile: str | None = None,
    enable_codebuild: bool = True,
    enable_ai: bool = False,
    idp_url: str = "",
    jwt_audience: str = "git",
    enable_kms_encryption: bool = True,
) -> dict:
    """Deploy or update the GitGateway CloudFormation stack.

    Packages the Lambda code, uploads to S3, then creates/updates
    the CloudFormation stack with all resources including the
    Lambda function, API Gateway integration, and IAM roles.

    Args:
        stack_name: CloudFormation stack name.
        domain_name: Custom domain for the service (required).
        acm_certificate_arn: ACM certificate ARN (required).
        region: AWS region.
        profile: Optional AWS profile name.
        enable_codebuild: Whether to enable CodeBuild integration.
        enable_ai: Whether to enable AI features.
        idp_url: Identity provider URL for OAuth.
        jwt_audience: Expected JWT 'aud' claim from the IdP (default 'git').
        enable_kms_encryption: Use a customer-managed KMS key for S3 and
            DynamoDB when True; otherwise use SSE-S3/AWS-owned keys.

    Returns:
        Dictionary of mapped stack outputs.

    Raises:
        DeployError: If deployment fails.
    """
    if not domain_name:
        raise DeployError("DomainName parameter is required")
    if not acm_certificate_arn:
        raise DeployError("ACMCertificateARN parameter is required")

    session_kwargs: dict = {"region_name": region}
    if profile:
        session_kwargs["profile_name"] = profile

    session = boto3.Session(**session_kwargs)
    client = session.client("cloudformation")

    # Use the repos bucket for Lambda code storage
    code_bucket = f"{stack_name}-repos"

    # Ensure bucket exists (it may not on first deploy)
    _ensure_bucket(session, code_bucket, region)

    # Package and upload Lambda code
    LOG.info("Packaging Lambda function...")
    output_dir = Path(tempfile.mkdtemp())
    zip_path = package_lambda(output_dir)
    s3_key = upload_lambda_package(zip_path, code_bucket, session)

    # Package and upload the backup CodeBuild source (used by the nightly
    # backup + on-demand restore CodeBuild project).
    LOG.info("Packaging backup CodeBuild source...")
    backup_zip = package_backup_source(output_dir)
    backup_source_key = upload_backup_source(backup_zip, code_bucket, session)

    # Build and publish layers
    LOG.info("Packaging core layer...")
    core_layer_zip = _package_layer(CORE_LAYER_DEPS, output_dir, "core")
    core_layer_arn = _publish_layer(
        session, "gitgateway-core", core_layer_zip, bucket=code_bucket,
    )

    LOG.info("Packaging linting layer...")
    linting_layer_zip = _package_layer(LINTING_LAYER_DEPS, output_dir, "linting")
    linting_layer_arn = _publish_layer(
        session, "gitgateway-linting", linting_layer_zip, bucket=code_bucket,
    )

    LOG.info("Packaging binary tools layer...")
    binaries_layer_zip = _package_binary_layer(output_dir)
    binaries_layer_arn = _publish_layer(
        session, "gitgateway-binaries", binaries_layer_zip, bucket=code_bucket,
    )

    # Clean up temp files
    shutil.rmtree(output_dir, ignore_errors=True)

    template_body = load_template()
    parameters = _build_parameters(
        domain_name, acm_certificate_arn,
        enable_codebuild, enable_ai,
        code_bucket, s3_key, idp_url,
        core_layer_arn=core_layer_arn,
        linting_layer_arn=linting_layer_arn,
        binaries_layer_arn=binaries_layer_arn,
        backup_source_key=backup_source_key,
        jwt_audience=jwt_audience,
        enable_kms_encryption=enable_kms_encryption,
    )

    kwargs = {
        "StackName": stack_name,
        "TemplateBody": template_body,
        "Parameters": parameters,
        "Capabilities": ["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
    }

    try:
        if _stack_exists(client, stack_name):
            LOG.info("Updating stack: %s", stack_name)
            client.update_stack(**kwargs)
            waiter = client.get_waiter("stack_update_complete")
        else:
            LOG.info("Creating stack: %s", stack_name)
            client.create_stack(**kwargs)
            waiter = client.get_waiter("stack_create_complete")

        waiter.wait(
            StackName=stack_name,
            WaiterConfig={"Delay": 10, "MaxAttempts": 120},
        )
    except client.exceptions.ClientError as exc:
        error_msg = str(exc)
        if "No updates are to be performed" in error_msg:
            LOG.info("No updates needed for stack: %s", stack_name)
        else:
            raise DeployError(f"Deployment failed: {exc}") from exc

    return _get_stack_outputs(client, stack_name)


def _ensure_bucket(session: object, bucket: str, region: str) -> None:
    """Ensure an S3 bucket exists, creating it if needed.

    Args:
        session: boto3 Session.
        bucket: Bucket name.
        region: AWS region.
    """
    s3_client = session.client("s3")
    try:
        s3_client.head_bucket(Bucket=bucket)
    except s3_client.exceptions.ClientError:
        LOG.info("Creating bucket: %s", bucket)
        create_kwargs: dict = {"Bucket": bucket}
        if region != "us-east-1":
            create_kwargs["CreateBucketConfiguration"] = {
                "LocationConstraint": region,
            }
        s3_client.create_bucket(**create_kwargs)


def _publish_layer(
    session: object,
    layer_name: str,
    zip_path: Path,
    bucket: str = "",
) -> str:
    """Publish a Lambda layer version.

    For layers under 50MB, uploads directly. For larger layers,
    uploads to S3 first then references the S3 object.

    Args:
        session: boto3 Session.
        layer_name: Name for the layer.
        zip_path: Path to the layer ZIP file.
        bucket: S3 bucket for large layers.

    Returns:
        The layer version ARN.

    Raises:
        DeployError: If publishing fails.
    """
    lambda_client = session.client("lambda")
    zip_size = zip_path.stat().st_size
    max_direct = 50 * 1024 * 1024  # 50MB limit for direct upload

    try:
        if zip_size < max_direct:
            content = {"ZipFile": zip_path.read_bytes()}
        else:
            # Upload to S3 first
            if not bucket:
                raise DeployError(
                    f"Layer {layer_name} is {zip_size} bytes "
                    f"(>{max_direct}); bucket required for S3 upload",
                )
            s3_key = f"lambda-layers/{layer_name}.zip"
            s3_client = session.client("s3")
            s3_client.upload_file(str(zip_path), bucket, s3_key)
            LOG.info(
                "Layer %s uploaded to s3://%s/%s",
                layer_name, bucket, s3_key,
            )
            content = {"S3Bucket": bucket, "S3Key": s3_key}

        response = lambda_client.publish_layer_version(
            LayerName=layer_name,
            Description=f"GitGateway {layer_name} dependencies",
            Content=content,
            CompatibleRuntimes=["python3.12"],
        )
        layer_arn = response["LayerVersionArn"]
        LOG.info("Published layer: %s", layer_arn)
        return layer_arn
    except DeployError:
        raise
    except Exception as exc:
        raise DeployError(
            f"Failed to publish layer {layer_name}: {exc}",
        ) from exc


def _build_parameters(  # pylint: disable=too-many-positional-arguments
    domain_name: str,
    acm_certificate_arn: str,
    enable_codebuild: bool,
    enable_ai: bool,
    lambda_s3_bucket: str,
    lambda_s3_key: str,
    idp_url: str = "",
    core_layer_arn: str = "",
    linting_layer_arn: str = "",
    binaries_layer_arn: str = "",
    backup_source_key: str = BACKUP_SOURCE_KEY,
    jwt_audience: str = "git",
    enable_kms_encryption: bool = True,
) -> list[dict]:
    """Build CloudFormation parameters list.

    Args:
        domain_name: Custom domain.
        acm_certificate_arn: ACM certificate ARN.
        enable_codebuild: Enable CodeBuild flag.
        enable_ai: Enable AI flag.
        lambda_s3_bucket: S3 bucket with Lambda code.
        lambda_s3_key: S3 key for Lambda ZIP.
        idp_url: Identity provider URL.
        core_layer_arn: ARN of the core dependencies layer.
        linting_layer_arn: ARN of the linting dependencies layer.
        binaries_layer_arn: ARN of the binary tools layer.
        backup_source_key: S3 key of the backup CodeBuild source zip.
        jwt_audience: Expected JWT 'aud' claim from the IdP.
        enable_kms_encryption: Encrypt S3/DynamoDB with a customer-managed
            KMS key when True; use SSE-S3/AWS-owned keys when False.

    Returns:
        List of parameter dicts for CloudFormation.
    """
    return [
        {"ParameterKey": "DomainName", "ParameterValue": domain_name},
        {
            "ParameterKey": "ACMCertificateARN",
            "ParameterValue": acm_certificate_arn,
        },
        {
            "ParameterKey": "EnableCodeBuild",
            "ParameterValue": str(enable_codebuild).lower(),
        },
        {
            "ParameterKey": "EnableAI",
            "ParameterValue": str(enable_ai).lower(),
        },
        {
            "ParameterKey": "LambdaS3Bucket",
            "ParameterValue": lambda_s3_bucket,
        },
        {
            "ParameterKey": "LambdaS3Key",
            "ParameterValue": lambda_s3_key,
        },
        {
            "ParameterKey": "IdpUrl",
            "ParameterValue": idp_url,
        },
        {
            "ParameterKey": "JwtAudience",
            "ParameterValue": jwt_audience,
        },
        {
            "ParameterKey": "EnableKmsEncryption",
            "ParameterValue": str(enable_kms_encryption).lower(),
        },
        {
            "ParameterKey": "CoreLayerArn",
            "ParameterValue": core_layer_arn,
        },
        {
            "ParameterKey": "LintingLayerArn",
            "ParameterValue": linting_layer_arn,
        },
        {
            "ParameterKey": "BinariesLayerArn",
            "ParameterValue": binaries_layer_arn,
        },
        {
            "ParameterKey": "BackupSourceKey",
            "ParameterValue": backup_source_key,
        },
    ]


def _stack_exists(client: object, stack_name: str) -> bool:
    """Check if a CloudFormation stack exists.

    Args:
        client: boto3 CloudFormation client.
        stack_name: Stack name to check.

    Returns:
        True if the stack exists and is not in REVIEW_IN_PROGRESS.
    """
    try:
        response = client.describe_stacks(StackName=stack_name)
        stacks = response.get("Stacks", [])
        if not stacks:
            return False
        status = stacks[0].get("StackStatus", "")
        return status != "REVIEW_IN_PROGRESS"
    except client.exceptions.ClientError:
        return False


def _get_stack_outputs(client: object, stack_name: str) -> dict:
    """Get and map stack outputs to config-friendly keys.

    Args:
        client: boto3 CloudFormation client.
        stack_name: Stack name.

    Returns:
        Dictionary of mapped outputs.
    """
    response = client.describe_stacks(StackName=stack_name)
    stacks = response.get("Stacks", [])
    if not stacks:
        return {}

    outputs = stacks[0].get("Outputs", [])
    result = {}
    for output in outputs:
        key = output.get("OutputKey", "")
        value = output.get("OutputValue", "")
        mapped_key = OUTPUT_KEY_MAP.get(key, key)
        result[mapped_key] = value

    return result
