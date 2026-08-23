"""Tests for gitgateway.status_checks module."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from gitgateway.status_checks import (
    CheckStatus,
    StatusCheck,
    StatusCheckError,
    _find_by_name,
    _get_table,
    are_checks_passing,
    list_statuses,
    post_status,
)


@pytest.fixture
def dynamodb():
    """Create mocked DynamoDB table for status checks."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="gitgateway-status-checks",
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


class TestStatusCheckModel:
    """Tests for StatusCheck dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dictionary."""
        check = StatusCheck(
            check_id="c1", name="ci/tests",
            status=CheckStatus.SUCCESS,
            description="All tests passed",
            target_url="https://logs.example.com/123",
            pr_number=1, group="t", repo="r",
        )
        result = check.to_dict()
        assert result["name"] == "ci/tests"
        assert result["status"] == "success"
        assert result["target_url"] == "https://logs.example.com/123"

    def test_from_dict(self) -> None:
        """Deserializes from dictionary."""
        data = {
            "check_id": "c2",
            "name": "sast/bandit",
            "status": "failure",
            "description": "2 issues found",
            "pr_number": 3,
        }
        check = StatusCheck.from_dict(data)
        assert check.status == CheckStatus.FAILURE
        assert check.pr_number == 3

    def test_from_dict_defaults(self) -> None:
        """Handles missing keys."""
        check = StatusCheck.from_dict({})
        assert check.status == CheckStatus.PENDING
        assert check.name == ""


class TestGetTable:
    """Tests for _get_table helper."""

    @mock_aws
    def test_default_resource(self) -> None:
        """Uses default boto3 resource."""
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="gitgateway-status-checks",
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
        table = _get_table("status-checks")
        assert table.table_name == "gitgateway-status-checks"


class TestPostStatus:
    """Tests for post_status function."""

    def test_creates_new_check(self, dynamodb) -> None:
        """Creates a new status check."""
        check = post_status(
            StatusCheck(
                name="ci/tests", status=CheckStatus.PENDING,
                pr_number=1, group="t", repo="r",
            ),
            dynamodb,
        )
        assert check.check_id != ""
        assert check.created_at != ""
        assert check.updated_at == check.created_at

    def test_updates_existing_check(self, dynamodb) -> None:
        """Updates an existing check with same name."""
        post_status(
            StatusCheck(
                name="ci/tests", status=CheckStatus.PENDING,
                pr_number=1, group="t", repo="r",
            ),
            dynamodb,
        )
        updated = post_status(
            StatusCheck(
                name="ci/tests", status=CheckStatus.SUCCESS,
                description="Passed!",
                pr_number=1, group="t", repo="r",
            ),
            dynamodb,
        )
        assert updated.status == CheckStatus.SUCCESS
        assert updated.description == "Passed!"

        # Should still be only one check
        statuses = list_statuses("t", "r", 1, dynamodb)
        assert len(statuses) == 1

    def test_missing_name_raises(self, dynamodb) -> None:
        """Raises when name is missing."""
        with pytest.raises(StatusCheckError, match="name is required"):
            post_status(
                StatusCheck(pr_number=1, group="g", repo="r"),
                dynamodb,
            )

    def test_missing_pr_number_raises(self, dynamodb) -> None:
        """Raises when PR number is missing."""
        with pytest.raises(StatusCheckError, match="PR number"):
            post_status(
                StatusCheck(name="test", group="g", repo="r"),
                dynamodb,
            )


class TestListStatuses:
    """Tests for list_statuses function."""

    def test_lists_all_checks(self, dynamodb) -> None:
        """Lists all status checks for a PR."""
        post_status(
            StatusCheck(
                name="ci/tests", status=CheckStatus.SUCCESS,
                pr_number=1, group="t", repo="r",
            ),
            dynamodb,
        )
        post_status(
            StatusCheck(
                name="sast/bandit", status=CheckStatus.FAILURE,
                pr_number=1, group="t", repo="r",
            ),
            dynamodb,
        )
        statuses = list_statuses("t", "r", 1, dynamodb)
        assert len(statuses) == 2
        names = {s.name for s in statuses}
        assert names == {"ci/tests", "sast/bandit"}

    def test_empty_result(self, dynamodb) -> None:
        """Returns empty list when no checks."""
        statuses = list_statuses("t", "r", 99, dynamodb)
        assert statuses == []


class TestFindByName:
    """Tests for _find_by_name function."""

    def test_finds_existing(self, dynamodb) -> None:
        """Finds a check by name."""
        post_status(
            StatusCheck(
                name="ci", status=CheckStatus.SUCCESS,
                pr_number=1, group="t", repo="r",
            ),
            dynamodb,
        )
        result = _find_by_name("t", "r", 1, "ci", dynamodb)
        assert result is not None
        assert result.name == "ci"

    def test_returns_none_when_missing(self, dynamodb) -> None:
        """Returns None when check doesn't exist."""
        result = _find_by_name("t", "r", 1, "ghost", dynamodb)
        assert result is None


class TestAreChecksPassing:
    """Tests for are_checks_passing function."""

    def test_all_passing(self, dynamodb) -> None:
        """Returns True when all required checks pass."""
        post_status(
            StatusCheck(
                name="ci", status=CheckStatus.SUCCESS,
                pr_number=1, group="t", repo="r",
            ),
            dynamodb,
        )
        post_status(
            StatusCheck(
                name="sast", status=CheckStatus.SUCCESS,
                pr_number=1, group="t", repo="r",
            ),
            dynamodb,
        )
        assert are_checks_passing("t", "r", 1, ["ci", "sast"], dynamodb)

    def test_one_failing(self, dynamodb) -> None:
        """Returns False when any required check fails."""
        post_status(
            StatusCheck(
                name="ci", status=CheckStatus.SUCCESS,
                pr_number=1, group="t", repo="r",
            ),
            dynamodb,
        )
        post_status(
            StatusCheck(
                name="sast", status=CheckStatus.FAILURE,
                pr_number=1, group="t", repo="r",
            ),
            dynamodb,
        )
        assert not are_checks_passing("t", "r", 1, ["ci", "sast"], dynamodb)

    def test_missing_check(self, dynamodb) -> None:
        """Returns False when a required check hasn't reported."""
        post_status(
            StatusCheck(
                name="ci", status=CheckStatus.SUCCESS,
                pr_number=1, group="t", repo="r",
            ),
            dynamodb,
        )
        assert not are_checks_passing(
            "t", "r", 1, ["ci", "sast"], dynamodb,
        )

    def test_empty_required_checks(self, dynamodb) -> None:
        """Returns True when no checks are required."""
        assert are_checks_passing("t", "r", 1, [], dynamodb)

    def test_pending_check_blocks(self, dynamodb) -> None:
        """Returns False when a required check is still pending."""
        post_status(
            StatusCheck(
                name="ci", status=CheckStatus.PENDING,
                pr_number=1, group="t", repo="r",
            ),
            dynamodb,
        )
        assert not are_checks_passing("t", "r", 1, ["ci"], dynamodb)
