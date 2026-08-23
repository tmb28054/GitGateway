"""Tests for gitgateway.fork module."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from gitgateway.fork import (
    ForkError,
    ForkMetadata,
    _get_table,
    create_fork_metadata,
    get_fork_parent,
    is_fork,
    list_forks,
)


@pytest.fixture
def dynamodb():
    """Create mocked DynamoDB table for repos."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="gitgateway-repos",
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


class TestForkMetadata:
    """Tests for ForkMetadata dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dictionary."""
        meta = ForkMetadata(
            source_group="team", source_repo="app",
            target_group="alice", target_repo="app-fork",
            forked_by="alice",
        )
        result = meta.to_dict()
        assert result["source_group"] == "team"
        assert result["target_repo"] == "app-fork"

    def test_from_dict(self) -> None:
        """Deserializes from dictionary."""
        data = {
            "source_group": "org",
            "source_repo": "lib",
            "target_group": "bob",
            "target_repo": "lib-fork",
            "forked_by": "bob",
            "forked_at": "2025-01-01T00:00:00Z",
        }
        meta = ForkMetadata.from_dict(data)
        assert meta.forked_by == "bob"
        assert meta.forked_at == "2025-01-01T00:00:00Z"

    def test_from_dict_defaults(self) -> None:
        """Handles missing keys."""
        meta = ForkMetadata.from_dict({})
        assert meta.source_group == ""

    def test_full_names(self) -> None:
        """Properties return group/repo format."""
        meta = ForkMetadata(
            source_group="team", source_repo="app",
            target_group="user", target_repo="fork",
        )
        assert meta.source_full_name == "team/app"
        assert meta.target_full_name == "user/fork"


class TestGetTable:
    """Tests for _get_table helper."""

    @mock_aws
    def test_default_resource(self) -> None:
        """Uses default boto3 resource."""
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="gitgateway-repos",
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
        table = _get_table()
        assert table.table_name == "gitgateway-repos"


class TestCreateForkMetadata:
    """Tests for create_fork_metadata function."""

    def test_creates_metadata(self, dynamodb) -> None:
        """Creates fork metadata on both source and target."""
        meta = create_fork_metadata(
            ForkMetadata(
                source_group="team", source_repo="app",
                target_group="alice", target_repo="app-fork",
                forked_by="alice",
            ),
            dynamodb,
        )
        assert meta.forked_at != ""

        # Verify target side
        parent = get_fork_parent("alice", "app-fork", dynamodb)
        assert parent is not None
        assert parent.source_full_name == "team/app"

        # Verify source side
        forks = list_forks("team", "app", dynamodb)
        assert len(forks) == 1
        assert forks[0].target_full_name == "alice/app-fork"

    def test_missing_source_raises(self, dynamodb) -> None:
        """Raises when source is missing."""
        with pytest.raises(ForkError, match="Source group and repo"):
            create_fork_metadata(
                ForkMetadata(
                    target_group="alice", target_repo="fork",
                ),
                dynamodb,
            )

    def test_missing_target_raises(self, dynamodb) -> None:
        """Raises when target is missing."""
        with pytest.raises(ForkError, match="Target group and repo"):
            create_fork_metadata(
                ForkMetadata(
                    source_group="team", source_repo="app",
                ),
                dynamodb,
            )


class TestGetForkParent:
    """Tests for get_fork_parent function."""

    def test_gets_parent(self, dynamodb) -> None:
        """Gets fork parent metadata."""
        create_fork_metadata(
            ForkMetadata(
                source_group="team", source_repo="app",
                target_group="bob", target_repo="my-fork",
                forked_by="bob",
            ),
            dynamodb,
        )
        parent = get_fork_parent("bob", "my-fork", dynamodb)
        assert parent is not None
        assert parent.source_group == "team"

    def test_returns_none_for_non_fork(self, dynamodb) -> None:
        """Returns None for a repo that isn't a fork."""
        parent = get_fork_parent("team", "original", dynamodb)
        assert parent is None


class TestListForks:
    """Tests for list_forks function."""

    def test_lists_forks(self, dynamodb) -> None:
        """Lists all forks of a repo."""
        create_fork_metadata(
            ForkMetadata(
                source_group="team", source_repo="app",
                target_group="alice", target_repo="fork-a",
                forked_by="alice",
            ),
            dynamodb,
        )
        create_fork_metadata(
            ForkMetadata(
                source_group="team", source_repo="app",
                target_group="bob", target_repo="fork-b",
                forked_by="bob",
            ),
            dynamodb,
        )
        forks = list_forks("team", "app", dynamodb)
        assert len(forks) == 2
        targets = {f.target_full_name for f in forks}
        assert "alice/fork-a" in targets
        assert "bob/fork-b" in targets

    def test_empty_when_no_forks(self, dynamodb) -> None:
        """Returns empty list when no forks exist."""
        forks = list_forks("team", "app", dynamodb)
        assert forks == []

    def test_filters_by_source_repo(self, dynamodb) -> None:
        """Only lists forks of the specified repo, not other repos in group."""
        create_fork_metadata(
            ForkMetadata(
                source_group="team", source_repo="app",
                target_group="alice", target_repo="fork-app",
                forked_by="alice",
            ),
            dynamodb,
        )
        create_fork_metadata(
            ForkMetadata(
                source_group="team", source_repo="other",
                target_group="bob", target_repo="fork-other",
                forked_by="bob",
            ),
            dynamodb,
        )
        forks = list_forks("team", "app", dynamodb)
        assert len(forks) == 1
        assert forks[0].target_repo == "fork-app"


class TestIsFork:
    """Tests for is_fork function."""

    def test_returns_true_for_fork(self, dynamodb) -> None:
        """Returns True for a forked repo."""
        create_fork_metadata(
            ForkMetadata(
                source_group="team", source_repo="app",
                target_group="alice", target_repo="my-fork",
                forked_by="alice",
            ),
            dynamodb,
        )
        assert is_fork("alice", "my-fork", dynamodb) is True

    def test_returns_false_for_non_fork(self, dynamodb) -> None:
        """Returns False for a non-forked repo."""
        assert is_fork("team", "original", dynamodb) is False
