"""Tests for gitgateway.mirror module."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from gitgateway.mirror import (
    MirrorConfig,
    MirrorError,
    _get_table,
    configure_mirror,
    delete_mirror_config,
    get_mirror_config,
    should_mirror_branch,
    update_sync_status,
)


@pytest.fixture
def dynamodb():
    """Create mocked DynamoDB table for repos (mirrors stored there)."""
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


class TestMirrorConfig:
    """Tests for MirrorConfig dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dictionary."""
        config = MirrorConfig(
            target_url="https://github.com/user/repo.git",
            credential_arn="arn:aws:secretsmanager:us-east-1:123:secret:key",
            enabled=True,
            branches=["main", "develop"],
            group="team", repo="app",
        )
        result = config.to_dict()
        assert result["target_url"] == "https://github.com/user/repo.git"
        assert result["branches"] == ["main", "develop"]

    def test_from_dict(self) -> None:
        """Deserializes from dictionary."""
        data = {
            "target_url": "git@gitlab.com:org/repo.git",
            "enabled": False,
            "last_sync_status": "failure",
            "error_message": "auth failed",
        }
        config = MirrorConfig.from_dict(data)
        assert config.enabled is False
        assert config.last_sync_status == "failure"
        assert config.error_message == "auth failed"

    def test_from_dict_defaults(self) -> None:
        """Handles missing keys."""
        config = MirrorConfig.from_dict({})
        assert config.enabled is True
        assert config.branches == []
        assert config.target_url == ""


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


class TestConfigureMirror:
    """Tests for configure_mirror function."""

    def test_configures_mirror(self, dynamodb) -> None:
        """Saves mirror configuration."""
        config = configure_mirror(
            MirrorConfig(
                target_url="https://github.com/u/r.git",
                group="team", repo="app",
            ),
            dynamodb,
        )
        assert config.target_url == "https://github.com/u/r.git"

    def test_missing_url_raises(self, dynamodb) -> None:
        """Raises when target URL is missing."""
        with pytest.raises(MirrorError, match="Target URL"):
            configure_mirror(
                MirrorConfig(group="g", repo="r"),
                dynamodb,
            )

    def test_missing_group_raises(self, dynamodb) -> None:
        """Raises when group/repo is missing."""
        with pytest.raises(MirrorError, match="Group and repo"):
            configure_mirror(
                MirrorConfig(target_url="https://x.com/r.git"),
                dynamodb,
            )


class TestGetMirrorConfig:
    """Tests for get_mirror_config function."""

    def test_gets_config(self, dynamodb) -> None:
        """Gets saved mirror configuration."""
        configure_mirror(
            MirrorConfig(
                target_url="https://github.com/u/r.git",
                group="team", repo="app",
                branches=["main"],
            ),
            dynamodb,
        )
        config = get_mirror_config("team", "app", dynamodb)
        assert config is not None
        assert config.target_url == "https://github.com/u/r.git"
        assert config.branches == ["main"]

    def test_returns_none_when_missing(self, dynamodb) -> None:
        """Returns None when no config exists."""
        config = get_mirror_config("ghost", "repo", dynamodb)
        assert config is None


class TestDeleteMirrorConfig:
    """Tests for delete_mirror_config function."""

    def test_deletes_config(self, dynamodb) -> None:
        """Deletes mirror configuration."""
        configure_mirror(
            MirrorConfig(
                target_url="https://x.com/r.git",
                group="team", repo="app",
            ),
            dynamodb,
        )
        delete_mirror_config("team", "app", dynamodb)
        assert get_mirror_config("team", "app", dynamodb) is None

    def test_no_error_on_missing(self, dynamodb) -> None:
        """Doesn't raise when config doesn't exist."""
        delete_mirror_config("ghost", "repo", dynamodb)


class TestUpdateSyncStatus:
    """Tests for update_sync_status function."""

    def test_updates_success(self, dynamodb) -> None:
        """Updates status to success."""
        configure_mirror(
            MirrorConfig(
                target_url="https://x.com/r.git",
                group="team", repo="app",
            ),
            dynamodb,
        )
        result = update_sync_status("team", "app", "success", dynamodb_resource=dynamodb)
        assert result is not None
        assert result.last_sync_status == "success"
        assert result.last_sync_time != ""
        assert result.error_message == ""

    def test_updates_failure(self, dynamodb) -> None:
        """Updates status to failure with error message."""
        configure_mirror(
            MirrorConfig(
                target_url="https://x.com/r.git",
                group="team", repo="app",
            ),
            dynamodb,
        )
        result = update_sync_status(
            "team", "app", "failure",
            error_message="Authentication failed",
            dynamodb_resource=dynamodb,
        )
        assert result is not None
        assert result.last_sync_status == "failure"
        assert result.error_message == "Authentication failed"

    def test_returns_none_when_no_config(self, dynamodb) -> None:
        """Returns None when mirror not configured."""
        result = update_sync_status("ghost", "repo", "success", dynamodb_resource=dynamodb)
        assert result is None


class TestShouldMirrorBranch:
    """Tests for should_mirror_branch function."""

    def test_all_branches_when_empty(self) -> None:
        """Mirrors all branches when list is empty."""
        config = MirrorConfig(enabled=True, branches=[])
        assert should_mirror_branch(config, "main") is True
        assert should_mirror_branch(config, "feature/x") is True

    def test_specific_branches(self) -> None:
        """Only mirrors specified branches."""
        config = MirrorConfig(enabled=True, branches=["main", "develop"])
        assert should_mirror_branch(config, "main") is True
        assert should_mirror_branch(config, "develop") is True
        assert should_mirror_branch(config, "feature/x") is False

    def test_disabled_mirror(self) -> None:
        """Returns False when mirror is disabled."""
        config = MirrorConfig(enabled=False, branches=[])
        assert should_mirror_branch(config, "main") is False
