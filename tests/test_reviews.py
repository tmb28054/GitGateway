"""Tests for gitgateway.reviews module."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from gitgateway.reviews import (
    Comment,
    Review,
    ReviewError,
    ReviewNotFoundError,
    ReviewStatus,
    _get_table,
    add_comment,
    count_approvals,
    create_review,
    list_comments,
    list_reviews,
    resolve_thread,
    submit_review,
)


@pytest.fixture
def dynamodb():
    """Create mocked DynamoDB table for comments/reviews."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="gitgateway-comments",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield resource


class TestCommentModel:
    """Tests for Comment dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dictionary."""
        comment = Comment(
            comment_id="c1",
            pr_number=1,
            author="alice",
            body="Nice work!",
            file_path="app.py",
            line_number=10,
        )
        result = comment.to_dict()
        assert result["comment_id"] == "c1"
        assert result["file_path"] == "app.py"
        assert result["line_number"] == 10

    def test_from_dict(self) -> None:
        """Deserializes from dictionary."""
        data = {
            "comment_id": "c2",
            "pr_number": 3,
            "author": "bob",
            "body": "Fix this",
            "side": "old",
            "suggestion": "use x instead",
            "resolved": True,
        }
        comment = Comment.from_dict(data)
        assert comment.author == "bob"
        assert comment.side == "old"
        assert comment.suggestion == "use x instead"
        assert comment.resolved is True

    def test_from_dict_defaults(self) -> None:
        """Handles missing keys."""
        comment = Comment.from_dict({})
        assert comment.side == "new"
        assert comment.resolved is False


class TestReviewModel:
    """Tests for Review dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dictionary."""
        review = Review(
            review_id="r1",
            pr_number=1,
            author="alice",
            status=ReviewStatus.APPROVED,
            body="LGTM",
        )
        result = review.to_dict()
        assert result["status"] == "approved"
        assert result["body"] == "LGTM"

    def test_from_dict(self) -> None:
        """Deserializes from dictionary."""
        data = {
            "review_id": "r2",
            "pr_number": 5,
            "author": "charlie",
            "status": "changes_requested",
            "body": "Needs work",
        }
        review = Review.from_dict(data)
        assert review.status == ReviewStatus.CHANGES_REQUESTED

    def test_from_dict_defaults(self) -> None:
        """Handles missing keys."""
        review = Review.from_dict({})
        assert review.status == ReviewStatus.PENDING


class TestGetTable:
    """Tests for _get_table helper."""

    @mock_aws
    def test_default_resource(self) -> None:
        """Uses default boto3 resource."""
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="gitgateway-comments",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table = _get_table("comments")
        assert table.table_name == "gitgateway-comments"


class TestAddComment:
    """Tests for add_comment function."""

    def test_adds_comment(self, dynamodb) -> None:
        """Creates a comment with auto-assigned ID."""
        comment = add_comment(
            Comment(
                pr_number=1, author="alice", body="Good change",
                group="team", repo="app", file_path="main.py",
                line_number=5,
            ),
            dynamodb,
        )
        assert comment.comment_id != ""
        assert comment.created_at != ""
        assert comment.thread_id == comment.comment_id

    def test_preserves_thread_id(self, dynamodb) -> None:
        """Preserves thread_id if already set (reply to thread)."""
        comment = add_comment(
            Comment(
                pr_number=1, author="bob", body="Reply",
                group="team", repo="app", thread_id="existing-thread",
            ),
            dynamodb,
        )
        assert comment.thread_id == "existing-thread"

    def test_missing_body_raises(self, dynamodb) -> None:
        """Raises when body is empty."""
        with pytest.raises(ReviewError, match="body is required"):
            add_comment(
                Comment(pr_number=1, author="x", group="g", repo="r"),
                dynamodb,
            )

    def test_missing_pr_number_raises(self, dynamodb) -> None:
        """Raises when PR number is missing."""
        with pytest.raises(ReviewError, match="PR number"):
            add_comment(
                Comment(body="text", author="x", group="g", repo="r"),
                dynamodb,
            )


class TestListComments:
    """Tests for list_comments function."""

    def test_lists_all_comments(self, dynamodb) -> None:
        """Lists all comments for a PR."""
        add_comment(
            Comment(pr_number=1, author="a", body="c1", group="t", repo="r"),
            dynamodb,
        )
        add_comment(
            Comment(pr_number=1, author="b", body="c2", group="t", repo="r"),
            dynamodb,
        )
        comments = list_comments("t", "r", 1, dynamodb_resource=dynamodb)
        assert len(comments) == 2

    def test_filters_by_file_path(self, dynamodb) -> None:
        """Filters comments by file path."""
        add_comment(
            Comment(
                pr_number=1, author="a", body="c1",
                group="t", repo="r", file_path="a.py",
            ),
            dynamodb,
        )
        add_comment(
            Comment(
                pr_number=1, author="b", body="c2",
                group="t", repo="r", file_path="b.py",
            ),
            dynamodb,
        )
        comments = list_comments("t", "r", 1, "a.py", dynamodb)
        assert len(comments) == 1
        assert comments[0].file_path == "a.py"

    def test_empty_result(self, dynamodb) -> None:
        """Returns empty list when no comments."""
        comments = list_comments("t", "r", 99, dynamodb_resource=dynamodb)
        assert comments == []


class TestResolveThread:
    """Tests for resolve_thread function."""

    def test_resolves_thread(self, dynamodb) -> None:
        """Marks all comments in thread as resolved."""
        c1 = add_comment(
            Comment(
                pr_number=1, author="alice", body="Issue here",
                group="t", repo="r",
            ),
            dynamodb,
        )
        # Reply in same thread
        add_comment(
            Comment(
                pr_number=1, author="bob", body="Fixed",
                group="t", repo="r", thread_id=c1.thread_id,
            ),
            dynamodb,
        )

        resolved = resolve_thread(
            "t", "r", 1, c1.thread_id, "alice", dynamodb,
        )
        assert len(resolved) == 2
        assert all(c.resolved for c in resolved)

    def test_thread_not_found_raises(self, dynamodb) -> None:
        """Raises when thread doesn't exist."""
        with pytest.raises(ReviewError, match="not found"):
            resolve_thread("t", "r", 1, "ghost-thread", "alice", dynamodb)

    def test_unauthorized_user_raises(self, dynamodb) -> None:
        """Raises when user is not a thread participant."""
        c1 = add_comment(
            Comment(
                pr_number=1, author="alice", body="Comment",
                group="t", repo="r",
            ),
            dynamodb,
        )
        with pytest.raises(ReviewError, match="cannot resolve"):
            resolve_thread("t", "r", 1, c1.thread_id, "stranger", dynamodb)


class TestCreateReview:
    """Tests for create_review function."""

    def test_creates_review(self, dynamodb) -> None:
        """Creates a review with auto-assigned ID."""
        review = create_review(
            Review(
                pr_number=1, author="alice",
                status=ReviewStatus.APPROVED, body="LGTM",
                group="t", repo="r",
            ),
            dynamodb,
        )
        assert review.review_id != ""
        assert review.created_at != ""

    def test_missing_pr_number_raises(self, dynamodb) -> None:
        """Raises when PR number is missing."""
        with pytest.raises(ReviewError, match="PR number"):
            create_review(
                Review(author="x", group="g", repo="r"),
                dynamodb,
            )

    def test_missing_author_raises(self, dynamodb) -> None:
        """Raises when author is missing."""
        with pytest.raises(ReviewError, match="author"):
            create_review(
                Review(pr_number=1, group="g", repo="r"),
                dynamodb,
            )


class TestSubmitReview:
    """Tests for submit_review function."""

    def test_submits_pending_review(self, dynamodb) -> None:
        """Changes review from pending to submitted status."""
        review = create_review(
            Review(
                pr_number=1, author="alice",
                status=ReviewStatus.PENDING, body="",
                group="t", repo="r",
            ),
            dynamodb,
        )
        submitted = submit_review(
            "t", "r", 1, review.review_id,
            ReviewStatus.APPROVED, dynamodb,
        )
        assert submitted.status == ReviewStatus.APPROVED

    def test_review_not_found_raises(self, dynamodb) -> None:
        """Raises when review doesn't exist."""
        with pytest.raises(ReviewNotFoundError, match="not found"):
            submit_review(
                "t", "r", 1, "ghost-review",
                ReviewStatus.APPROVED, dynamodb,
            )


class TestListReviews:
    """Tests for list_reviews function."""

    def test_lists_reviews(self, dynamodb) -> None:
        """Lists all reviews for a PR."""
        create_review(
            Review(
                pr_number=1, author="a",
                status=ReviewStatus.APPROVED, group="t", repo="r",
            ),
            dynamodb,
        )
        create_review(
            Review(
                pr_number=1, author="b",
                status=ReviewStatus.CHANGES_REQUESTED, group="t", repo="r",
            ),
            dynamodb,
        )
        reviews = list_reviews("t", "r", 1, dynamodb)
        assert len(reviews) == 2

    def test_empty_result(self, dynamodb) -> None:
        """Returns empty list when no reviews."""
        reviews = list_reviews("t", "r", 99, dynamodb)
        assert reviews == []


class TestCountApprovals:
    """Tests for count_approvals function."""

    def test_counts_approvals(self, dynamodb) -> None:
        """Counts only approved reviews."""
        create_review(
            Review(
                pr_number=1, author="a",
                status=ReviewStatus.APPROVED, group="t", repo="r",
            ),
            dynamodb,
        )
        create_review(
            Review(
                pr_number=1, author="b",
                status=ReviewStatus.APPROVED, group="t", repo="r",
            ),
            dynamodb,
        )
        create_review(
            Review(
                pr_number=1, author="c",
                status=ReviewStatus.CHANGES_REQUESTED, group="t", repo="r",
            ),
            dynamodb,
        )
        assert count_approvals("t", "r", 1, dynamodb) == 2

    def test_zero_approvals(self, dynamodb) -> None:
        """Returns 0 when no approvals."""
        assert count_approvals("t", "r", 1, dynamodb) == 0


    def test_submits_review_not_first_in_list(self, dynamodb) -> None:
        """Finds and submits review when it's not the first in the list."""
        # Create reviews with controlled timestamps to ensure ordering
        from unittest.mock import patch  # pylint: disable=import-outside-toplevel

        with patch("gitgateway.reviews._now_iso", return_value="2025-01-01T00:00:01Z"):
            first = create_review(
                Review(
                    pr_number=1, author="alice",
                    status=ReviewStatus.PENDING, body="first",
                    group="t", repo="r",
                ),
                dynamodb,
            )

        with patch("gitgateway.reviews._now_iso", return_value="2025-01-01T00:00:02Z"):
            second = create_review(
                Review(
                    pr_number=1, author="bob",
                    status=ReviewStatus.PENDING, body="second",
                    group="t", repo="r",
                ),
                dynamodb,
            )

        # second should be after first in DynamoDB sort order
        # so searching for second should iterate past first
        submitted = submit_review(
            "t", "r", 1, second.review_id,
            ReviewStatus.CHANGES_REQUESTED, dynamodb,
        )
        assert submitted.status == ReviewStatus.CHANGES_REQUESTED
        assert submitted.author == "bob"
