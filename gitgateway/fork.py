"""Repository forking for GitGateway.

Implements server-side forking with S3 copy, fork metadata
linking back to parent, upstream tracking, and sync.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import boto3

LOG = logging.getLogger(__name__)

TABLE_PREFIX = "gitgateway"


class ForkError(Exception):
    """Raised when a fork operation fails."""


@dataclass
class ForkMetadata:
    """Metadata linking a fork to its parent repository.

    Attributes:
        source_group: Parent repository group.
        source_repo: Parent repository name.
        target_group: Fork repository group.
        target_repo: Fork repository name.
        forked_by: Username who created the fork.
        forked_at: ISO timestamp of fork creation.
    """

    source_group: str = ""
    source_repo: str = ""
    target_group: str = ""
    target_repo: str = ""
    forked_by: str = ""
    forked_at: str = ""

    @property
    def source_full_name(self) -> str:
        """Return source as group/repo."""
        return f"{self.source_group}/{self.source_repo}"

    @property
    def target_full_name(self) -> str:
        """Return target as group/repo."""
        return f"{self.target_group}/{self.target_repo}"

    def to_dict(self) -> dict:
        """Serialize to dictionary for DynamoDB storage.

        Returns:
            Dictionary representation.
        """
        return {
            "source_group": self.source_group,
            "source_repo": self.source_repo,
            "target_group": self.target_group,
            "target_repo": self.target_repo,
            "forked_by": self.forked_by,
            "forked_at": self.forked_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ForkMetadata:
        """Deserialize from DynamoDB dictionary.

        Args:
            data: Dictionary from DynamoDB.

        Returns:
            A ForkMetadata instance.
        """
        return cls(
            source_group=data.get("source_group", ""),
            source_repo=data.get("source_repo", ""),
            target_group=data.get("target_group", ""),
            target_repo=data.get("target_repo", ""),
            forked_by=data.get("forked_by", ""),
            forked_at=data.get("forked_at", ""),
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


def create_fork_metadata(
    metadata: ForkMetadata,
    dynamodb_resource: object = None,
) -> ForkMetadata:
    """Record fork metadata linking target to source.

    Args:
        metadata: The ForkMetadata to save.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The saved ForkMetadata.

    Raises:
        ForkError: If required fields are missing.
    """
    if not metadata.source_group or not metadata.source_repo:
        raise ForkError("Source group and repo are required")
    if not metadata.target_group or not metadata.target_repo:
        raise ForkError("Target group and repo are required")

    metadata.forked_at = _now_iso()

    table = _get_table(dynamodb_resource)

    # Store on the target repo side (fork knows its parent)
    table.put_item(
        Item={
            "PK": f"GROUP#{metadata.target_group}",
            "SK": f"FORK#{metadata.target_repo}",
            **metadata.to_dict(),
        },
    )

    # Also store on source side (parent knows its forks)
    table.put_item(
        Item={
            "PK": f"GROUP#{metadata.source_group}",
            "SK": (
                f"FORKED_TO#{metadata.target_group}"
                f"/{metadata.target_repo}"
            ),
            **metadata.to_dict(),
        },
    )

    LOG.info(
        "Created fork: %s -> %s",
        metadata.source_full_name, metadata.target_full_name,
    )
    return metadata


def get_fork_parent(
    group: str,
    repo: str,
    dynamodb_resource: object = None,
) -> ForkMetadata | None:
    """Get the parent repository of a fork.

    Args:
        group: The fork's group.
        repo: The fork's repo name.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        ForkMetadata with parent info, or None if not a fork.
    """
    table = _get_table(dynamodb_resource)
    response = table.get_item(
        Key={"PK": f"GROUP#{group}", "SK": f"FORK#{repo}"},
    )
    item = response.get("Item")
    if not item:
        return None
    return ForkMetadata.from_dict(item)


def list_forks(
    group: str,
    repo: str,
    dynamodb_resource: object = None,
) -> list[ForkMetadata]:
    """List all forks of a repository.

    Args:
        group: The source repository group.
        repo: The source repository name.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        List of ForkMetadata for each fork.
    """
    table = _get_table(dynamodb_resource)
    response = table.query(
        KeyConditionExpression=(
            "PK = :pk AND begins_with(SK, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": f"GROUP#{group}",
            ":prefix": "FORKED_TO#",
        },
    )
    # Filter to only forks of this specific repo
    return [
        ForkMetadata.from_dict(item)
        for item in response.get("Items", [])
        if item.get("source_repo") == repo
    ]


def is_fork(
    group: str,
    repo: str,
    dynamodb_resource: object = None,
) -> bool:
    """Check if a repository is a fork.

    Args:
        group: The repository group.
        repo: The repository name.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        True if the repository is a fork.
    """
    return get_fork_parent(group, repo, dynamodb_resource) is not None
