"""Webhook system for GitGateway.

Fires events on PR and push operations to external systems.
Supports HMAC-SHA256 signing, retry logic, and delivery logging.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass, field

import boto3

LOG = logging.getLogger(__name__)

TABLE_PREFIX = "gitgateway"

VALID_EVENTS = frozenset({
    "push",
    "pr.opened",
    "pr.updated",
    "pr.merged",
    "pr.closed",
    "pr.commented",
    "review.submitted",
    "branch_protection.updated",
})


class WebhookError(Exception):
    """Raised when a webhook operation fails."""


class WebhookNotFoundError(WebhookError):
    """Raised when a webhook is not found."""


@dataclass
class Webhook:
    """A registered webhook subscription.

    Attributes:
        hook_id: Unique webhook identifier.
        url: The URL to deliver events to.
        secret: Shared secret for HMAC signing.
        events: Set of event types to subscribe to.
        group: Repository group.
        repo: Repository name.
        created_at: ISO timestamp of creation.
        active: Whether the webhook is active.
    """

    hook_id: str = ""
    url: str = ""
    secret: str = ""
    events: list[str] = field(default_factory=list)
    group: str = ""
    repo: str = ""
    created_at: str = ""
    active: bool = True

    def to_dict(self) -> dict:
        """Serialize to dictionary for DynamoDB storage.

        Returns:
            Dictionary representation.
        """
        return {
            "hook_id": self.hook_id,
            "url": self.url,
            "secret": self.secret,
            "events": self.events,
            "group": self.group,
            "repo": self.repo,
            "created_at": self.created_at,
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Webhook:
        """Deserialize from DynamoDB dictionary.

        Args:
            data: Dictionary from DynamoDB.

        Returns:
            A Webhook instance.
        """
        return cls(
            hook_id=data.get("hook_id", ""),
            url=data.get("url", ""),
            secret=data.get("secret", ""),
            events=list(data.get("events", [])),
            group=data.get("group", ""),
            repo=data.get("repo", ""),
            created_at=data.get("created_at", ""),
            active=bool(data.get("active", True)),
        )


@dataclass
class WebhookDelivery:
    """A webhook delivery attempt record.

    Attributes:
        delivery_id: Unique delivery identifier.
        hook_id: The webhook this delivery is for.
        event: The event type that triggered delivery.
        payload: The JSON payload sent.
        response_code: HTTP response code (0 if failed).
        success: Whether delivery was successful.
        delivered_at: ISO timestamp of delivery attempt.
        attempt: Attempt number (1-3).
    """

    delivery_id: str = ""
    hook_id: str = ""
    event: str = ""
    payload: str = ""
    response_code: int = 0
    success: bool = False
    delivered_at: str = ""
    attempt: int = 1

    def to_dict(self) -> dict:
        """Serialize to dictionary for DynamoDB storage.

        Returns:
            Dictionary representation.
        """
        return {
            "delivery_id": self.delivery_id,
            "hook_id": self.hook_id,
            "event": self.event,
            "payload": self.payload,
            "response_code": self.response_code,
            "success": self.success,
            "delivered_at": self.delivered_at,
            "attempt": self.attempt,
        }

    @classmethod
    def from_dict(cls, data: dict) -> WebhookDelivery:
        """Deserialize from DynamoDB dictionary.

        Args:
            data: Dictionary from DynamoDB.

        Returns:
            A WebhookDelivery instance.
        """
        return cls(
            delivery_id=data.get("delivery_id", ""),
            hook_id=data.get("hook_id", ""),
            event=data.get("event", ""),
            payload=data.get("payload", ""),
            response_code=int(data.get("response_code", 0)),
            success=bool(data.get("success", False)),
            delivered_at=data.get("delivered_at", ""),
            attempt=int(data.get("attempt", 1)),
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


def compute_signature(payload: str, secret: str) -> str:
    """Compute HMAC-SHA256 signature for a webhook payload.

    Args:
        payload: The JSON payload string.
        secret: The shared secret.

    Returns:
        Hex-encoded HMAC-SHA256 signature prefixed with 'sha256='.
    """
    signature = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={signature}"


def verify_signature(payload: str, secret: str, signature: str) -> bool:
    """Verify an HMAC-SHA256 signature.

    Args:
        payload: The JSON payload string.
        secret: The shared secret.
        signature: The signature to verify (with sha256= prefix).

    Returns:
        True if the signature is valid.
    """
    expected = compute_signature(payload, secret)
    return hmac.compare_digest(expected, signature)


# --- Webhook CRUD ---


def register_webhook(
    webhook: Webhook,
    dynamodb_resource: object = None,
) -> Webhook:
    """Register a new webhook.

    Args:
        webhook: The Webhook to register (hook_id will be assigned).
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The registered Webhook.

    Raises:
        WebhookError: If required fields are missing or events invalid.
    """
    if not webhook.url:
        raise WebhookError("Webhook URL is required")
    if not webhook.events:
        raise WebhookError("At least one event is required")

    invalid = set(webhook.events) - VALID_EVENTS
    if invalid:
        raise WebhookError(f"Invalid events: {', '.join(sorted(invalid))}")

    webhook.hook_id = str(uuid.uuid4())
    webhook.created_at = _now_iso()

    if not webhook.secret:
        webhook.secret = uuid.uuid4().hex

    table = _get_table("webhooks", dynamodb_resource)
    table.put_item(
        Item={
            "PK": f"REPO#{webhook.group}/{webhook.repo}",
            "SK": f"HOOK#{webhook.hook_id}",
            **webhook.to_dict(),
        },
    )

    LOG.info(
        "Registered webhook %s for %s/%s",
        webhook.hook_id, webhook.group, webhook.repo,
    )
    return webhook


def list_webhooks(
    group: str,
    repo: str,
    dynamodb_resource: object = None,
) -> list[Webhook]:
    """List webhooks for a repository.

    Args:
        group: The group name.
        repo: The repository name.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        List of Webhook objects.
    """
    table = _get_table("webhooks", dynamodb_resource)
    response = table.query(
        KeyConditionExpression=(
            "PK = :pk AND begins_with(SK, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": f"REPO#{group}/{repo}",
            ":prefix": "HOOK#",
        },
    )
    return [Webhook.from_dict(item) for item in response.get("Items", [])]


def get_webhook(
    group: str,
    repo: str,
    hook_id: str,
    dynamodb_resource: object = None,
) -> Webhook:
    """Get a specific webhook.

    Args:
        group: The group name.
        repo: The repository name.
        hook_id: The webhook ID.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The Webhook.

    Raises:
        WebhookNotFoundError: If the webhook doesn't exist.
    """
    table = _get_table("webhooks", dynamodb_resource)
    response = table.get_item(
        Key={
            "PK": f"REPO#{group}/{repo}",
            "SK": f"HOOK#{hook_id}",
        },
    )
    item = response.get("Item")
    if not item:
        raise WebhookNotFoundError(
            f"Webhook '{hook_id}' not found",
        )
    return Webhook.from_dict(item)


def delete_webhook(
    group: str,
    repo: str,
    hook_id: str,
    dynamodb_resource: object = None,
) -> None:
    """Delete a webhook.

    Args:
        group: The group name.
        repo: The repository name.
        hook_id: The webhook ID.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Raises:
        WebhookNotFoundError: If the webhook doesn't exist.
    """
    # Verify exists
    get_webhook(group, repo, hook_id, dynamodb_resource)

    table = _get_table("webhooks", dynamodb_resource)
    table.delete_item(
        Key={
            "PK": f"REPO#{group}/{repo}",
            "SK": f"HOOK#{hook_id}",
        },
    )
    LOG.info("Deleted webhook %s", hook_id)


# --- Event Firing ---


def build_event_payload(
    event_type: str,
    group: str,
    repo: str,
    actor: str,
    data: dict | None = None,
) -> dict:
    """Build a webhook event payload.

    Args:
        event_type: The event type (e.g., 'push', 'pr.opened').
        group: The repository group.
        repo: The repository name.
        actor: The username that triggered the event.
        data: Optional event-specific data.

    Returns:
        The event payload dictionary.
    """
    return {
        "event": event_type,
        "repository": f"{group}/{repo}",
        "actor": actor,
        "timestamp": _now_iso(),
        "data": data or {},
    }


def fire_event(  # pylint: disable=too-many-positional-arguments
    group: str,
    repo: str,
    event_type: str,
    actor: str,
    data: dict | None = None,
    dynamodb_resource: object = None,
) -> list[str]:
    """Fire an event to all matching webhooks.

    In production, this would invoke the webhook dispatcher Lambda
    asynchronously. Here it records delivery intents.

    Args:
        group: The repository group.
        repo: The repository name.
        event_type: The event type.
        actor: The user that triggered the event.
        data: Optional event-specific data.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        List of delivery IDs created.
    """
    webhooks = list_webhooks(group, repo, dynamodb_resource)
    payload = build_event_payload(event_type, group, repo, actor, data)
    payload_json = json.dumps(payload, separators=(",", ":"))

    delivery_ids: list[str] = []
    for webhook in webhooks:
        if not webhook.active:
            continue
        if event_type not in webhook.events:
            continue

        delivery = WebhookDelivery(
            delivery_id=str(uuid.uuid4()),
            hook_id=webhook.hook_id,
            event=event_type,
            payload=payload_json,
            delivered_at=_now_iso(),
        )

        _record_delivery(
            group, repo, webhook.hook_id, delivery, dynamodb_resource,
        )
        delivery_ids.append(delivery.delivery_id)

    LOG.info(
        "Fired event '%s' for %s/%s: %d deliveries",
        event_type, group, repo, len(delivery_ids),
    )
    return delivery_ids


def _record_delivery(
    group: str,
    repo: str,
    hook_id: str,
    delivery: WebhookDelivery,
    dynamodb_resource: object = None,
) -> None:
    """Record a webhook delivery attempt.

    Args:
        group: The repository group.
        repo: The repository name.
        hook_id: The webhook ID.
        delivery: The delivery record.
        dynamodb_resource: Optional boto3 DynamoDB resource.
    """
    table = _get_table("webhooks", dynamodb_resource)
    table.put_item(
        Item={
            "PK": f"REPO#{group}/{repo}",
            "SK": (
                f"DELIVERY#{hook_id}#"
                f"{delivery.delivered_at}#{delivery.delivery_id}"
            ),
            **delivery.to_dict(),
        },
    )


def list_deliveries(
    group: str,
    repo: str,
    hook_id: str,
    dynamodb_resource: object = None,
) -> list[WebhookDelivery]:
    """List delivery attempts for a webhook.

    Args:
        group: The repository group.
        repo: The repository name.
        hook_id: The webhook ID.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        List of WebhookDelivery objects.
    """
    table = _get_table("webhooks", dynamodb_resource)
    response = table.query(
        KeyConditionExpression=(
            "PK = :pk AND begins_with(SK, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": f"REPO#{group}/{repo}",
            ":prefix": f"DELIVERY#{hook_id}#",
        },
    )
    return [
        WebhookDelivery.from_dict(item)
        for item in response.get("Items", [])
    ]
