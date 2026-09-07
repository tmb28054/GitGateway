"""Audit logging for GitGateway.

Records all state-changing operations with actor, action, resource,
details, and timestamp. Supports filtering by resource, user, and
time range. Configurable TTL for automatic expiration.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from functools import wraps

import boto3

LOG = logging.getLogger(__name__)

TABLE_PREFIX = "gitgateway"
DEFAULT_TTL_DAYS = 90


class AuditError(Exception):
    """Raised when an audit operation fails."""


@dataclass
class AuditEvent:
    """An audit log entry.

    Attributes:
        event_id: Unique event identifier.
        timestamp: ISO timestamp of the event.
        actor: Username of the user who performed the action.
        action: The action performed (e.g., 'repo.created').
        resource: The affected resource (e.g., 'team/app').
        details: JSON-serializable details about the action.
        ip_address: Client IP address.
        ttl: DynamoDB TTL epoch timestamp for auto-expiration.
    """

    event_id: str = ""
    timestamp: str = ""
    actor: str = ""
    action: str = ""
    resource: str = ""
    details: dict | None = None
    ip_address: str = ""
    ttl: int = 0

    def to_dict(self) -> dict:
        """Serialize to dictionary for DynamoDB storage.

        Returns:
            Dictionary representation.
        """
        result = {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "actor": self.actor,
            "action": self.action,
            "resource": self.resource,
            "details": json.dumps(self.details or {}),
            "ip_address": self.ip_address,
        }
        if self.ttl:
            result["ttl"] = self.ttl
        return result

    @classmethod
    def from_dict(cls, data: dict) -> AuditEvent:
        """Deserialize from DynamoDB dictionary.

        Args:
            data: Dictionary from DynamoDB.

        Returns:
            An AuditEvent instance.
        """
        details_raw = data.get("details", "{}")
        if isinstance(details_raw, str):
            try:
                details = json.loads(details_raw)
            except json.JSONDecodeError:
                details = {}
        else:
            details = details_raw

        return cls(
            event_id=data.get("event_id", ""),
            timestamp=data.get("timestamp", ""),
            actor=data.get("actor", ""),
            action=data.get("action", ""),
            resource=data.get("resource", ""),
            details=details,
            ip_address=data.get("ip_address", ""),
            ttl=int(data.get("ttl", 0)),
        )


def _get_table(dynamodb_resource: object = None):
    """Get the audit DynamoDB table resource.

    Args:
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        A DynamoDB Table resource.
    """
    if dynamodb_resource is None:
        dynamodb_resource = boto3.resource("dynamodb")
    return dynamodb_resource.Table(f"{TABLE_PREFIX}-audit")


def _now_iso() -> str:
    """Get current time as ISO string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _compute_ttl(days: int = DEFAULT_TTL_DAYS) -> int:
    """Compute TTL epoch timestamp.

    Args:
        days: Number of days until expiration.

    Returns:
        Unix epoch timestamp for TTL.
    """
    return int(time.time()) + (days * 86400)


def log_event(  # pylint: disable=too-many-positional-arguments
    actor: str,
    action: str,
    resource: str,
    details: dict | None = None,
    ip_address: str = "",
    ttl_days: int = DEFAULT_TTL_DAYS,
    dynamodb_resource: object = None,
) -> AuditEvent:
    """Log an audit event.

    Args:
        actor: Username performing the action.
        action: Action identifier (e.g., 'repo.created').
        resource: Affected resource path.
        details: Optional event-specific details.
        ip_address: Client IP address.
        ttl_days: Days until auto-expiration (0 = no TTL).
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The created AuditEvent.
    """
    event = AuditEvent(
        event_id=str(uuid.uuid4()),
        timestamp=_now_iso(),
        actor=actor,
        action=action,
        resource=resource,
        details=details,
        ip_address=ip_address,
        ttl=_compute_ttl(ttl_days) if ttl_days > 0 else 0,
    )

    table = _get_table(dynamodb_resource)
    table.put_item(
        Item={
            "PK": f"REPO#{resource}",
            "SK": f"EVENT#{event.timestamp}#{event.event_id}",
            **event.to_dict(),
        },
    )

    LOG.info(
        "Audit: %s performed '%s' on '%s'",
        actor, action, resource,
    )
    return event


def get_audit_log(
    resource: str,
    start_time: str = "",
    end_time: str = "",
    dynamodb_resource: object = None,
) -> list[AuditEvent]:
    """Get audit log for a resource.

    Args:
        resource: The resource path to get events for.
        start_time: Optional ISO start time filter.
        end_time: Optional ISO end time filter.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        List of AuditEvent objects (newest first).
    """
    table = _get_table(dynamodb_resource)

    key_condition = "PK = :pk AND begins_with(SK, :prefix)"
    expr_values: dict = {
        ":pk": f"REPO#{resource}",
        ":prefix": "EVENT#",
    }

    response = table.query(
        KeyConditionExpression=key_condition,
        ExpressionAttributeValues=expr_values,
        ScanIndexForward=False,
    )

    events = [
        AuditEvent.from_dict(item)
        for item in response.get("Items", [])
    ]

    # Apply time filters in memory (simple approach)
    if start_time:
        events = [e for e in events if e.timestamp >= start_time]
    if end_time:
        events = [e for e in events if e.timestamp <= end_time]

    return events


def list_all_events(
    limit: int = 200,
    dynamodb_resource: object = None,
) -> list[AuditEvent]:
    """List audit events across all resources, newest first.

    Scans the audit table (acceptable for admin queries) and sorts
    by timestamp descending. Suitable for the admin audit viewer.

    Args:
        limit: Maximum number of events to return.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        List of AuditEvent objects sorted newest first.
    """
    table = _get_table(dynamodb_resource)

    events: list[AuditEvent] = []
    scan_kwargs: dict = {
        "FilterExpression": "begins_with(SK, :prefix)",
        "ExpressionAttributeValues": {":prefix": "EVENT#"},
    }

    while True:
        response = table.scan(**scan_kwargs)
        events.extend(
            AuditEvent.from_dict(item)
            for item in response.get("Items", [])
        )
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    events.sort(key=lambda e: e.timestamp, reverse=True)
    return events[:limit]


def get_user_audit_log(
    username: str,
    dynamodb_resource: object = None,
) -> list[AuditEvent]:
    """Get audit log for a specific user across all resources.

    Uses a scan with filter (not ideal for large datasets but
    acceptable for admin queries).

    Args:
        username: The username to filter by.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        List of AuditEvent objects.
    """
    table = _get_table(dynamodb_resource)

    response = table.scan(
        FilterExpression="actor = :actor",
        ExpressionAttributeValues={":actor": username},
    )

    return [
        AuditEvent.from_dict(item)
        for item in response.get("Items", [])
    ]


def audit(action: str):
    """Decorator to automatically log audit events.

    The decorated function must accept 'actor' and 'resource'
    as keyword arguments (or have them in its signature).

    Args:
        action: The action string to log.

    Returns:
        Decorator function.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            actor = kwargs.get("actor", "system")
            resource = kwargs.get("resource", "unknown")
            log_event(
                actor=actor,
                action=action,
                resource=resource,
                dynamodb_resource=kwargs.get("dynamodb_resource"),
            )
            return result
        return wrapper
    return decorator
