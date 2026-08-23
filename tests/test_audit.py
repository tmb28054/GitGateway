"""Tests for gitgateway.audit module."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from gitgateway.audit import (
    AuditEvent,
    _compute_ttl,
    _get_table,
    audit,
    get_audit_log,
    get_user_audit_log,
    log_event,
)


@pytest.fixture
def dynamodb():
    """Create mocked DynamoDB table for audit."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="gitgateway-audit",
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


class TestAuditEvent:
    """Tests for AuditEvent dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dictionary."""
        event = AuditEvent(
            event_id="e1", timestamp="2025-01-01T00:00:00Z",
            actor="alice", action="repo.created",
            resource="team/app", details={"name": "app"},
            ip_address="1.2.3.4", ttl=9999999,
        )
        result = event.to_dict()
        assert result["actor"] == "alice"
        assert result["action"] == "repo.created"
        assert '"name": "app"' in result["details"]
        assert result["ttl"] == 9999999

    def test_to_dict_no_ttl(self) -> None:
        """Omits TTL when zero."""
        event = AuditEvent(actor="x", action="y", resource="z")
        result = event.to_dict()
        assert "ttl" not in result

    def test_from_dict(self) -> None:
        """Deserializes from dictionary."""
        data = {
            "event_id": "e2", "timestamp": "2025-01-02T00:00:00Z",
            "actor": "bob", "action": "push",
            "resource": "team/api",
            "details": '{"branch": "main"}',
            "ip_address": "5.6.7.8",
            "ttl": 1234567,
        }
        event = AuditEvent.from_dict(data)
        assert event.actor == "bob"
        assert event.details == {"branch": "main"}
        assert event.ttl == 1234567

    def test_from_dict_invalid_details_json(self) -> None:
        """Handles invalid JSON in details field."""
        data = {"details": "not json {{{"}
        event = AuditEvent.from_dict(data)
        assert event.details == {}

    def test_from_dict_dict_details(self) -> None:
        """Handles details that are already a dict."""
        data = {"details": {"key": "value"}}
        event = AuditEvent.from_dict(data)
        assert event.details == {"key": "value"}

    def test_from_dict_defaults(self) -> None:
        """Handles missing keys."""
        event = AuditEvent.from_dict({})
        assert event.actor == ""
        assert event.ttl == 0


class TestGetTable:
    """Tests for _get_table helper."""

    @mock_aws
    def test_default_resource(self) -> None:
        """Uses default boto3 resource."""
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="gitgateway-audit",
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
        assert table.table_name == "gitgateway-audit"


class TestComputeTtl:
    """Tests for _compute_ttl function."""

    def test_default_90_days(self) -> None:
        """Default TTL is ~90 days from now."""
        import time  # pylint: disable=import-outside-toplevel

        ttl = _compute_ttl()
        expected = int(time.time()) + (90 * 86400)
        assert abs(ttl - expected) < 2  # Within 2 seconds

    def test_custom_days(self) -> None:
        """Custom TTL days."""
        import time  # pylint: disable=import-outside-toplevel

        ttl = _compute_ttl(30)
        expected = int(time.time()) + (30 * 86400)
        assert abs(ttl - expected) < 2


class TestLogEvent:
    """Tests for log_event function."""

    def test_logs_event(self, dynamodb) -> None:
        """Logs an event with all fields."""
        event = log_event(
            actor="alice", action="repo.created",
            resource="team/app",
            details={"description": "New app"},
            ip_address="10.0.0.1",
            dynamodb_resource=dynamodb,
        )
        assert event.event_id != ""
        assert event.timestamp != ""
        assert event.actor == "alice"
        assert event.ttl > 0

    def test_logs_event_no_ttl(self, dynamodb) -> None:
        """Logs event with no TTL (permanent)."""
        event = log_event(
            actor="bob", action="push",
            resource="team/api",
            ttl_days=0,
            dynamodb_resource=dynamodb,
        )
        assert event.ttl == 0

    def test_logs_event_no_details(self, dynamodb) -> None:
        """Logs event without details."""
        event = log_event(
            actor="charlie", action="access.granted",
            resource="team",
            dynamodb_resource=dynamodb,
        )
        assert event.details is None


class TestGetAuditLog:
    """Tests for get_audit_log function."""

    def test_gets_events_for_resource(self, dynamodb) -> None:
        """Gets all events for a resource."""
        log_event(
            actor="alice", action="repo.created",
            resource="team/app", dynamodb_resource=dynamodb,
        )
        log_event(
            actor="bob", action="push",
            resource="team/app", dynamodb_resource=dynamodb,
        )
        log_event(
            actor="alice", action="repo.created",
            resource="other/repo", dynamodb_resource=dynamodb,
        )

        events = get_audit_log("team/app", dynamodb_resource=dynamodb)
        assert len(events) == 2

    def test_filter_by_start_time(self, dynamodb) -> None:
        """Filters events after start_time."""
        from unittest.mock import patch  # pylint: disable=import-outside-toplevel

        with patch(
            "gitgateway.audit._now_iso",
            return_value="2025-01-01T00:00:00Z",
        ):
            log_event(
                actor="a", action="old",
                resource="r", dynamodb_resource=dynamodb,
            )

        with patch(
            "gitgateway.audit._now_iso",
            return_value="2025-06-01T00:00:00Z",
        ):
            log_event(
                actor="b", action="new",
                resource="r", dynamodb_resource=dynamodb,
            )

        events = get_audit_log(
            "r", start_time="2025-03-01T00:00:00Z",
            dynamodb_resource=dynamodb,
        )
        assert len(events) == 1
        assert events[0].action == "new"

    def test_filter_by_end_time(self, dynamodb) -> None:
        """Filters events before end_time."""
        from unittest.mock import patch  # pylint: disable=import-outside-toplevel

        with patch(
            "gitgateway.audit._now_iso",
            return_value="2025-01-01T00:00:00Z",
        ):
            log_event(
                actor="a", action="old",
                resource="r", dynamodb_resource=dynamodb,
            )

        with patch(
            "gitgateway.audit._now_iso",
            return_value="2025-06-01T00:00:00Z",
        ):
            log_event(
                actor="b", action="new",
                resource="r", dynamodb_resource=dynamodb,
            )

        events = get_audit_log(
            "r", end_time="2025-03-01T00:00:00Z",
            dynamodb_resource=dynamodb,
        )
        assert len(events) == 1
        assert events[0].action == "old"

    def test_empty_result(self, dynamodb) -> None:
        """Returns empty list for resource with no events."""
        events = get_audit_log("ghost/repo", dynamodb_resource=dynamodb)
        assert events == []


class TestGetUserAuditLog:
    """Tests for get_user_audit_log function."""

    def test_gets_events_for_user(self, dynamodb) -> None:
        """Gets all events by a specific user."""
        log_event(
            actor="alice", action="repo.created",
            resource="team/app", dynamodb_resource=dynamodb,
        )
        log_event(
            actor="bob", action="push",
            resource="team/app", dynamodb_resource=dynamodb,
        )
        log_event(
            actor="alice", action="access.granted",
            resource="other", dynamodb_resource=dynamodb,
        )

        events = get_user_audit_log("alice", dynamodb)
        assert len(events) == 2
        assert all(e.actor == "alice" for e in events)

    def test_empty_result(self, dynamodb) -> None:
        """Returns empty list for user with no events."""
        events = get_user_audit_log("ghost", dynamodb)
        assert events == []


class TestAuditDecorator:
    """Tests for @audit decorator."""

    def test_decorator_logs_event(self, dynamodb) -> None:
        """Decorated function logs an audit event."""

        @audit("test.action")
        def my_function(actor: str = "", resource: str = "", **kwargs):
            return "result"

        result = my_function(
            actor="alice", resource="team/app",
            dynamodb_resource=dynamodb,
        )
        assert result == "result"

        events = get_audit_log("team/app", dynamodb_resource=dynamodb)
        assert len(events) == 1
        assert events[0].action == "test.action"
        assert events[0].actor == "alice"

    def test_decorator_defaults(self, dynamodb) -> None:
        """Uses default actor/resource when not provided."""

        @audit("default.action")
        def my_function(**kwargs):
            return "ok"

        my_function(dynamodb_resource=dynamodb)

        # Event logged with "system" / "unknown" defaults
        events = get_audit_log("unknown", dynamodb_resource=dynamodb)
        assert len(events) == 1
        assert events[0].actor == "system"
