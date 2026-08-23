"""Tests for gitgateway.webhooks module."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from gitgateway.webhooks import (
    Webhook,
    WebhookDelivery,
    WebhookError,
    WebhookNotFoundError,
    _get_table,
    build_event_payload,
    compute_signature,
    delete_webhook,
    fire_event,
    get_webhook,
    list_deliveries,
    list_webhooks,
    register_webhook,
    verify_signature,
)


@pytest.fixture
def dynamodb():
    """Create mocked DynamoDB table for webhooks."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="gitgateway-webhooks",
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


class TestWebhookModel:
    """Tests for Webhook dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dictionary."""
        wh = Webhook(
            hook_id="h1", url="https://example.com/hook",
            secret="s3cr3t", events=["push", "pr.opened"],
            group="team", repo="app",
        )
        result = wh.to_dict()
        assert result["url"] == "https://example.com/hook"
        assert result["events"] == ["push", "pr.opened"]

    def test_from_dict(self) -> None:
        """Deserializes from dictionary."""
        data = {
            "hook_id": "h2",
            "url": "https://ci.example.com",
            "secret": "key",
            "events": ["pr.merged"],
            "active": False,
        }
        wh = Webhook.from_dict(data)
        assert wh.hook_id == "h2"
        assert wh.active is False

    def test_from_dict_defaults(self) -> None:
        """Handles missing keys."""
        wh = Webhook.from_dict({})
        assert wh.active is True
        assert wh.events == []


class TestWebhookDeliveryModel:
    """Tests for WebhookDelivery dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dictionary."""
        d = WebhookDelivery(
            delivery_id="d1", hook_id="h1", event="push",
            response_code=200, success=True, attempt=1,
        )
        result = d.to_dict()
        assert result["response_code"] == 200
        assert result["success"] is True

    def test_from_dict(self) -> None:
        """Deserializes from dictionary."""
        data = {
            "delivery_id": "d2",
            "hook_id": "h1",
            "event": "pr.merged",
            "response_code": 500,
            "success": False,
            "attempt": 2,
        }
        d = WebhookDelivery.from_dict(data)
        assert d.attempt == 2
        assert d.success is False

    def test_from_dict_defaults(self) -> None:
        """Handles missing keys."""
        d = WebhookDelivery.from_dict({})
        assert d.attempt == 1
        assert d.response_code == 0


class TestComputeSignature:
    """Tests for HMAC signature functions."""

    def test_compute_signature(self) -> None:
        """Computes deterministic signature."""
        sig = compute_signature('{"event":"push"}', "secret")
        assert sig.startswith("sha256=")
        assert len(sig) > 10

    def test_verify_valid_signature(self) -> None:
        """Verifies a correct signature."""
        payload = '{"test": true}'
        secret = "mysecret"
        sig = compute_signature(payload, secret)
        assert verify_signature(payload, secret, sig) is True

    def test_verify_invalid_signature(self) -> None:
        """Rejects an incorrect signature."""
        assert verify_signature("data", "secret", "sha256=bad") is False

    def test_different_secrets_differ(self) -> None:
        """Different secrets produce different signatures."""
        payload = "same payload"
        sig1 = compute_signature(payload, "secret1")
        sig2 = compute_signature(payload, "secret2")
        assert sig1 != sig2


class TestGetTable:
    """Tests for _get_table helper."""

    @mock_aws
    def test_default_resource(self) -> None:
        """Uses default boto3 resource."""
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="gitgateway-webhooks",
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
        table = _get_table("webhooks")
        assert table.table_name == "gitgateway-webhooks"


class TestRegisterWebhook:
    """Tests for register_webhook function."""

    def test_registers_webhook(self, dynamodb) -> None:
        """Registers a webhook with auto-assigned ID."""
        wh = register_webhook(
            Webhook(
                url="https://ci.example.com/hook",
                events=["push", "pr.opened"],
                group="team", repo="app",
            ),
            dynamodb,
        )
        assert wh.hook_id != ""
        assert wh.created_at != ""
        assert wh.secret != ""

    def test_preserves_provided_secret(self, dynamodb) -> None:
        """Keeps user-provided secret."""
        wh = register_webhook(
            Webhook(
                url="https://example.com",
                events=["push"],
                secret="custom-secret",
                group="team", repo="app",
            ),
            dynamodb,
        )
        assert wh.secret == "custom-secret"

    def test_missing_url_raises(self, dynamodb) -> None:
        """Raises when URL is missing."""
        with pytest.raises(WebhookError, match="URL is required"):
            register_webhook(
                Webhook(events=["push"], group="g", repo="r"),
                dynamodb,
            )

    def test_missing_events_raises(self, dynamodb) -> None:
        """Raises when events list is empty."""
        with pytest.raises(WebhookError, match="event is required"):
            register_webhook(
                Webhook(url="https://x.com", group="g", repo="r"),
                dynamodb,
            )

    def test_invalid_events_raises(self, dynamodb) -> None:
        """Raises on invalid event names."""
        with pytest.raises(WebhookError, match="Invalid events"):
            register_webhook(
                Webhook(
                    url="https://x.com",
                    events=["bad.event"],
                    group="g", repo="r",
                ),
                dynamodb,
            )


class TestListWebhooks:
    """Tests for list_webhooks function."""

    def test_lists_webhooks(self, dynamodb) -> None:
        """Lists all webhooks for a repo."""
        register_webhook(
            Webhook(url="https://a.com", events=["push"], group="t", repo="r"),
            dynamodb,
        )
        register_webhook(
            Webhook(url="https://b.com", events=["pr.opened"], group="t", repo="r"),
            dynamodb,
        )
        hooks = list_webhooks("t", "r", dynamodb)
        assert len(hooks) == 2

    def test_empty_result(self, dynamodb) -> None:
        """Returns empty list when no webhooks."""
        hooks = list_webhooks("t", "r", dynamodb)
        assert hooks == []


class TestGetWebhook:
    """Tests for get_webhook function."""

    def test_gets_webhook(self, dynamodb) -> None:
        """Gets a webhook by ID."""
        wh = register_webhook(
            Webhook(url="https://x.com", events=["push"], group="t", repo="r"),
            dynamodb,
        )
        result = get_webhook("t", "r", wh.hook_id, dynamodb)
        assert result.url == "https://x.com"

    def test_not_found_raises(self, dynamodb) -> None:
        """Raises for missing webhook."""
        with pytest.raises(WebhookNotFoundError, match="not found"):
            get_webhook("t", "r", "ghost", dynamodb)


class TestDeleteWebhook:
    """Tests for delete_webhook function."""

    def test_deletes_webhook(self, dynamodb) -> None:
        """Deletes an existing webhook."""
        wh = register_webhook(
            Webhook(url="https://x.com", events=["push"], group="t", repo="r"),
            dynamodb,
        )
        delete_webhook("t", "r", wh.hook_id, dynamodb)
        with pytest.raises(WebhookNotFoundError):
            get_webhook("t", "r", wh.hook_id, dynamodb)

    def test_not_found_raises(self, dynamodb) -> None:
        """Raises for missing webhook."""
        with pytest.raises(WebhookNotFoundError):
            delete_webhook("t", "r", "ghost", dynamodb)


class TestBuildEventPayload:
    """Tests for build_event_payload function."""

    def test_basic_payload(self) -> None:
        """Builds payload with required fields."""
        payload = build_event_payload(
            "push", "team", "app", "alice",
            data={"branch": "main", "commits": 3},
        )
        assert payload["event"] == "push"
        assert payload["repository"] == "team/app"
        assert payload["actor"] == "alice"
        assert payload["timestamp"] != ""
        assert payload["data"]["branch"] == "main"

    def test_payload_without_data(self) -> None:
        """Builds payload with no extra data."""
        payload = build_event_payload("pr.opened", "g", "r", "bob")
        assert payload["data"] == {}


class TestFireEvent:
    """Tests for fire_event function."""

    def test_fires_to_matching_webhooks(self, dynamodb) -> None:
        """Delivers to webhooks subscribed to the event."""
        register_webhook(
            Webhook(
                url="https://ci.com", events=["push"],
                group="t", repo="r",
            ),
            dynamodb,
        )
        delivery_ids = fire_event(
            "t", "r", "push", "alice", dynamodb_resource=dynamodb,
        )
        assert len(delivery_ids) == 1

    def test_skips_non_matching_events(self, dynamodb) -> None:
        """Does not deliver to webhooks not subscribed to the event."""
        register_webhook(
            Webhook(
                url="https://ci.com", events=["pr.merged"],
                group="t", repo="r",
            ),
            dynamodb,
        )
        delivery_ids = fire_event(
            "t", "r", "push", "alice", dynamodb_resource=dynamodb,
        )
        assert delivery_ids == []

    def test_skips_inactive_webhooks(self, dynamodb) -> None:
        """Does not deliver to inactive webhooks."""
        wh = register_webhook(
            Webhook(
                url="https://ci.com", events=["push"],
                group="t", repo="r",
            ),
            dynamodb,
        )
        # Deactivate
        table = _get_table("webhooks", dynamodb)
        table.put_item(
            Item={
                "PK": f"REPO#t/r",
                "SK": f"HOOK#{wh.hook_id}",
                **{**wh.to_dict(), "active": False},
            },
        )
        delivery_ids = fire_event(
            "t", "r", "push", "alice", dynamodb_resource=dynamodb,
        )
        assert delivery_ids == []

    def test_no_webhooks_returns_empty(self, dynamodb) -> None:
        """Returns empty list when no webhooks registered."""
        delivery_ids = fire_event(
            "t", "r", "push", "alice", dynamodb_resource=dynamodb,
        )
        assert delivery_ids == []

    def test_delivery_is_recorded(self, dynamodb) -> None:
        """Records delivery attempt in DynamoDB."""
        wh = register_webhook(
            Webhook(
                url="https://ci.com", events=["push"],
                group="t", repo="r",
            ),
            dynamodb,
        )
        fire_event("t", "r", "push", "alice", dynamodb_resource=dynamodb)
        deliveries = list_deliveries("t", "r", wh.hook_id, dynamodb)
        assert len(deliveries) == 1
        assert deliveries[0].event == "push"


class TestListDeliveries:
    """Tests for list_deliveries function."""

    def test_lists_deliveries(self, dynamodb) -> None:
        """Lists deliveries for a webhook."""
        wh = register_webhook(
            Webhook(
                url="https://ci.com", events=["push", "pr.opened"],
                group="t", repo="r",
            ),
            dynamodb,
        )
        fire_event("t", "r", "push", "alice", dynamodb_resource=dynamodb)
        fire_event("t", "r", "pr.opened", "bob", dynamodb_resource=dynamodb)
        deliveries = list_deliveries("t", "r", wh.hook_id, dynamodb)
        assert len(deliveries) == 2

    def test_empty_result(self, dynamodb) -> None:
        """Returns empty list when no deliveries."""
        deliveries = list_deliveries("t", "r", "ghost-hook", dynamodb)
        assert deliveries == []
