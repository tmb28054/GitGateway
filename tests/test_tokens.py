"""Tests for gitgateway.tokens module."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from gitgateway.tokens import (
    PersonalAccessToken,
    TokenError,
    TokenExpiredError,
    TokenNotFoundError,
    TokenScope,
    _hash_token,
    generate_token,
    list_tokens,
    revoke_token,
    validate_token,
)


class TestTokenScope:
    """Tests for TokenScope enum."""

    def test_read_scope(self) -> None:
        """Read scope value."""
        assert TokenScope.READ.value == "read"

    def test_read_write_scope(self) -> None:
        """Read/write scope value."""
        assert TokenScope.READ_WRITE.value == "read/write"


class TestPersonalAccessToken:
    """Tests for PersonalAccessToken dataclass."""

    def test_to_dict_excludes_hash(self) -> None:
        """Serialization excludes the token hash."""
        pat = PersonalAccessToken(
            token_id="abc12345",
            username="topaz",
            description="Dev laptop",
            scope=TokenScope.READ_WRITE,
            created_at="2026-01-01T00:00:00+00:00",
            expires_at="2027-01-01T00:00:00+00:00",
            token_hash="secrethash",
        )
        d = pat.to_dict()
        assert "token_hash" not in d
        assert d["token_id"] == "abc12345"
        assert d["username"] == "topaz"
        assert d["scope"] == "read/write"

    def test_is_expired_false(self) -> None:
        """Token with future expiry is not expired."""
        pat = PersonalAccessToken(
            expires_at="2099-01-01T00:00:00+00:00",
        )
        assert pat.is_expired() is False

    def test_is_expired_true(self) -> None:
        """Token with past expiry is expired."""
        pat = PersonalAccessToken(
            expires_at="2020-01-01T00:00:00+00:00",
        )
        assert pat.is_expired() is True

    def test_is_expired_empty(self) -> None:
        """Token with no expiry is not expired."""
        pat = PersonalAccessToken(expires_at="")
        assert pat.is_expired() is False


class TestHashToken:
    """Tests for _hash_token."""

    def test_produces_hex_string(self) -> None:
        """Returns a hex string."""
        result = _hash_token("gg_abcdef1234567890abcdef1234567890")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self) -> None:
        """Same input produces same hash."""
        token = "gg_test123"
        assert _hash_token(token) == _hash_token(token)

    def test_different_inputs_different_hashes(self) -> None:
        """Different tokens produce different hashes."""
        assert _hash_token("gg_aaa") != _hash_token("gg_bbb")


class TestGenerateToken:
    """Tests for generate_token."""

    @patch("gitgateway.tokens._get_table")
    def test_returns_raw_token_and_metadata(self, mock_table) -> None:
        """Returns a tuple of raw token and PAT."""
        table = MagicMock()
        mock_table.return_value = table

        raw, pat = generate_token(
            username="topaz",
            description="Test token",
            scope=TokenScope.READ,
            expiry_days=30,
        )

        assert raw.startswith("gg_")
        assert len(raw) == 3 + 32  # gg_ + 16 bytes hex
        assert pat.username == "topaz"
        assert pat.description == "Test token"
        assert pat.scope == TokenScope.READ
        assert pat.token_id == _hash_token(raw)[:8]
        assert pat.created_at
        assert pat.expires_at
        assert not pat.revoked

    @patch("gitgateway.tokens._get_table")
    def test_stores_two_dynamo_entries(self, mock_table) -> None:
        """Creates both user entry and hash lookup entry."""
        table = MagicMock()
        mock_table.return_value = table

        raw, pat = generate_token(username="user1", description="ci")

        assert table.put_item.call_count == 2
        calls = table.put_item.call_args_list

        # First call: USER# entry
        user_item = calls[0][1]["Item"]
        assert user_item["PK"].startswith("USER#")
        assert user_item["SK"].startswith("TOKEN#")

        # Second call: TOKEN_HASH# entry
        hash_item = calls[1][1]["Item"]
        assert hash_item["PK"].startswith("TOKEN_HASH#")
        assert hash_item["SK"] == "META"
        assert hash_item["username"] == "user1"

    @patch("gitgateway.tokens._get_table")
    def test_default_expiry_is_one_year(self, mock_table) -> None:
        """Default expiry is approximately 365 days from now."""
        table = MagicMock()
        mock_table.return_value = table

        raw, pat = generate_token(username="user1")

        expires = datetime.fromisoformat(pat.expires_at)
        now = datetime.now(timezone.utc)
        delta = (expires - now).days
        assert 364 <= delta <= 366


class TestValidateToken:
    """Tests for validate_token."""

    @patch("gitgateway.tokens._get_table")
    def test_valid_token(self, mock_table) -> None:
        """Valid token returns metadata."""
        table = MagicMock()
        mock_table.return_value = table

        raw_token = "gg_abcdef1234567890abcdef1234567890"
        token_hash = _hash_token(raw_token)

        table.get_item.return_value = {
            "Item": {
                "username": "topaz",
                "token_id": token_hash[:8],
                "scope": "read/write",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "revoked": False,
            },
        }

        pat = validate_token(raw_token)
        assert pat.username == "topaz"
        assert pat.scope == TokenScope.READ_WRITE

    @patch("gitgateway.tokens._get_table")
    def test_invalid_prefix_raises(self, mock_table) -> None:
        """Token without gg_ prefix raises."""
        with pytest.raises(TokenNotFoundError, match="Invalid token format"):
            validate_token("bad_token_no_prefix")

    @patch("gitgateway.tokens._get_table")
    def test_token_not_found_raises(self, mock_table) -> None:
        """Unknown token raises TokenNotFoundError."""
        table = MagicMock()
        mock_table.return_value = table
        table.get_item.return_value = {}

        with pytest.raises(TokenNotFoundError, match="not found"):
            validate_token("gg_unknown1234567890unknown12345678")

    @patch("gitgateway.tokens._get_table")
    def test_revoked_token_raises(self, mock_table) -> None:
        """Revoked token raises TokenError."""
        table = MagicMock()
        mock_table.return_value = table
        table.get_item.return_value = {
            "Item": {
                "username": "topaz",
                "token_id": "abc",
                "scope": "read",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "revoked": True,
            },
        }

        with pytest.raises(TokenError, match="revoked"):
            validate_token("gg_abcdef1234567890abcdef1234567890")

    @patch("gitgateway.tokens._get_table")
    def test_expired_token_raises(self, mock_table) -> None:
        """Expired token raises TokenExpiredError."""
        table = MagicMock()
        mock_table.return_value = table
        table.get_item.return_value = {
            "Item": {
                "username": "topaz",
                "token_id": "abc",
                "scope": "read",
                "expires_at": "2020-01-01T00:00:00+00:00",
                "revoked": False,
            },
        }

        with pytest.raises(TokenExpiredError, match="expired"):
            validate_token("gg_abcdef1234567890abcdef1234567890")


class TestListTokens:
    """Tests for list_tokens."""

    @patch("gitgateway.tokens._get_table")
    def test_returns_user_tokens(self, mock_table) -> None:
        """Lists all tokens for a user."""
        table = MagicMock()
        mock_table.return_value = table
        table.query.return_value = {
            "Items": [
                {
                    "SK": "TOKEN#abc12345",
                    "description": "Laptop",
                    "scope": "read/write",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "expires_at": "2027-01-01T00:00:00+00:00",
                    "last_used_at": "",
                    "token_hash": "hashvalue",
                    "revoked": False,
                },
            ],
        }

        tokens = list_tokens("topaz")
        assert len(tokens) == 1
        assert tokens[0].token_id == "abc12345"
        assert tokens[0].description == "Laptop"
        assert tokens[0].scope == TokenScope.READ_WRITE

    @patch("gitgateway.tokens._get_table")
    def test_empty_list(self, mock_table) -> None:
        """Returns empty list when no tokens."""
        table = MagicMock()
        mock_table.return_value = table
        table.query.return_value = {"Items": []}

        tokens = list_tokens("newuser")
        assert tokens == []


class TestRevokeToken:
    """Tests for revoke_token."""

    @patch("gitgateway.tokens._get_table")
    def test_revokes_token(self, mock_table) -> None:
        """Marks token as revoked in both entries."""
        table = MagicMock()
        mock_table.return_value = table
        table.get_item.return_value = {
            "Item": {
                "PK": "USER#topaz",
                "SK": "TOKEN#abc12345",
                "token_hash": "somehash",
            },
        }

        revoke_token("topaz", "abc12345")

        assert table.update_item.call_count == 2

    @patch("gitgateway.tokens._get_table")
    def test_not_found_raises(self, mock_table) -> None:
        """Raises when token doesn't exist."""
        table = MagicMock()
        mock_table.return_value = table
        table.get_item.return_value = {}

        with pytest.raises(TokenNotFoundError):
            revoke_token("topaz", "nonexistent")
