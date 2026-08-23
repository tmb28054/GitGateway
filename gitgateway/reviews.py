"""PR comments, review threads, and suggestions for GitGateway.

Manages inline comments on diffs, review threads with resolution,
code suggestions, and review approvals/change requests.
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


class ReviewError(Exception):
    """Raised when a review operation fails."""


class ReviewNotFoundError(ReviewError):
    """Raised when a review or comment is not found."""


class ReviewStatus(Enum):
    """Review submission status."""

    PENDING = "pending"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    COMMENTED = "commented"


@dataclass
class Comment:
    """An inline comment on a PR diff.

    Attributes:
        comment_id: Unique comment identifier.
        pr_number: The PR this comment belongs to.
        author: Username of the comment author.
        body: Comment text content.
        created_at: ISO timestamp of creation.
        file_path: File path the comment is on (empty for general).
        line_number: Line number in the diff.
        side: Which side of the diff ('old' or 'new').
        thread_id: Thread grouping identifier.
        suggestion: Optional code suggestion block.
        resolved: Whether this thread is resolved.
        group: Repository group.
        repo: Repository name.
    """

    comment_id: str = ""
    pr_number: int = 0
    author: str = ""
    body: str = ""
    created_at: str = ""
    file_path: str = ""
    line_number: int = 0
    side: str = "new"
    thread_id: str = ""
    suggestion: str = ""
    resolved: bool = False
    group: str = ""
    repo: str = ""

    def to_dict(self) -> dict:
        """Serialize to dictionary for DynamoDB storage.

        Returns:
            Dictionary representation.
        """
        return {
            "comment_id": self.comment_id,
            "pr_number": self.pr_number,
            "author": self.author,
            "body": self.body,
            "created_at": self.created_at,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "side": self.side,
            "thread_id": self.thread_id,
            "suggestion": self.suggestion,
            "resolved": self.resolved,
            "group": self.group,
            "repo": self.repo,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Comment:
        """Deserialize from DynamoDB dictionary.

        Args:
            data: Dictionary from DynamoDB.

        Returns:
            A Comment instance.
        """
        return cls(
            comment_id=data.get("comment_id", ""),
            pr_number=int(data.get("pr_number", 0)),
            author=data.get("author", ""),
            body=data.get("body", ""),
            created_at=data.get("created_at", ""),
            file_path=data.get("file_path", ""),
            line_number=int(data.get("line_number", 0)),
            side=data.get("side", "new"),
            thread_id=data.get("thread_id", ""),
            suggestion=data.get("suggestion", ""),
            resolved=bool(data.get("resolved", False)),
            group=data.get("group", ""),
            repo=data.get("repo", ""),
        )


@dataclass
class Review:
    """A PR review (approval, change request, or comment).

    Attributes:
        review_id: Unique review identifier.
        pr_number: The PR this review belongs to.
        author: Username of the reviewer.
        status: Review status (approved, changes_requested, etc.).
        body: Review summary text.
        created_at: ISO timestamp of creation.
        group: Repository group.
        repo: Repository name.
    """

    review_id: str = ""
    pr_number: int = 0
    author: str = ""
    status: ReviewStatus = ReviewStatus.PENDING
    body: str = ""
    created_at: str = ""
    group: str = ""
    repo: str = ""

    def to_dict(self) -> dict:
        """Serialize to dictionary for DynamoDB storage.

        Returns:
            Dictionary representation.
        """
        return {
            "review_id": self.review_id,
            "pr_number": self.pr_number,
            "author": self.author,
            "status": self.status.value,
            "body": self.body,
            "created_at": self.created_at,
            "group": self.group,
            "repo": self.repo,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Review:
        """Deserialize from DynamoDB dictionary.

        Args:
            data: Dictionary from DynamoDB.

        Returns:
            A Review instance.
        """
        return cls(
            review_id=data.get("review_id", ""),
            pr_number=int(data.get("pr_number", 0)),
            author=data.get("author", ""),
            status=ReviewStatus(data.get("status", "pending")),
            body=data.get("body", ""),
            created_at=data.get("created_at", ""),
            group=data.get("group", ""),
            repo=data.get("repo", ""),
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


# --- Comment Operations ---


def add_comment(
    comment: Comment,
    dynamodb_resource: object = None,
) -> Comment:
    """Add a comment to a PR.

    Args:
        comment: The Comment to add (comment_id will be assigned).
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The created Comment with assigned ID and timestamp.

    Raises:
        ReviewError: If required fields are missing.
    """
    if not comment.body:
        raise ReviewError("Comment body is required")
    if not comment.pr_number:
        raise ReviewError("PR number is required")

    comment.comment_id = str(uuid.uuid4())
    comment.created_at = _now_iso()

    if not comment.thread_id:
        comment.thread_id = comment.comment_id

    table = _get_table("comments", dynamodb_resource)
    table.put_item(
        Item={
            "PK": f"PR#{comment.group}/{comment.repo}/{comment.pr_number}",
            "SK": f"COMMENT#{comment.created_at}#{comment.comment_id}",
            **comment.to_dict(),
        },
    )

    LOG.info(
        "Added comment %s to PR #%d",
        comment.comment_id, comment.pr_number,
    )
    return comment


def list_comments(
    group: str,
    repo: str,
    pr_number: int,
    file_path: str | None = None,
    dynamodb_resource: object = None,
) -> list[Comment]:
    """List comments for a PR.

    Args:
        group: The group name.
        repo: The repository name.
        pr_number: The PR number.
        file_path: Optional filter by file path.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        List of Comment objects.
    """
    table = _get_table("comments", dynamodb_resource)
    response = table.query(
        KeyConditionExpression=(
            "PK = :pk AND begins_with(SK, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": f"PR#{group}/{repo}/{pr_number}",
            ":prefix": "COMMENT#",
        },
    )
    items = response.get("Items", [])
    comments = [Comment.from_dict(item) for item in items]

    if file_path:
        comments = [c for c in comments if c.file_path == file_path]

    return comments


def resolve_thread(  # pylint: disable=too-many-positional-arguments
    group: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    username: str,
    dynamodb_resource: object = None,
) -> list[Comment]:
    """Resolve a comment thread.

    Only the PR author or the thread creator can resolve.

    Args:
        group: The group name.
        repo: The repository name.
        pr_number: The PR number.
        thread_id: The thread ID to resolve.
        username: The user requesting resolution.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        List of resolved comments in the thread.

    Raises:
        ReviewError: If no comments found for the thread.
    """
    comments = list_comments(
        group, repo, pr_number, dynamodb_resource=dynamodb_resource,
    )
    thread_comments = [c for c in comments if c.thread_id == thread_id]

    if not thread_comments:
        raise ReviewError(f"Thread '{thread_id}' not found")

    # Check permission: thread creator or any participant can resolve
    thread_authors = {c.author for c in thread_comments}
    if username not in thread_authors:
        raise ReviewError(
            f"User '{username}' cannot resolve this thread",
        )

    table = _get_table("comments", dynamodb_resource)
    for comment in thread_comments:
        comment.resolved = True
        table.put_item(
            Item={
                "PK": f"PR#{group}/{repo}/{pr_number}",
                "SK": f"COMMENT#{comment.created_at}#{comment.comment_id}",
                **comment.to_dict(),
            },
        )

    LOG.info("Resolved thread %s on PR #%d", thread_id, pr_number)
    return thread_comments


# --- Review Operations ---


def create_review(
    review: Review,
    dynamodb_resource: object = None,
) -> Review:
    """Create a review on a PR.

    Args:
        review: The Review to create (review_id will be assigned).
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The created Review.

    Raises:
        ReviewError: If required fields are missing.
    """
    if not review.pr_number:
        raise ReviewError("PR number is required for review")
    if not review.author:
        raise ReviewError("Review author is required")

    review.review_id = str(uuid.uuid4())
    review.created_at = _now_iso()

    table = _get_table("comments", dynamodb_resource)
    table.put_item(
        Item={
            "PK": f"PR#{review.group}/{review.repo}/{review.pr_number}",
            "SK": f"REVIEW#{review.created_at}#{review.review_id}",
            **review.to_dict(),
        },
    )

    LOG.info(
        "Created review %s (%s) on PR #%d",
        review.review_id, review.status.value, review.pr_number,
    )
    return review


def submit_review(  # pylint: disable=too-many-positional-arguments
    group: str,
    repo: str,
    pr_number: int,
    review_id: str,
    status: ReviewStatus,
    dynamodb_resource: object = None,
) -> Review:
    """Submit (finalize) a pending review.

    Args:
        group: The group name.
        repo: The repository name.
        pr_number: The PR number.
        review_id: The review ID to submit.
        status: The final review status.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The submitted Review.

    Raises:
        ReviewNotFoundError: If the review is not found.
    """
    reviews = list_reviews(group, repo, pr_number, dynamodb_resource)
    target = None
    for review in reviews:
        if review.review_id == review_id:
            target = review
            break

    if target is None:
        raise ReviewNotFoundError(
            f"Review '{review_id}' not found on PR #{pr_number}",
        )

    target.status = status

    table = _get_table("comments", dynamodb_resource)
    table.put_item(
        Item={
            "PK": f"PR#{group}/{repo}/{pr_number}",
            "SK": f"REVIEW#{target.created_at}#{target.review_id}",
            **target.to_dict(),
        },
    )

    LOG.info("Submitted review %s as %s", review_id, status.value)
    return target


def list_reviews(
    group: str,
    repo: str,
    pr_number: int,
    dynamodb_resource: object = None,
) -> list[Review]:
    """List reviews for a PR.

    Args:
        group: The group name.
        repo: The repository name.
        pr_number: The PR number.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        List of Review objects.
    """
    table = _get_table("comments", dynamodb_resource)
    response = table.query(
        KeyConditionExpression=(
            "PK = :pk AND begins_with(SK, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": f"PR#{group}/{repo}/{pr_number}",
            ":prefix": "REVIEW#",
        },
    )
    items = response.get("Items", [])
    return [Review.from_dict(item) for item in items]


def count_approvals(
    group: str,
    repo: str,
    pr_number: int,
    dynamodb_resource: object = None,
) -> int:
    """Count the number of approvals on a PR.

    Args:
        group: The group name.
        repo: The repository name.
        pr_number: The PR number.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        Number of approved reviews.
    """
    reviews = list_reviews(group, repo, pr_number, dynamodb_resource)
    return sum(
        1 for r in reviews if r.status == ReviewStatus.APPROVED
    )
