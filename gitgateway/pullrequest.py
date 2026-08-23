"""Pull Request data model and CRUD operations for GitGateway.

Manages the full PR lifecycle: create, list, update, merge, close.
Supports fork PRs, merge strategies, and auto-incrementing PR numbers.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

import boto3

LOG = logging.getLogger(__name__)

TABLE_PREFIX = "gitgateway"


class PullRequestError(Exception):
    """Raised when a PR operation fails."""


class PrNotFoundError(PullRequestError):
    """Raised when a requested PR is not found."""


class InvalidTransitionError(PullRequestError):
    """Raised when a PR status transition is invalid."""


class MergeBlockedError(PullRequestError):
    """Raised when merge pre-checks fail."""


class PrStatus(Enum):
    """Pull request status."""

    OPEN = "open"
    MERGED = "merged"
    CLOSED = "closed"


class MergeStrategy(Enum):
    """Merge strategy options."""

    MERGE = "merge"
    SQUASH = "squash"
    FAST_FORWARD = "fast-forward"


@dataclass
class PullRequest:
    """Represents a pull request.

    Attributes:
        number: Auto-incremented PR number within the repo.
        title: PR title.
        description: PR description/body.
        source_branch: Branch to merge from.
        target_branch: Branch to merge into.
        source_repo: Source repo for fork PRs (group/name format).
        author: Username of the PR creator.
        status: Current PR status.
        created_at: ISO timestamp of creation.
        updated_at: ISO timestamp of last update.
        reviewers: List of requested reviewer usernames.
        merge_commit: SHA of the merge commit (set after merge).
        merge_strategy: Strategy used for merge.
        group: Target repo group.
        repo: Target repo name.
    """

    number: int = 0
    title: str = ""
    description: str = ""
    source_branch: str = ""
    target_branch: str = "main"
    source_repo: str = ""
    author: str = ""
    status: PrStatus = PrStatus.OPEN
    created_at: str = ""
    updated_at: str = ""
    reviewers: list[str] = field(default_factory=list)
    merge_commit: str = ""
    merge_strategy: str = ""
    group: str = ""
    repo: str = ""

    def to_dict(self) -> dict:
        """Serialize to a dictionary for DynamoDB storage.

        Returns:
            Dictionary representation of the PR.
        """
        return {
            "number": self.number,
            "title": self.title,
            "description": self.description,
            "source_branch": self.source_branch,
            "target_branch": self.target_branch,
            "source_repo": self.source_repo,
            "author": self.author,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "reviewers": self.reviewers,
            "merge_commit": self.merge_commit,
            "merge_strategy": self.merge_strategy,
            "group": self.group,
            "repo": self.repo,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PullRequest:
        """Deserialize from a DynamoDB dictionary.

        Args:
            data: Dictionary from DynamoDB.

        Returns:
            A PullRequest instance.
        """
        return cls(
            number=int(data.get("number", 0)),
            title=data.get("title", ""),
            description=data.get("description", ""),
            source_branch=data.get("source_branch", ""),
            target_branch=data.get("target_branch", "main"),
            source_repo=data.get("source_repo", ""),
            author=data.get("author", ""),
            status=PrStatus(data.get("status", "open")),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            reviewers=list(data.get("reviewers", [])),
            merge_commit=data.get("merge_commit", ""),
            merge_strategy=data.get("merge_strategy", ""),
            group=data.get("group", ""),
            repo=data.get("repo", ""),
        )


def _get_table(table_suffix: str, dynamodb_resource: object = None):
    """Get a DynamoDB table resource.

    Args:
        table_suffix: Table name suffix.
        dynamodb_resource: Optional boto3 DynamoDB resource for testing.

    Returns:
        A DynamoDB Table resource.
    """
    if dynamodb_resource is None:
        dynamodb_resource = boto3.resource("dynamodb")
    return dynamodb_resource.Table(f"{TABLE_PREFIX}-{table_suffix}")


def _now_iso() -> str:
    """Get current time as ISO string.

    Returns:
        ISO formatted timestamp.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _next_pr_number(
    group: str,
    repo: str,
    dynamodb_resource: object = None,
) -> int:
    """Get the next PR number using atomic counter.

    Args:
        group: The group name.
        repo: The repository name.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The next PR number.
    """
    table = _get_table("prs", dynamodb_resource)
    response = table.update_item(
        Key={
            "PK": f"REPO#{group}/{repo}",
            "SK": "COUNTER",
        },
        UpdateExpression="ADD #cnt :inc",
        ExpressionAttributeNames={"#cnt": "counter"},
        ExpressionAttributeValues={":inc": 1},
        ReturnValues="UPDATED_NEW",
    )
    return int(response["Attributes"]["counter"])


def create_pr(
    pr: PullRequest,
    dynamodb_resource: object = None,
) -> PullRequest:
    """Create a new pull request.

    Assigns an auto-incremented number and sets timestamps.

    Args:
        pr: The PullRequest to create (number will be assigned).
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The created PullRequest with assigned number.

    Raises:
        PullRequestError: If required fields are missing.
    """
    if not pr.title:
        raise PullRequestError("PR title is required")
    if not pr.source_branch:
        raise PullRequestError("Source branch is required")
    if not pr.group or not pr.repo:
        raise PullRequestError("Group and repo are required")

    # Assign number
    pr.number = _next_pr_number(pr.group, pr.repo, dynamodb_resource)
    pr.status = PrStatus.OPEN
    pr.created_at = _now_iso()
    pr.updated_at = pr.created_at

    table = _get_table("prs", dynamodb_resource)
    table.put_item(
        Item={
            "PK": f"REPO#{pr.group}/{pr.repo}",
            "SK": f"PR#{pr.number:06d}",
            **pr.to_dict(),
        },
    )

    LOG.info("Created PR #%d: %s/%s", pr.number, pr.group, pr.repo)
    return pr


def get_pr(
    group: str,
    repo: str,
    number: int,
    dynamodb_resource: object = None,
) -> PullRequest:
    """Get a pull request by number.

    Args:
        group: The group name.
        repo: The repository name.
        number: The PR number.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The PullRequest.

    Raises:
        PrNotFoundError: If the PR does not exist.
    """
    table = _get_table("prs", dynamodb_resource)
    response = table.get_item(
        Key={
            "PK": f"REPO#{group}/{repo}",
            "SK": f"PR#{number:06d}",
        },
    )
    item = response.get("Item")
    if not item:
        raise PrNotFoundError(
            f"PR #{number} not found in {group}/{repo}",
        )
    return PullRequest.from_dict(item)


def list_prs(
    group: str,
    repo: str,
    status: PrStatus | None = None,
    dynamodb_resource: object = None,
) -> list[PullRequest]:
    """List pull requests for a repository.

    Args:
        group: The group name.
        repo: The repository name.
        status: Optional status filter.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        List of PullRequest objects.
    """
    table = _get_table("prs", dynamodb_resource)
    response = table.query(
        KeyConditionExpression=(
            "PK = :pk AND begins_with(SK, :sk_prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": f"REPO#{group}/{repo}",
            ":sk_prefix": "PR#",
        },
    )
    items = response.get("Items", [])
    prs = [PullRequest.from_dict(item) for item in items]

    if status:
        prs = [pr for pr in prs if pr.status == status]

    return prs


def update_pr(
    group: str,
    repo: str,
    number: int,
    updates: dict,
    dynamodb_resource: object = None,
) -> PullRequest:
    """Update a pull request's fields.

    Args:
        group: The group name.
        repo: The repository name.
        number: The PR number.
        updates: Dictionary of fields to update.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The updated PullRequest.

    Raises:
        PrNotFoundError: If the PR does not exist.
    """
    pr = get_pr(group, repo, number, dynamodb_resource)

    allowed_fields = {
        "title", "description", "reviewers", "target_branch",
    }
    for key, value in updates.items():
        if key in allowed_fields:
            setattr(pr, key, value)

    pr.updated_at = _now_iso()

    table = _get_table("prs", dynamodb_resource)
    table.put_item(
        Item={
            "PK": f"REPO#{group}/{repo}",
            "SK": f"PR#{number:06d}",
            **pr.to_dict(),
        },
    )

    LOG.info("Updated PR #%d: %s/%s", number, group, repo)
    return pr


def close_pr(
    group: str,
    repo: str,
    number: int,
    dynamodb_resource: object = None,
) -> PullRequest:
    """Close a pull request without merging.

    Args:
        group: The group name.
        repo: The repository name.
        number: The PR number.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The closed PullRequest.

    Raises:
        PrNotFoundError: If the PR does not exist.
        InvalidTransitionError: If the PR is already merged.
    """
    pr = get_pr(group, repo, number, dynamodb_resource)

    if pr.status == PrStatus.MERGED:
        raise InvalidTransitionError(
            f"Cannot close PR #{number}: already merged",
        )

    pr.status = PrStatus.CLOSED
    pr.updated_at = _now_iso()

    table = _get_table("prs", dynamodb_resource)
    table.put_item(
        Item={
            "PK": f"REPO#{group}/{repo}",
            "SK": f"PR#{number:06d}",
            **pr.to_dict(),
        },
    )

    LOG.info("Closed PR #%d: %s/%s", number, group, repo)
    return pr


def merge_pr(  # pylint: disable=too-many-positional-arguments
    group: str,
    repo: str,
    number: int,
    merge_commit: str = "",
    strategy: MergeStrategy = MergeStrategy.MERGE,
    dynamodb_resource: object = None,
) -> PullRequest:
    """Merge a pull request.

    Args:
        group: The group name.
        repo: The repository name.
        number: The PR number.
        merge_commit: SHA of the merge commit.
        strategy: Merge strategy used.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The merged PullRequest.

    Raises:
        PrNotFoundError: If the PR does not exist.
        InvalidTransitionError: If PR is not open.
    """
    pr = get_pr(group, repo, number, dynamodb_resource)

    if pr.status != PrStatus.OPEN:
        raise InvalidTransitionError(
            f"Cannot merge PR #{number}: status is {pr.status.value}",
        )

    pr.status = PrStatus.MERGED
    pr.merge_commit = merge_commit
    pr.merge_strategy = strategy.value
    pr.updated_at = _now_iso()

    table = _get_table("prs", dynamodb_resource)
    table.put_item(
        Item={
            "PK": f"REPO#{group}/{repo}",
            "SK": f"PR#{number:06d}",
            **pr.to_dict(),
        },
    )

    LOG.info("Merged PR #%d: %s/%s (strategy=%s)", number, group, repo, strategy.value)
    return pr
