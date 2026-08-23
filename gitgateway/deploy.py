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
import subprocess  # nosec
import tempfile
import zipfile
from pathlib import Path

import boto3

LOG = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).parent / "template.yaml"
PACKAGE_DIR = Path(__file__).parent

OUTPUT_KEY_MAP = {
    "ApiUrl": "api_url",
    "CloudFrontUrl": "cloudfront_url",
    "CustomDomainUrl": "domain_url",
    "RepoBucketName": "repo_bucket",
    "LfsBucketName": "lfs_bucket",
    "LambdaFunctionName": "lambda_function",
}


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

    Installs runtime dependencies into a temp directory, copies
    the gitgateway package, and creates a ZIP file.

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

    # Install dependencies into build dir
    requirements = [
        "jinja2",
        "pygments",
        "pyjwt[crypto]",
        "cryptography",
        "requests",
    ]

    try:
        subprocess.run(  # nosec
            [
                "pip", "install",
                "--target", str(build_dir),
                "--quiet",
                "--platform", "manylinux2014_x86_64",
                "--implementation", "cp",
                "--python-version", "3.12",
                "--only-binary=:all:",
            ] + requirements,
            check=True,
            capture_output=True,
            timeout=120,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise DeployError(
            f"Failed to install Lambda dependencies: {exc}",
        ) from exc

    # Copy gitgateway package
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
    zip_path = package_lambda()
    s3_key = upload_lambda_package(zip_path, code_bucket, session)

    # Clean up temp files
    shutil.rmtree(zip_path.parent, ignore_errors=True)

    template_body = load_template()
    parameters = _build_parameters(
        domain_name, acm_certificate_arn,
        enable_codebuild, enable_ai,
        code_bucket, s3_key, idp_url,
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


def _build_parameters(  # pylint: disable=too-many-positional-arguments
    domain_name: str,
    acm_certificate_arn: str,
    enable_codebuild: bool,
    enable_ai: bool,
    lambda_s3_bucket: str,
    lambda_s3_key: str,
    idp_url: str = "",
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
