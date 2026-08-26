"""Personal Access Token management for GitGateway.

Provides token generation, hashing, validation, and CRUD operations
backed by DynamoDB. Tokens are used for git Basic auth (username:
__token__, password: the token value).

Token format: gg_<32 hex chars> (e.g., gg_a1b2c3d4e5f6...)
Storage: SHA-256 hash of the token stored in DynamoDB (never raw).
"""
# pylint: disable=too-many-lines

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

import boto3

LOG = logging.getLogger(__name__)

TABLE_PREFIX = os.environ.get("STACK_NAME_PREFIX", "gitgateway")
TOKEN_PREFIX = "gg_"  # nosec B105 — not a password, it's a token format prefix
DEFAULT_EXPIRY_DAYS = 365


class TokenScope(Enum):
    """Access scope for a token."""

    READ = "read"
    READ_WRITE = "read/write"


class TokenError(Exception):
    """Raised when a token operation fails."""


class TokenNotFoundError(TokenError):
    """Raised when a token is not found."""


class TokenExpiredError(TokenError):
    """Raised when a token has expired."""


@dataclass
class PersonalAccessToken:
    """A personal access token.

    Attributes:
        token_id: Unique identifier (first 8 chars of hash).
        username: Owner of the token.
        description: Human-readable description.
        scope: Access scope (read or read/write).
        created_at: ISO timestamp of creation.
        expires_at: ISO timestamp of expiration.
        last_used_at: ISO timestamp of last use (or empty).
        token_hash: SHA-256 hash of the raw token.
        revoked: Whether the token has been revoked.
    """

    token_id: str = ""
    username: str = ""
    description: str = ""
    scope: TokenScope = TokenScope.READ_WRITE
    created_at: str = ""
    expires_at: str = ""
    last_used_at: str = ""
    token_hash: str = ""
    revoked: bool = False

    def to_dict(self) -> dict:
        """Serialize to dict (excludes hash for safety)."""
        return {
            "token_id": self.token_id,
            "username": self.username,
            "description": self.description,
            "scope": self.scope.value,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "last_used_at": self.last_used_at,
            "revoked": self.revoked,
        }

    def is_expired(self) -> bool:
        """Check if the token has expired."""
        if not self.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at)
            return datetime.now(timezone.utc) > exp
        except (ValueError, TypeError):
            return False


def _get_table(dynamodb_resource: object = None):
    """Get the tokens DynamoDB table.

    Args:
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        DynamoDB Table resource.
    """
    if dynamodb_resource is None:
        dynamodb_resource = boto3.resource("dynamodb")
    return dynamodb_resource.Table(f"{TABLE_PREFIX}-repos")


def _hash_token(raw_token: str) -> str:
    """Compute SHA-256 hash of a raw token.

    Args:
        raw_token: The raw token string.

    Returns:
        Hex-encoded SHA-256 hash.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    """Get current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()


def generate_token(
    username: str,
    description: str = "",
    scope: TokenScope = TokenScope.READ_WRITE,
    expiry_days: int = DEFAULT_EXPIRY_DAYS,
    dynamodb_resource: object = None,
) -> tuple[str, PersonalAccessToken]:
    """Generate a new personal access token.

    The raw token is returned once and never stored. Only the
    SHA-256 hash is persisted in DynamoDB.

    Args:
        username: The user who owns this token.
        description: Human-readable label.
        scope: Access scope.
        expiry_days: Days until expiration (default 365).
        dynamodb_resource: Optional DynamoDB resource for testing.

    Returns:
        Tuple of (raw_token, PersonalAccessToken metadata).
    """
    # Generate raw token: gg_ + 32 hex chars
    raw_token = TOKEN_PREFIX + os.urandom(16).hex()
    token_hash = _hash_token(raw_token)
    token_id = token_hash[:8]

    now = _now_iso()
    expires = datetime.fromtimestamp(
        time.time() + (expiry_days * 86400),
        tz=timezone.utc,
    ).isoformat()

    pat = PersonalAccessToken(
        token_id=token_id,
        username=username,
        description=description,
        scope=scope,
        created_at=now,
        expires_at=expires,
        last_used_at="",
        token_hash=token_hash,
        revoked=False,
    )

    # Store in DynamoDB
    table = _get_table(dynamodb_resource)
    table.put_item(Item={
        "PK": f"USER#{username}",
        "SK": f"TOKEN#{token_id}",
        "token_hash": token_hash,
        "description": description,
        "scope": scope.value,
        "created_at": now,
        "expires_at": expires,
        "last_used_at": "",
        "revoked": False,
    })

    # Also store a lookup entry by hash for fast validation
    table.put_item(Item={
        "PK": f"TOKEN_HASH#{token_hash}",
        "SK": "META",
        "username": username,
        "token_id": token_id,
        "scope": scope.value,
        "expires_at": expires,
        "revoked": False,
    })

    LOG.info(
        "Generated token %s for user %s (expires %s)",
        token_id, username, expires,
    )
    return raw_token, pat


def validate_token(
    raw_token: str,
    dynamodb_resource: object = None,
) -> PersonalAccessToken:
    """Validate a raw token and return its metadata.

    Checks that the token exists, is not revoked, and has not
    expired. Updates the last_used_at timestamp.

    Args:
        raw_token: The raw token string from the user.
        dynamodb_resource: Optional DynamoDB resource for testing.

    Returns:
        The PersonalAccessToken if valid.

    Raises:
        TokenNotFoundError: If the token doesn't exist.
        TokenExpiredError: If the token has expired.
        TokenError: If the token is revoked.
    """
    if not raw_token.startswith(TOKEN_PREFIX):
        raise TokenNotFoundError("Invalid token format")

    token_hash = _hash_token(raw_token)
    table = _get_table(dynamodb_resource)

    # Lookup by hash
    response = table.get_item(
        Key={"PK": f"TOKEN_HASH#{token_hash}", "SK": "META"},
    )
    item = response.get("Item")
    if not item:
        raise TokenNotFoundError("Token not found")

    if item.get("revoked"):
        raise TokenError("Token has been revoked")

    # Check expiration
    expires_at = item.get("expires_at", "")
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at)
            if datetime.now(timezone.utc) > exp:
                raise TokenExpiredError("Token has expired")
        except (ValueError, TypeError):
            pass

    # Update last_used_at on the user's token entry
    username = item.get("username", "")
    token_id = item.get("token_id", "")
    now = _now_iso()

    try:
        table.update_item(
            Key={"PK": f"USER#{username}", "SK": f"TOKEN#{token_id}"},
            UpdateExpression="SET last_used_at = :now",
            ExpressionAttributeValues={":now": now},
        )
    except Exception:  # pylint: disable=broad-except  # nosec B110
        pass  # Non-critical — don't fail auth if tracking fails

    return PersonalAccessToken(
        token_id=token_id,
        username=username,
        scope=TokenScope(item.get("scope", "read/write")),
        expires_at=expires_at,
        last_used_at=now,
        revoked=False,
    )


def list_tokens(
    username: str,
    dynamodb_resource: object = None,
) -> list[PersonalAccessToken]:
    """List all tokens for a user.

    Args:
        username: The user to list tokens for.
        dynamodb_resource: Optional DynamoDB resource for testing.

    Returns:
        List of PersonalAccessToken objects (without raw values).
    """
    table = _get_table(dynamodb_resource)
    response = table.query(
        KeyConditionExpression=(
            "PK = :pk AND begins_with(SK, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": f"USER#{username}",
            ":prefix": "TOKEN#",
        },
    )

    tokens = []
    for item in response.get("Items", []):
        tokens.append(PersonalAccessToken(
            token_id=item["SK"].replace("TOKEN#", ""),
            username=username,
            description=item.get("description", ""),
            scope=TokenScope(item.get("scope", "read/write")),
            created_at=item.get("created_at", ""),
            expires_at=item.get("expires_at", ""),
            last_used_at=item.get("last_used_at", ""),
            token_hash=item.get("token_hash", ""),
            revoked=item.get("revoked", False),
        ))

    return tokens


def revoke_token(
    username: str,
    token_id: str,
    dynamodb_resource: object = None,
) -> None:
    """Revoke a personal access token.

    Marks the token as revoked in both the user entry and the
    hash lookup entry.

    Args:
        username: Token owner.
        token_id: The token ID to revoke.
        dynamodb_resource: Optional DynamoDB resource for testing.

    Raises:
        TokenNotFoundError: If the token doesn't exist.
    """
    table = _get_table(dynamodb_resource)

    # Get the token to find its hash
    response = table.get_item(
        Key={"PK": f"USER#{username}", "SK": f"TOKEN#{token_id}"},
    )
    item = response.get("Item")
    if not item:
        raise TokenNotFoundError(
            f"Token {token_id} not found for user {username}",
        )

    token_hash = item.get("token_hash", "")

    # Mark revoked on user entry
    table.update_item(
        Key={"PK": f"USER#{username}", "SK": f"TOKEN#{token_id}"},
        UpdateExpression="SET revoked = :r",
        ExpressionAttributeValues={":r": True},
    )

    # Mark revoked on hash lookup
    if token_hash:
        table.update_item(
            Key={"PK": f"TOKEN_HASH#{token_hash}", "SK": "META"},
            UpdateExpression="SET revoked = :r",
            ExpressionAttributeValues={":r": True},
        )

    LOG.info("Revoked token %s for user %s", token_id, username)
