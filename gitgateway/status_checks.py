"""Status checks API for GitGateway.

Manages generic status checks on PRs posted by external CI systems,
and provides merge blocking when required checks haven't passed.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from enum import Enum

import boto3

LOG = logging.getLogger(__name__)

TABLE_PREFIX = "gitgateway"


class StatusCheckError(Exception):
    """Raised when a status check operation fails."""


class CheckStatus(Enum):
    """Status check result states."""

    PENDING = "pending"
    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"


@dataclass
class StatusCheck:
    """A status check on a pull request.

    Attributes:
        check_id: Unique check identifier.
        name: The check name (e.g., 'ci/tests', 'sast/bandit').
        status: Current check status.
        description: Human-readable description of the result.
        target_url: URL to the check details (e.g., build logs).
        context: Additional context identifier.
        pr_number: The PR this check is on.
        group: Repository group.
        repo: Repository name.
        created_at: ISO timestamp of creation.
        updated_at: ISO timestamp of last update.
    """

    check_id: str = ""
    name: str = ""
    status: CheckStatus = CheckStatus.PENDING
    description: str = ""
    target_url: str = ""
    context: str = ""
    pr_number: int = 0
    group: str = ""
    repo: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        """Serialize to dictionary for DynamoDB storage.

        Returns:
            Dictionary representation.
        """
        return {
            "check_id": self.check_id,
            "name": self.name,
            "status": self.status.value,
            "description": self.description,
            "target_url": self.target_url,
            "context": self.context,
            "pr_number": self.pr_number,
            "group": self.group,
            "repo": self.repo,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> StatusCheck:
        """Deserialize from DynamoDB dictionary.

        Args:
            data: Dictionary from DynamoDB.

        Returns:
            A StatusCheck instance.
        """
        return cls(
            check_id=data.get("check_id", ""),
            name=data.get("name", ""),
            status=CheckStatus(data.get("status", "pending")),
            description=data.get("description", ""),
            target_url=data.get("target_url", ""),
            context=data.get("context", ""),
            pr_number=int(data.get("pr_number", 0)),
            group=data.get("group", ""),
            repo=data.get("repo", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


def _get_table(table_suffix: str, dynamodb_resource: object = None):
    """Get a DynamoDB table resource.

    Args:
        table_suffix: Table name suffix.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        A DynamoDB Table resource.
    """
    if dynamodb_resource is None:
        dynamodb_resource = boto3.resource("dynamodb")
    return dynamodb_resource.Table(f"{TABLE_PREFIX}-{table_suffix}")


def _now_iso() -> str:
    """Get current time as ISO string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def post_status(
    check: StatusCheck,
    dynamodb_resource: object = None,
) -> StatusCheck:
    """Post or update a status check on a PR.

    If a check with the same name already exists on the PR,
    it will be updated. Otherwise a new check is created.

    Args:
        check: The StatusCheck to post.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The posted/updated StatusCheck.

    Raises:
        StatusCheckError: If required fields are missing.
    """
    if not check.name:
        raise StatusCheckError("Check name is required")
    if not check.pr_number:
        raise StatusCheckError("PR number is required")

    now = _now_iso()

    # Check if one already exists with this name
    existing = _find_by_name(
        check.group, check.repo, check.pr_number,
        check.name, dynamodb_resource,
    )

    if existing:
        check.check_id = existing.check_id
        check.created_at = existing.created_at
        check.updated_at = now
    else:
        check.check_id = str(uuid.uuid4())
        check.created_at = now
        check.updated_at = now

    table = _get_table("status-checks", dynamodb_resource)
    table.put_item(
        Item={
            "PK": f"PR#{check.group}/{check.repo}/{check.pr_number}",
            "SK": f"CHECK#{check.name}",
            **check.to_dict(),
        },
    )

    LOG.info(
        "Posted status '%s' = %s on PR #%d",
        check.name, check.status.value, check.pr_number,
    )
    return check


def list_statuses(
    group: str,
    repo: str,
    pr_number: int,
    dynamodb_resource: object = None,
) -> list[StatusCheck]:
    """List all status checks for a PR.

    Args:
        group: The group name.
        repo: The repository name.
        pr_number: The PR number.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        List of StatusCheck objects.
    """
    table = _get_table("status-checks", dynamodb_resource)
    response = table.query(
        KeyConditionExpression=(
            "PK = :pk AND begins_with(SK, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": f"PR#{group}/{repo}/{pr_number}",
            ":prefix": "CHECK#",
        },
    )
    return [
        StatusCheck.from_dict(item)
        for item in response.get("Items", [])
    ]


def _find_by_name(
    group: str,
    repo: str,
    pr_number: int,
    name: str,
    dynamodb_resource: object = None,
) -> StatusCheck | None:
    """Find a status check by name on a PR.

    Args:
        group: The group name.
        repo: The repository name.
        pr_number: The PR number.
        name: The check name.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The StatusCheck or None.
    """
    table = _get_table("status-checks", dynamodb_resource)
    response = table.get_item(
        Key={
            "PK": f"PR#{group}/{repo}/{pr_number}",
            "SK": f"CHECK#{name}",
        },
    )
    item = response.get("Item")
    if not item:
        return None
    return StatusCheck.from_dict(item)


def are_checks_passing(
    group: str,
    repo: str,
    pr_number: int,
    required_checks: list[str],
    dynamodb_resource: object = None,
) -> bool:
    """Check if all required status checks are passing.

    Args:
        group: The group name.
        repo: The repository name.
        pr_number: The PR number.
        required_checks: List of check names that must pass.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        True if all required checks have status SUCCESS.
    """
    if not required_checks:
        return True

    statuses = list_statuses(group, repo, pr_number, dynamodb_resource)
    status_map = {s.name: s.status for s in statuses}

    for check_name in required_checks:
        if check_name not in status_map:
            return False
        if status_map[check_name] != CheckStatus.SUCCESS:
            return False

    return True
