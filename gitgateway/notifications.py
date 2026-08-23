"""Notification system for GitGateway.

Handles email notifications via SES, in-app notifications stored
in DynamoDB, user preferences, and rate limiting/batching.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

import boto3

LOG = logging.getLogger(__name__)

TABLE_PREFIX = "gitgateway"


class NotificationError(Exception):
    """Raised when a notification operation fails."""


class NotificationType(Enum):
    """Types of notifications."""

    PR_ASSIGNED = "pr_assigned"
    REVIEW_REQUESTED = "review_requested"
    PR_COMMENTED = "pr_commented"
    PR_MERGED = "pr_merged"
    PR_CLOSED = "pr_closed"
    MENTION = "mention"
    SAST_FINDINGS = "sast_findings"


@dataclass
class Notification:
    """An in-app notification.

    Attributes:
        notification_id: Unique identifier.
        username: Recipient username.
        notification_type: Type of notification.
        title: Short notification title.
        body: Notification body text.
        resource: Related resource (e.g., 'team/app#1').
        read: Whether the notification has been read.
        created_at: ISO timestamp.
    """

    notification_id: str = ""
    username: str = ""
    notification_type: str = ""
    title: str = ""
    body: str = ""
    resource: str = ""
    read: bool = False
    created_at: str = ""

    def to_dict(self) -> dict:
        """Serialize to dictionary for DynamoDB storage.

        Returns:
            Dictionary representation.
        """
        return {
            "notification_id": self.notification_id,
            "username": self.username,
            "notification_type": self.notification_type,
            "title": self.title,
            "body": self.body,
            "resource": self.resource,
            "read": self.read,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Notification:
        """Deserialize from DynamoDB dictionary.

        Args:
            data: Dictionary from DynamoDB.

        Returns:
            A Notification instance.
        """
        return cls(
            notification_id=data.get("notification_id", ""),
            username=data.get("username", ""),
            notification_type=data.get("notification_type", ""),
            title=data.get("title", ""),
            body=data.get("body", ""),
            resource=data.get("resource", ""),
            read=bool(data.get("read", False)),
            created_at=data.get("created_at", ""),
        )


@dataclass
class NotificationPreferences:
    """User notification preferences.

    Attributes:
        username: The user.
        email_enabled: Whether email notifications are enabled.
        disabled_types: Set of notification types to suppress.
    """

    username: str = ""
    email_enabled: bool = True
    disabled_types: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "username": self.username,
            "email_enabled": self.email_enabled,
            "disabled_types": self.disabled_types,
        }

    @classmethod
    def from_dict(cls, data: dict) -> NotificationPreferences:
        """Deserialize from dictionary.

        Args:
            data: Dictionary from DynamoDB.

        Returns:
            A NotificationPreferences instance.
        """
        return cls(
            username=data.get("username", ""),
            email_enabled=bool(data.get("email_enabled", True)),
            disabled_types=list(data.get("disabled_types", [])),
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


# --- In-app Notifications ---


def create_notification(  # pylint: disable=too-many-positional-arguments
    username: str,
    notification_type: NotificationType,
    title: str,
    body: str,
    resource: str = "",
    dynamodb_resource: object = None,
) -> Notification:
    """Create an in-app notification for a user.

    Args:
        username: Recipient username.
        notification_type: Type of notification.
        title: Short title.
        body: Body text.
        resource: Related resource identifier.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The created Notification.
    """
    notification = Notification(
        notification_id=str(uuid.uuid4()),
        username=username,
        notification_type=notification_type.value,
        title=title,
        body=body,
        resource=resource,
        created_at=_now_iso(),
    )

    table = _get_table("notifications", dynamodb_resource)
    table.put_item(
        Item={
            "PK": f"USER#{username}",
            "SK": (
                f"NOTIF#{notification.created_at}"
                f"#{notification.notification_id}"
            ),
            **notification.to_dict(),
        },
    )

    LOG.info("Created notification for %s: %s", username, title)
    return notification


def list_notifications(
    username: str,
    unread_only: bool = False,
    dynamodb_resource: object = None,
) -> list[Notification]:
    """List notifications for a user.

    Args:
        username: The username.
        unread_only: If True, only return unread notifications.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        List of Notification objects (newest first).
    """
    table = _get_table("notifications", dynamodb_resource)
    response = table.query(
        KeyConditionExpression=(
            "PK = :pk AND begins_with(SK, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": f"USER#{username}",
            ":prefix": "NOTIF#",
        },
        ScanIndexForward=False,
    )

    notifications = [
        Notification.from_dict(item)
        for item in response.get("Items", [])
    ]

    if unread_only:
        notifications = [n for n in notifications if not n.read]

    return notifications


def mark_read(
    username: str,
    notification_ids: list[str],
    dynamodb_resource: object = None,
) -> int:
    """Mark notifications as read.

    Args:
        username: The username.
        notification_ids: List of notification IDs to mark as read.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        Number of notifications marked as read.
    """
    all_notifications = list_notifications(
        username, dynamodb_resource=dynamodb_resource,
    )
    table = _get_table("notifications", dynamodb_resource)

    marked = 0
    for notification in all_notifications:
        if notification.notification_id in notification_ids:
            notification.read = True
            table.put_item(
                Item={
                    "PK": f"USER#{username}",
                    "SK": (
                        f"NOTIF#{notification.created_at}"
                        f"#{notification.notification_id}"
                    ),
                    **notification.to_dict(),
                },
            )
            marked += 1

    return marked


# --- Preferences ---


def get_preferences(
    username: str,
    dynamodb_resource: object = None,
) -> NotificationPreferences:
    """Get notification preferences for a user.

    Args:
        username: The username.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        NotificationPreferences (defaults if none saved).
    """
    table = _get_table("notifications", dynamodb_resource)
    response = table.get_item(
        Key={"PK": f"USER#{username}", "SK": "PREFERENCES"},
    )
    item = response.get("Item")
    if not item:
        return NotificationPreferences(username=username)
    return NotificationPreferences.from_dict(item)


def save_preferences(
    prefs: NotificationPreferences,
    dynamodb_resource: object = None,
) -> NotificationPreferences:
    """Save notification preferences for a user.

    Args:
        prefs: The preferences to save.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The saved preferences.
    """
    table = _get_table("notifications", dynamodb_resource)
    table.put_item(
        Item={
            "PK": f"USER#{prefs.username}",
            "SK": "PREFERENCES",
            **prefs.to_dict(),
        },
    )
    return prefs


# --- Notification Dispatch ---


def should_notify(
    username: str,
    notification_type: NotificationType,
    dynamodb_resource: object = None,
) -> bool:
    """Check if a user should receive a notification type.

    Args:
        username: The username.
        notification_type: The notification type.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        True if the user has not disabled this type.
    """
    prefs = get_preferences(username, dynamodb_resource)
    return notification_type.value not in prefs.disabled_types


def render_email(
    notification_type: NotificationType,  # pylint: disable=unused-argument
    title: str,
    body: str,
    resource: str = "",
) -> dict[str, str]:
    """Render email content for a notification.

    Args:
        notification_type: The notification type.
        title: Notification title.
        body: Notification body.
        resource: Related resource.

    Returns:
        Dictionary with 'subject', 'html', and 'text' keys.
    """
    subject = f"[GitGateway] {title}"

    html = (
        "<html><body>"
        f"<h2>{title}</h2>"
        f"<p>{body}</p>"
    )
    if resource:
        html += f"<p><small>Resource: {resource}</small></p>"
    html += "</body></html>"

    text = f"{title}\n\n{body}"
    if resource:
        text += f"\n\nResource: {resource}"

    return {
        "subject": subject,
        "html": html,
        "text": text,
    }
