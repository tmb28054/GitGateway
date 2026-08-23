"""Repository mirroring for GitGateway.

Pushes repository changes to external Git hosts (GitHub, GitLab)
as a background sync after each push. Credentials stored in
Secrets Manager.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import boto3

LOG = logging.getLogger(__name__)

TABLE_PREFIX = "gitgateway"


class MirrorError(Exception):
    """Raised when a mirror operation fails."""


@dataclass
class MirrorConfig:
    """Mirror configuration for a repository.

    Attributes:
        target_url: The remote URL to push to.
        credential_arn: Secrets Manager ARN for credentials.
        enabled: Whether mirroring is active.
        branches: List of branches to mirror (empty = all).
        last_sync_time: ISO timestamp of last successful sync.
        last_sync_status: Status of last sync (success/failure).
        error_message: Error from last failed sync.
        group: Repository group.
        repo: Repository name.
    """

    target_url: str = ""
    credential_arn: str = ""
    enabled: bool = True
    branches: list[str] = field(default_factory=list)
    last_sync_time: str = ""
    last_sync_status: str = ""
    error_message: str = ""
    group: str = ""
    repo: str = ""

    def to_dict(self) -> dict:
        """Serialize to dictionary for DynamoDB storage.

        Returns:
            Dictionary representation.
        """
        return {
            "target_url": self.target_url,
            "credential_arn": self.credential_arn,
            "enabled": self.enabled,
            "branches": self.branches,
            "last_sync_time": self.last_sync_time,
            "last_sync_status": self.last_sync_status,
            "error_message": self.error_message,
            "group": self.group,
            "repo": self.repo,
        }

    @classmethod
    def from_dict(cls, data: dict) -> MirrorConfig:
        """Deserialize from DynamoDB dictionary.

        Args:
            data: Dictionary from DynamoDB.

        Returns:
            A MirrorConfig instance.
        """
        return cls(
            target_url=data.get("target_url", ""),
            credential_arn=data.get("credential_arn", ""),
            enabled=bool(data.get("enabled", True)),
            branches=list(data.get("branches", [])),
            last_sync_time=data.get("last_sync_time", ""),
            last_sync_status=data.get("last_sync_status", ""),
            error_message=data.get("error_message", ""),
            group=data.get("group", ""),
            repo=data.get("repo", ""),
        )


def _get_table(dynamodb_resource: object = None):
    """Get the repos DynamoDB table.

    Args:
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        A DynamoDB Table resource.
    """
    if dynamodb_resource is None:
        dynamodb_resource = boto3.resource("dynamodb")
    return dynamodb_resource.Table(f"{TABLE_PREFIX}-repos")


def _now_iso() -> str:
    """Get current time as ISO string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def configure_mirror(
    config: MirrorConfig,
    dynamodb_resource: object = None,
) -> MirrorConfig:
    """Configure mirroring for a repository.

    Args:
        config: The MirrorConfig to save.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The saved MirrorConfig.

    Raises:
        MirrorError: If required fields are missing.
    """
    if not config.target_url:
        raise MirrorError("Target URL is required")
    if not config.group or not config.repo:
        raise MirrorError("Group and repo are required")

    table = _get_table(dynamodb_resource)
    table.put_item(
        Item={
            "PK": f"GROUP#{config.group}",
            "SK": f"MIRROR#{config.repo}",
            **config.to_dict(),
        },
    )

    LOG.info(
        "Configured mirror for %s/%s -> %s",
        config.group, config.repo, config.target_url,
    )
    return config


def get_mirror_config(
    group: str,
    repo: str,
    dynamodb_resource: object = None,
) -> MirrorConfig | None:
    """Get mirror configuration for a repository.

    Args:
        group: The group name.
        repo: The repository name.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The MirrorConfig, or None if not configured.
    """
    table = _get_table(dynamodb_resource)
    response = table.get_item(
        Key={"PK": f"GROUP#{group}", "SK": f"MIRROR#{repo}"},
    )
    item = response.get("Item")
    if not item:
        return None
    return MirrorConfig.from_dict(item)


def delete_mirror_config(
    group: str,
    repo: str,
    dynamodb_resource: object = None,
) -> None:
    """Delete mirror configuration for a repository.

    Args:
        group: The group name.
        repo: The repository name.
        dynamodb_resource: Optional boto3 DynamoDB resource.
    """
    table = _get_table(dynamodb_resource)
    table.delete_item(
        Key={"PK": f"GROUP#{group}", "SK": f"MIRROR#{repo}"},
    )
    LOG.info("Deleted mirror config for %s/%s", group, repo)


def update_sync_status(
    group: str,
    repo: str,
    status: str,
    error_message: str = "",
    dynamodb_resource: object = None,
) -> MirrorConfig | None:
    """Update the sync status after a mirror push attempt.

    Args:
        group: The group name.
        repo: The repository name.
        status: Status string ('success' or 'failure').
        error_message: Error message if failed.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The updated MirrorConfig, or None if not configured.
    """
    config = get_mirror_config(group, repo, dynamodb_resource)
    if not config:
        return None

    config.last_sync_time = _now_iso()
    config.last_sync_status = status
    config.error_message = error_message

    table = _get_table(dynamodb_resource)
    table.put_item(
        Item={
            "PK": f"GROUP#{group}",
            "SK": f"MIRROR#{repo}",
            **config.to_dict(),
        },
    )

    LOG.info("Updated mirror status for %s/%s: %s", group, repo, status)
    return config


def should_mirror_branch(
    config: MirrorConfig,
    branch: str,
) -> bool:
    """Check if a branch should be mirrored.

    Args:
        config: The mirror configuration.
        branch: The branch name to check.

    Returns:
        True if the branch should be mirrored.
    """
    if not config.enabled:
        return False
    if not config.branches:
        return True  # Empty means all branches
    return branch in config.branches
