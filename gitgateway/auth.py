"""Authentication and authorization for GitGateway.

Handles OAuth flow, JWT validation (RS256), Basic auth for git operations,
and session cookie management for the web UI.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import jwt
import requests

LOG = logging.getLogger(__name__)

# Cache for IdP public key
_PUBLIC_KEY_CACHE: dict[str, Any] = {}
_PUBLIC_KEY_CACHE_TTL = 3600  # 1 hour


class AuthError(Exception):
    """Raised when authentication fails."""


@dataclass
class AuthenticatedUser:
    """Represents an authenticated user.

    Attributes:
        username: The authenticated username (from JWT 'sub' claim).
        is_admin: Whether the user has gitgateway-admin privileges.
        has_access: Whether the user has gitgateway-access.
        token: The raw JWT token.
    """

    username: str
    is_admin: bool = False
    has_access: bool = True
    token: str = ""


def get_idp_public_key(
    idp_url: str,
    fetch_func: Any = None,
) -> str:
    """Fetch and cache the IdP's public key from its metadata endpoint.

    Args:
        idp_url: The base URL of the identity provider.
        fetch_func: Optional callable for fetching URL content (for testing).

    Returns:
        The PEM-encoded public key string.

    Raises:
        AuthError: If the public key cannot be retrieved.
    """
    cache_key = idp_url
    now = time.time()

    # Check cache
    if cache_key in _PUBLIC_KEY_CACHE:
        cached_key, cached_time = _PUBLIC_KEY_CACHE[cache_key]
        if now - cached_time < _PUBLIC_KEY_CACHE_TTL:
            return cached_key

    # Fetch metadata
    metadata_url = f"{idp_url}/metadata"
    try:
        if fetch_func:
            content = fetch_func(metadata_url)
        else:
            response = requests.get(metadata_url, timeout=10)
            response.raise_for_status()
            content = response.text
    except (requests.RequestException, OSError) as exc:
        raise AuthError(
            f"Cannot fetch IdP metadata from {metadata_url}: {exc}",
        ) from exc

    # Extract X509Certificate from XML metadata
    public_key = _extract_public_key_from_metadata(content)
    _PUBLIC_KEY_CACHE[cache_key] = (public_key, now)
    return public_key


def _extract_public_key_from_metadata(metadata_xml: str) -> str:
    """Extract the public key from SAML metadata XML.

    Looks for the X509Certificate element, loads the certificate, and
    extracts the RSA public key in PEM format for JWT verification.

    Args:
        metadata_xml: The raw metadata XML string.

    Returns:
        PEM-encoded RSA public key.

    Raises:
        AuthError: If the certificate cannot be found in the metadata.
    """
    from cryptography.x509 import load_der_x509_certificate  # pylint: disable=import-outside-toplevel
    from cryptography.hazmat.primitives.serialization import (  # pylint: disable=import-outside-toplevel
        Encoding,
        PublicFormat,
    )

    # Simple extraction — find X509Certificate content
    start_tag = "<ds:X509Certificate>"
    end_tag = "</ds:X509Certificate>"

    # Try with namespace prefix
    start_idx = metadata_xml.find(start_tag)
    if start_idx == -1:
        # Try without namespace prefix
        start_tag = "<X509Certificate>"
        end_tag = "</X509Certificate>"
        start_idx = metadata_xml.find(start_tag)

    if start_idx == -1:
        raise AuthError("Cannot find X509Certificate in IdP metadata")

    start_idx += len(start_tag)
    end_idx = metadata_xml.find(end_tag, start_idx)
    if end_idx == -1:
        raise AuthError("Malformed X509Certificate in IdP metadata")

    cert_b64 = metadata_xml[start_idx:end_idx].strip()

    # Decode certificate and extract public key
    try:
        cert_der = base64.b64decode(cert_b64)
        cert = load_der_x509_certificate(cert_der)
        public_key = cert.public_key()
        return public_key.public_bytes(
            Encoding.PEM,
            PublicFormat.SubjectPublicKeyInfo,
        ).decode()
    except (ValueError, TypeError) as exc:
        raise AuthError(
            f"Cannot parse certificate from IdP metadata: {exc}",
        ) from exc


def validate_jwt(
    token: str,
    idp_url: str,
    fetch_func: Any = None,
    algorithms: list[str] | None = None,
) -> AuthenticatedUser:
    """Validate a JWT token and extract user information.

    Args:
        token: The raw JWT token string.
        idp_url: The base URL of the identity provider.
        fetch_func: Optional callable for fetching URL content (for testing).
        algorithms: Allowed signing algorithms. Defaults to RS256.

    Returns:
        An AuthenticatedUser with extracted claims.

    Raises:
        AuthError: If the token is invalid, expired, or malformed.
    """
    if algorithms is None:
        algorithms = ["RS256"]

    public_key_pem = get_idp_public_key(idp_url, fetch_func)

    try:
        payload = jwt.decode(
            token,
            public_key_pem,
            algorithms=algorithms,
            issuer=f"{idp_url}/metadata",
            options={"verify_aud": False},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Token has expired") from exc
    except jwt.InvalidIssuerError as exc:
        raise AuthError("Invalid token issuer") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(f"Invalid token: {exc}") from exc

    username = payload.get("sub", "")
    if not username:
        raise AuthError("Token missing 'sub' claim")

    is_admin = bool(payload.get("gitgateway-admin", False))
    has_access = bool(payload.get("gitgateway-access", True))

    return AuthenticatedUser(
        username=username,
        is_admin=is_admin,
        has_access=has_access,
        token=token,
    )


def parse_basic_auth(authorization_header: str) -> tuple[str, str]:
    """Parse HTTP Basic Authorization header.

    For git operations, username is '__token__' and password is the JWT.

    Args:
        authorization_header: The full Authorization header value.

    Returns:
        Tuple of (username, password).

    Raises:
        AuthError: If the header is malformed.
    """
    if not authorization_header.startswith("Basic "):
        raise AuthError("Invalid authorization header format")

    try:
        encoded = authorization_header[6:]
        decoded = base64.b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise AuthError("Cannot decode Basic auth credentials") from exc

    if ":" not in decoded:
        raise AuthError("Malformed Basic auth credentials")

    username, password = decoded.split(":", 1)
    return username, password


def authenticate_request(
    headers: dict[str, str],
    idp_url: str,
    fetch_func: Any = None,
) -> AuthenticatedUser | None:
    """Authenticate a request using either Bearer token or Basic auth.

    Checks Authorization header for:
    1. Bearer token — validates as JWT directly
    2. Basic auth with username '__token__' — password is the JWT

    Args:
        headers: Request headers dictionary (case-insensitive keys).
        idp_url: The base URL of the identity provider.
        fetch_func: Optional callable for fetching URL content (for testing).

    Returns:
        An AuthenticatedUser if authenticated, None if no auth provided.

    Raises:
        AuthError: If credentials are provided but invalid.
    """
    # Normalize header keys to lowercase
    normalized = {k.lower(): v for k, v in headers.items()}
    auth_header = normalized.get("authorization", "")

    if not auth_header:
        return None

    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        return validate_jwt(token, idp_url, fetch_func)

    if auth_header.startswith("Basic "):
        username, password = parse_basic_auth(auth_header)
        if username == "__token__":
            return validate_jwt(password, idp_url, fetch_func)
        raise AuthError(
            "Basic auth requires username '__token__' with JWT as password",
        )

    raise AuthError(f"Unsupported authorization scheme: {auth_header[:10]}")


def generate_oauth_redirect_url(
    idp_url: str,
    service_name: str = "gitgateway",
    callback_url: str = "",
) -> str:
    """Generate the OAuth redirect URL for login.

    Args:
        idp_url: The base URL of the identity provider.
        service_name: The service name registered with the IdP.
        callback_url: The callback URL after authentication.

    Returns:
        The full redirect URL for the IdP login page.
    """
    base = f"{idp_url}/{service_name}"
    if callback_url:
        return f"{base}?{urlencode({'callback': callback_url})}"
    return base


def create_session_cookie(
    user: AuthenticatedUser,
    secret_key: str,
    max_age: int = 3600,
) -> str:
    """Create a signed session cookie value.

    Args:
        user: The authenticated user.
        secret_key: Secret key for HMAC signing.
        max_age: Cookie lifetime in seconds.

    Returns:
        The signed cookie value (base64-encoded JSON + signature).
    """
    payload = {
        "sub": user.username,
        "admin": user.is_admin,
        "exp": int(time.time()) + max_age,
    }
    payload_json = json.dumps(payload, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(
        payload_json.encode(),
    ).decode()

    signature = hmac.new(
        secret_key.encode(),
        payload_b64.encode(),
        hashlib.sha256,
    ).hexdigest()

    return f"{payload_b64}.{signature}"


def validate_session_cookie(
    cookie_value: str,
    secret_key: str,
) -> AuthenticatedUser | None:
    """Validate a signed session cookie.

    Args:
        cookie_value: The cookie value to validate.
        secret_key: Secret key for HMAC verification.

    Returns:
        An AuthenticatedUser if valid, None if invalid or expired.
    """
    if "." not in cookie_value:
        return None

    payload_b64, signature = cookie_value.rsplit(".", 1)

    # Verify signature
    expected_sig = hmac.new(
        secret_key.encode(),
        payload_b64.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_sig):
        return None

    # Decode payload
    try:
        payload_json = base64.urlsafe_b64decode(payload_b64).decode()
        payload = json.loads(payload_json)
    except (ValueError, json.JSONDecodeError):
        return None

    # Check expiration
    if payload.get("exp", 0) < time.time():
        return None

    return AuthenticatedUser(
        username=payload.get("sub", ""),
        is_admin=bool(payload.get("admin", False)),
    )


def clear_public_key_cache() -> None:
    """Clear the public key cache. Used in testing."""
    _PUBLIC_KEY_CACHE.clear()
