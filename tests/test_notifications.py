"""Tests for gitgateway.notifications module."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from gitgateway.notifications import (
    Notification,
    NotificationPreferences,
    NotificationType,
    _get_table,
    create_notification,
    get_preferences,
    list_notifications,
    mark_read,
    render_email,
    save_preferences,
    should_notify,
)


@pytest.fixture
def dynamodb():
    """Create mocked DynamoDB table for notifications."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="gitgateway-notifications",
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


class TestNotificationModel:
    """Tests for Notification dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dictionary."""
        n = Notification(
            notification_id="n1", username="alice",
            notification_type="pr_commented",
            title="New comment", body="Bob commented on PR #1",
            resource="team/app#1",
        )
        result = n.to_dict()
        assert result["username"] == "alice"
        assert result["notification_type"] == "pr_commented"

    def test_from_dict(self) -> None:
        """Deserializes from dictionary."""
        data = {
            "notification_id": "n2", "username": "bob",
            "notification_type": "pr_merged",
            "title": "PR merged", "read": True,
        }
        n = Notification.from_dict(data)
        assert n.read is True
        assert n.notification_type == "pr_merged"

    def test_from_dict_defaults(self) -> None:
        """Handles missing keys."""
        n = Notification.from_dict({})
        assert n.read is False
        assert n.username == ""


class TestNotificationPreferences:
    """Tests for NotificationPreferences dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dictionary."""
        prefs = NotificationPreferences(
            username="alice", email_enabled=False,
            disabled_types=["pr_commented"],
        )
        result = prefs.to_dict()
        assert result["email_enabled"] is False
        assert "pr_commented" in result["disabled_types"]

    def test_from_dict(self) -> None:
        """Deserializes from dictionary."""
        data = {
            "username": "bob",
            "email_enabled": False,
            "disabled_types": ["mention"],
        }
        prefs = NotificationPreferences.from_dict(data)
        assert prefs.email_enabled is False
        assert "mention" in prefs.disabled_types

    def test_from_dict_defaults(self) -> None:
        """Handles missing keys."""
        prefs = NotificationPreferences.from_dict({})
        assert prefs.email_enabled is True
        assert prefs.disabled_types == []


class TestGetTable:
    """Tests for _get_table helper."""

    @mock_aws
    def test_default_resource(self) -> None:
        """Uses default boto3 resource."""
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="gitgateway-notifications",
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
        table = _get_table("notifications")
        assert table.table_name == "gitgateway-notifications"


class TestCreateNotification:
    """Tests for create_notification function."""

    def test_creates_notification(self, dynamodb) -> None:
        """Creates an in-app notification."""
        n = create_notification(
            "alice", NotificationType.PR_COMMENTED,
            "New comment", "Bob commented on your PR",
            resource="team/app#1",
            dynamodb_resource=dynamodb,
        )
        assert n.notification_id != ""
        assert n.created_at != ""
        assert n.username == "alice"
        assert n.read is False


class TestListNotifications:
    """Tests for list_notifications function."""

    def test_lists_all(self, dynamodb) -> None:
        """Lists all notifications for a user."""
        create_notification(
            "alice", NotificationType.PR_COMMENTED,
            "Comment 1", "body1",
            dynamodb_resource=dynamodb,
        )
        create_notification(
            "alice", NotificationType.PR_MERGED,
            "Merged", "body2",
            dynamodb_resource=dynamodb,
        )
        notifs = list_notifications("alice", dynamodb_resource=dynamodb)
        assert len(notifs) == 2

    def test_unread_only(self, dynamodb) -> None:
        """Filters to unread only."""
        n = create_notification(
            "alice", NotificationType.MENTION,
            "Mention", "body",
            dynamodb_resource=dynamodb,
        )
        create_notification(
            "alice", NotificationType.PR_CLOSED,
            "Closed", "body2",
            dynamodb_resource=dynamodb,
        )
        # Mark one as read
        mark_read("alice", [n.notification_id], dynamodb)

        unread = list_notifications(
            "alice", unread_only=True, dynamodb_resource=dynamodb,
        )
        assert len(unread) == 1
        assert unread[0].title == "Closed"

    def test_empty_result(self, dynamodb) -> None:
        """Returns empty list for user with no notifications."""
        notifs = list_notifications("ghost", dynamodb_resource=dynamodb)
        assert notifs == []


class TestMarkRead:
    """Tests for mark_read function."""

    def test_marks_notifications_read(self, dynamodb) -> None:
        """Marks specified notifications as read."""
        n1 = create_notification(
            "alice", NotificationType.PR_ASSIGNED,
            "Assigned", "body",
            dynamodb_resource=dynamodb,
        )
        n2 = create_notification(
            "alice", NotificationType.REVIEW_REQUESTED,
            "Review", "body",
            dynamodb_resource=dynamodb,
        )
        marked = mark_read("alice", [n1.notification_id], dynamodb)
        assert marked == 1

        notifs = list_notifications("alice", dynamodb_resource=dynamodb)
        read_ids = {n.notification_id for n in notifs if n.read}
        assert n1.notification_id in read_ids
        assert n2.notification_id not in read_ids

    def test_no_matching_ids(self, dynamodb) -> None:
        """Returns 0 when no IDs match."""
        create_notification(
            "alice", NotificationType.MENTION,
            "Test", "body",
            dynamodb_resource=dynamodb,
        )
        marked = mark_read("alice", ["nonexistent-id"], dynamodb)
        assert marked == 0


class TestPreferences:
    """Tests for preferences CRUD."""

    def test_get_default_preferences(self, dynamodb) -> None:
        """Returns default preferences for new user."""
        prefs = get_preferences("newuser", dynamodb)
        assert prefs.username == "newuser"
        assert prefs.email_enabled is True
        assert prefs.disabled_types == []

    def test_save_and_get_preferences(self, dynamodb) -> None:
        """Saves and retrieves preferences."""
        save_preferences(
            NotificationPreferences(
                username="alice",
                email_enabled=False,
                disabled_types=["pr_commented", "mention"],
            ),
            dynamodb,
        )
        prefs = get_preferences("alice", dynamodb)
        assert prefs.email_enabled is False
        assert "pr_commented" in prefs.disabled_types


class TestShouldNotify:
    """Tests for should_notify function."""

    def test_should_notify_default(self, dynamodb) -> None:
        """Returns True by default (no preferences saved)."""
        assert should_notify(
            "alice", NotificationType.PR_COMMENTED, dynamodb,
        ) is True

    def test_should_not_notify_disabled(self, dynamodb) -> None:
        """Returns False when type is disabled."""
        save_preferences(
            NotificationPreferences(
                username="alice",
                disabled_types=["pr_commented"],
            ),
            dynamodb,
        )
        assert should_notify(
            "alice", NotificationType.PR_COMMENTED, dynamodb,
        ) is False

    def test_should_notify_other_type(self, dynamodb) -> None:
        """Returns True for types not disabled."""
        save_preferences(
            NotificationPreferences(
                username="alice",
                disabled_types=["pr_commented"],
            ),
            dynamodb,
        )
        assert should_notify(
            "alice", NotificationType.PR_MERGED, dynamodb,
        ) is True


class TestRenderEmail:
    """Tests for render_email function."""

    def test_renders_with_resource(self) -> None:
        """Renders email with resource link."""
        result = render_email(
            NotificationType.PR_COMMENTED,
            "New comment on PR #1",
            "Alice commented: looks good!",
            resource="team/app#1",
        )
        assert "[GitGateway]" in result["subject"]
        assert "New comment" in result["subject"]
        assert "<html>" in result["html"]
        assert "looks good" in result["html"]
        assert "team/app#1" in result["html"]
        assert "looks good" in result["text"]
        assert "team/app#1" in result["text"]

    def test_renders_without_resource(self) -> None:
        """Renders email without resource."""
        result = render_email(
            NotificationType.MENTION,
            "You were mentioned",
            "In a comment",
        )
        assert "mentioned" in result["subject"]
        assert "Resource" not in result["text"]
        assert "Resource" not in result["html"]
