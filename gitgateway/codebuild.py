"""AWS CodeBuild integration for GitGateway.

Triggers CodeBuild projects on PR events and posts status check
results back to the PR.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import boto3

LOG = logging.getLogger(__name__)


class CodeBuildError(Exception):
    """Raised when a CodeBuild operation fails."""


@dataclass
class BuildResult:
    """Result from a CodeBuild run.

    Attributes:
        build_id: The CodeBuild build ID.
        status: Build status (SUCCEEDED, FAILED, etc.).
        logs_url: URL to the build logs.
        start_time: ISO timestamp when build started.
        end_time: ISO timestamp when build ended.
    """

    build_id: str = ""
    status: str = ""
    logs_url: str = ""
    start_time: str = ""
    end_time: str = ""

    def to_dict(self) -> dict:
        """Serialize to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "build_id": self.build_id,
            "status": self.status,
            "logs_url": self.logs_url,
            "start_time": self.start_time,
            "end_time": self.end_time,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BuildResult:
        """Deserialize from dictionary.

        Args:
            data: Dictionary.

        Returns:
            A BuildResult instance.
        """
        return cls(
            build_id=data.get("build_id", ""),
            status=data.get("status", ""),
            logs_url=data.get("logs_url", ""),
            start_time=data.get("start_time", ""),
            end_time=data.get("end_time", ""),
        )


def _get_codebuild_client(client: object = None) -> object:
    """Get a CodeBuild client.

    Args:
        client: Optional boto3 CodeBuild client.

    Returns:
        A boto3 CodeBuild client.
    """
    if client is None:
        return boto3.client("codebuild")
    return client


def start_build(  # pylint: disable=too-many-positional-arguments
    project_name: str,
    source_bucket: str,
    source_key: str,
    environment_variables: dict[str, str] | None = None,
    buildspec_override: str = "",
    codebuild_client: object = None,
) -> str:
    """Start a CodeBuild project.

    Args:
        project_name: The CodeBuild project name.
        source_bucket: S3 bucket containing the source.
        source_key: S3 key for the source zip.
        environment_variables: Optional env vars for the build.
        buildspec_override: Optional buildspec content override.
        codebuild_client: Optional boto3 CodeBuild client.

    Returns:
        The build ID.

    Raises:
        CodeBuildError: If the build cannot be started.
    """
    client = _get_codebuild_client(codebuild_client)

    kwargs: dict = {
        "projectName": project_name,
        "sourceTypeOverride": "S3",
        "sourceLocationOverride": f"{source_bucket}/{source_key}",
    }

    if environment_variables:
        kwargs["environmentVariablesOverride"] = [
            {"name": k, "value": v, "type": "PLAINTEXT"}
            for k, v in environment_variables.items()
        ]

    if buildspec_override:
        kwargs["buildspecOverride"] = buildspec_override

    try:
        response = client.start_build(**kwargs)
    except (client.exceptions.ResourceNotFoundException, Exception) as exc:
        raise CodeBuildError(
            f"Failed to start build: {exc}",
        ) from exc

    build_id = response.get("build", {}).get("id", "")
    LOG.info("Started CodeBuild: %s (project=%s)", build_id, project_name)
    return build_id


def get_build_status(
    build_id: str,
    codebuild_client: object = None,
) -> BuildResult:
    """Get the status of a CodeBuild build.

    Args:
        build_id: The CodeBuild build ID.
        codebuild_client: Optional boto3 CodeBuild client.

    Returns:
        A BuildResult with current status.

    Raises:
        CodeBuildError: If the build cannot be found.
    """
    client = _get_codebuild_client(codebuild_client)

    try:
        response = client.batch_get_builds(ids=[build_id])
    except Exception as exc:
        raise CodeBuildError(
            f"Failed to get build status: {exc}",
        ) from exc

    builds = response.get("builds", [])
    if not builds:
        raise CodeBuildError(f"Build '{build_id}' not found")

    build = builds[0]
    logs = build.get("logs", {})
    logs_url = logs.get("deepLink", "")

    return BuildResult(
        build_id=build.get("id", ""),
        status=build.get("buildStatus", "IN_PROGRESS"),
        logs_url=logs_url,
        start_time=str(build.get("startTime", "")),
        end_time=str(build.get("endTime", "")),
    )


def map_build_status_to_check(build_status: str) -> str:
    """Map CodeBuild status to status check status.

    Args:
        build_status: CodeBuild build status string.

    Returns:
        Status check status string.
    """
    mapping = {
        "SUCCEEDED": "success",
        "FAILED": "failure",
        "FAULT": "error",
        "TIMED_OUT": "error",
        "STOPPED": "error",
        "IN_PROGRESS": "pending",
    }
    return mapping.get(build_status, "pending")
