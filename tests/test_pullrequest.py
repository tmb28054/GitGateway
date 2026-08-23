"""Tests for gitgateway.pullrequest module."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from gitgateway.pullrequest import (
    InvalidTransitionError,
    MergeStrategy,
    PrNotFoundError,
    PrStatus,
    PullRequest,
    PullRequestError,
    _get_table,
    _next_pr_number,
    close_pr,
    create_pr,
    get_pr,
    list_prs,
    merge_pr,
    update_pr,
)


@pytest.fixture
def dynamodb():
    """Create mocked DynamoDB table for PRs."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="gitgateway-prs",
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


class TestPullRequestModel:
    """Tests for PullRequest dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dictionary."""
        pr = PullRequest(
            number=1,
            title="Fix bug",
            source_branch="fix/bug",
            target_branch="main",
            author="alice",
            group="team",
            repo="app",
        )
        result = pr.to_dict()
        assert result["number"] == 1
        assert result["title"] == "Fix bug"
        assert result["status"] == "open"

    def test_from_dict(self) -> None:
        """Deserializes from dictionary."""
        data = {
            "number": 5,
            "title": "Add feature",
            "source_branch": "feat/new",
            "target_branch": "develop",
            "author": "bob",
            "status": "merged",
            "merge_commit": "abc123",
            "group": "team",
            "repo": "api",
            "reviewers": ["alice"],
        }
        pr = PullRequest.from_dict(data)
        assert pr.number == 5
        assert pr.status == PrStatus.MERGED
        assert pr.merge_commit == "abc123"
        assert pr.reviewers == ["alice"]

    def test_from_dict_defaults(self) -> None:
        """Handles missing keys with defaults."""
        pr = PullRequest.from_dict({})
        assert pr.number == 0
        assert pr.status == PrStatus.OPEN
        assert pr.target_branch == "main"

    def test_roundtrip(self) -> None:
        """Serialization roundtrip is lossless."""
        pr = PullRequest(
            number=3,
            title="Test",
            source_branch="test",
            author="charlie",
            status=PrStatus.CLOSED,
            group="g",
            repo="r",
        )
        assert PullRequest.from_dict(pr.to_dict()).title == "Test"


class TestNextPrNumber:
    """Tests for _next_pr_number function."""

    def test_increments(self, dynamodb) -> None:
        """Auto-increments PR numbers."""
        n1 = _next_pr_number("team", "app", dynamodb)
        n2 = _next_pr_number("team", "app", dynamodb)
        n3 = _next_pr_number("team", "app", dynamodb)
        assert n1 == 1
        assert n2 == 2
        assert n3 == 3

    def test_independent_per_repo(self, dynamodb) -> None:
        """Each repo has its own counter."""
        _next_pr_number("team", "app", dynamodb)
        _next_pr_number("team", "app", dynamodb)
        n = _next_pr_number("team", "other", dynamodb)
        assert n == 1


class TestGetTable:
    """Tests for _get_table helper."""

    @mock_aws
    def test_default_resource(self) -> None:
        """Uses default boto3 resource when none provided."""
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="gitgateway-prs",
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
        table = _get_table("prs")
        assert table.table_name == "gitgateway-prs"


class TestCreatePr:
    """Tests for create_pr function."""

    def test_creates_pr(self, dynamodb) -> None:
        """Creates a PR with auto-assigned number."""
        pr = PullRequest(
            title="New feature",
            source_branch="feat/x",
            author="alice",
            group="team",
            repo="app",
        )
        result = create_pr(pr, dynamodb)
        assert result.number == 1
        assert result.status == PrStatus.OPEN
        assert result.created_at != ""
        assert result.updated_at == result.created_at

    def test_increments_numbers(self, dynamodb) -> None:
        """Successive PRs get incrementing numbers."""
        pr1 = create_pr(
            PullRequest(
                title="PR 1", source_branch="a",
                group="team", repo="app", author="x",
            ),
            dynamodb,
        )
        pr2 = create_pr(
            PullRequest(
                title="PR 2", source_branch="b",
                group="team", repo="app", author="x",
            ),
            dynamodb,
        )
        assert pr1.number == 1
        assert pr2.number == 2

    def test_missing_title_raises(self, dynamodb) -> None:
        """Raises when title is missing."""
        with pytest.raises(PullRequestError, match="title is required"):
            create_pr(
                PullRequest(source_branch="x", group="g", repo="r"),
                dynamodb,
            )

    def test_missing_source_branch_raises(self, dynamodb) -> None:
        """Raises when source branch is missing."""
        with pytest.raises(PullRequestError, match="Source branch"):
            create_pr(
                PullRequest(title="X", group="g", repo="r"),
                dynamodb,
            )

    def test_missing_group_repo_raises(self, dynamodb) -> None:
        """Raises when group/repo is missing."""
        with pytest.raises(PullRequestError, match="Group and repo"):
            create_pr(
                PullRequest(title="X", source_branch="x"),
                dynamodb,
            )

    def test_fork_pr(self, dynamodb) -> None:
        """Creates a PR from a fork."""
        pr = create_pr(
            PullRequest(
                title="Fork PR",
                source_branch="feat",
                source_repo="user/forked",
                group="team",
                repo="app",
                author="bob",
            ),
            dynamodb,
        )
        assert pr.source_repo == "user/forked"


class TestGetPr:
    """Tests for get_pr function."""

    def test_gets_existing_pr(self, dynamodb) -> None:
        """Gets a PR by number."""
        create_pr(
            PullRequest(
                title="Test PR", source_branch="x",
                group="team", repo="app", author="a",
            ),
            dynamodb,
        )
        pr = get_pr("team", "app", 1, dynamodb)
        assert pr.title == "Test PR"

    def test_nonexistent_raises(self, dynamodb) -> None:
        """Raises PrNotFoundError for missing PR."""
        with pytest.raises(PrNotFoundError, match="not found"):
            get_pr("team", "app", 99, dynamodb)


class TestListPrs:
    """Tests for list_prs function."""

    def test_lists_all(self, dynamodb) -> None:
        """Lists all PRs for a repo."""
        create_pr(
            PullRequest(
                title="PR 1", source_branch="a",
                group="team", repo="app", author="x",
            ),
            dynamodb,
        )
        create_pr(
            PullRequest(
                title="PR 2", source_branch="b",
                group="team", repo="app", author="x",
            ),
            dynamodb,
        )
        prs = list_prs("team", "app", dynamodb_resource=dynamodb)
        assert len(prs) == 2

    def test_filter_by_status(self, dynamodb) -> None:
        """Filters PRs by status."""
        pr = create_pr(
            PullRequest(
                title="PR 1", source_branch="a",
                group="team", repo="app", author="x",
            ),
            dynamodb,
        )
        create_pr(
            PullRequest(
                title="PR 2", source_branch="b",
                group="team", repo="app", author="x",
            ),
            dynamodb,
        )
        close_pr("team", "app", pr.number, dynamodb)

        open_prs = list_prs("team", "app", PrStatus.OPEN, dynamodb)
        closed_prs = list_prs("team", "app", PrStatus.CLOSED, dynamodb)
        assert len(open_prs) == 1
        assert len(closed_prs) == 1

    def test_empty_result(self, dynamodb) -> None:
        """Returns empty list when no PRs exist."""
        prs = list_prs("team", "app", dynamodb_resource=dynamodb)
        assert prs == []


class TestUpdatePr:
    """Tests for update_pr function."""

    def test_updates_allowed_fields(self, dynamodb) -> None:
        """Updates title, description, reviewers, target_branch."""
        create_pr(
            PullRequest(
                title="Original", source_branch="x",
                group="team", repo="app", author="a",
            ),
            dynamodb,
        )
        pr = update_pr(
            "team", "app", 1,
            {"title": "Updated", "reviewers": ["bob"]},
            dynamodb,
        )
        assert pr.title == "Updated"
        assert pr.reviewers == ["bob"]
        assert pr.updated_at != ""

    def test_ignores_disallowed_fields(self, dynamodb) -> None:
        """Ignores fields not in allowed set."""
        create_pr(
            PullRequest(
                title="Test", source_branch="x",
                group="team", repo="app", author="a",
            ),
            dynamodb,
        )
        pr = update_pr(
            "team", "app", 1,
            {"status": "merged", "author": "hacker"},
            dynamodb,
        )
        assert pr.status == PrStatus.OPEN
        assert pr.author == "a"

    def test_nonexistent_raises(self, dynamodb) -> None:
        """Raises PrNotFoundError for missing PR."""
        with pytest.raises(PrNotFoundError):
            update_pr("team", "app", 99, {"title": "x"}, dynamodb)


class TestClosePr:
    """Tests for close_pr function."""

    def test_closes_open_pr(self, dynamodb) -> None:
        """Closes an open PR."""
        create_pr(
            PullRequest(
                title="Test", source_branch="x",
                group="team", repo="app", author="a",
            ),
            dynamodb,
        )
        pr = close_pr("team", "app", 1, dynamodb)
        assert pr.status == PrStatus.CLOSED

    def test_cannot_close_merged(self, dynamodb) -> None:
        """Cannot close a merged PR."""
        create_pr(
            PullRequest(
                title="Test", source_branch="x",
                group="team", repo="app", author="a",
            ),
            dynamodb,
        )
        merge_pr("team", "app", 1, "abc", dynamodb_resource=dynamodb)
        with pytest.raises(InvalidTransitionError, match="already merged"):
            close_pr("team", "app", 1, dynamodb)

    def test_nonexistent_raises(self, dynamodb) -> None:
        """Raises PrNotFoundError for missing PR."""
        with pytest.raises(PrNotFoundError):
            close_pr("team", "app", 99, dynamodb)


class TestMergePr:
    """Tests for merge_pr function."""

    def test_merges_open_pr(self, dynamodb) -> None:
        """Merges an open PR."""
        create_pr(
            PullRequest(
                title="Test", source_branch="x",
                group="team", repo="app", author="a",
            ),
            dynamodb,
        )
        pr = merge_pr(
            "team", "app", 1, "sha123",
            strategy=MergeStrategy.SQUASH,
            dynamodb_resource=dynamodb,
        )
        assert pr.status == PrStatus.MERGED
        assert pr.merge_commit == "sha123"
        assert pr.merge_strategy == "squash"

    def test_cannot_merge_closed(self, dynamodb) -> None:
        """Cannot merge a closed PR."""
        create_pr(
            PullRequest(
                title="Test", source_branch="x",
                group="team", repo="app", author="a",
            ),
            dynamodb,
        )
        close_pr("team", "app", 1, dynamodb)
        with pytest.raises(InvalidTransitionError, match="status is closed"):
            merge_pr("team", "app", 1, dynamodb_resource=dynamodb)

    def test_cannot_merge_already_merged(self, dynamodb) -> None:
        """Cannot merge an already merged PR."""
        create_pr(
            PullRequest(
                title="Test", source_branch="x",
                group="team", repo="app", author="a",
            ),
            dynamodb,
        )
        merge_pr("team", "app", 1, "sha1", dynamodb_resource=dynamodb)
        with pytest.raises(InvalidTransitionError, match="status is merged"):
            merge_pr("team", "app", 1, dynamodb_resource=dynamodb)

    def test_default_strategy_is_merge(self, dynamodb) -> None:
        """Default merge strategy is 'merge'."""
        create_pr(
            PullRequest(
                title="Test", source_branch="x",
                group="team", repo="app", author="a",
            ),
            dynamodb,
        )
        pr = merge_pr("team", "app", 1, "sha", dynamodb_resource=dynamodb)
        assert pr.merge_strategy == "merge"

    def test_fast_forward_strategy(self, dynamodb) -> None:
        """Fast-forward merge strategy."""
        create_pr(
            PullRequest(
                title="Test", source_branch="x",
                group="team", repo="app", author="a",
            ),
            dynamodb,
        )
        pr = merge_pr(
            "team", "app", 1, "sha",
            strategy=MergeStrategy.FAST_FORWARD,
            dynamodb_resource=dynamodb,
        )
        assert pr.merge_strategy == "fast-forward"

    def test_nonexistent_raises(self, dynamodb) -> None:
        """Raises PrNotFoundError for missing PR."""
        with pytest.raises(PrNotFoundError):
            merge_pr("team", "app", 99, dynamodb_resource=dynamodb)
