"""Tests for gitgateway.auth module."""

from __future__ import annotations

import base64
import time

import jwt as pyjwt
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509 import (
    CertificateBuilder,
    Name,
    NameAttribute,
)
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives.hashes import SHA256
import datetime

from gitgateway.auth import (
    AuthError,
    AuthenticatedUser,
    authenticate_request,
    clear_public_key_cache,
    create_session_cookie,
    generate_oauth_redirect_url,
    parse_basic_auth,
    validate_jwt,
    validate_session_cookie,
    get_idp_public_key,
    _extract_public_key_from_metadata,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear public key cache before each test."""
    clear_public_key_cache()
    yield
    clear_public_key_cache()


@pytest.fixture
def rsa_keypair():
    """Generate an RSA key pair for testing."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    public_key = private_key.public_key()

    # Create self-signed certificate
    subject = issuer = Name([
        NameAttribute(NameOID.COMMON_NAME, "idp.botthouse.net"),
    ])
    cert = (
        CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(1000)
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=365),
        )
        .sign(private_key, SHA256())
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    cert_der_b64 = base64.b64encode(
        cert.public_bytes(serialization.Encoding.DER),
    ).decode()

    private_key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()

    return private_key_pem, cert_pem, cert_der_b64


@pytest.fixture
def metadata_xml(rsa_keypair):
    """Generate fake IdP metadata XML."""
    _, _, cert_der_b64 = rsa_keypair
    return (
        '<?xml version="1.0"?>'
        "<EntityDescriptor>"
        "<KeyDescriptor>"
        "<ds:KeyInfo>"
        f"<ds:X509Certificate>{cert_der_b64}</ds:X509Certificate>"
        "</ds:KeyInfo>"
        "</KeyDescriptor>"
        "</EntityDescriptor>"
    )


@pytest.fixture
def make_token(rsa_keypair):
    """Factory for creating signed JWT tokens."""
    private_key_pem, _, _ = rsa_keypair

    def _make(
        sub: str = "testuser",
        is_admin: bool = False,
        has_access: bool = True,
        exp_offset: int = 3600,
        issuer: str = "https://idp.botthouse.net/metadata",
    ) -> str:
        payload = {
            "sub": sub,
            "iss": issuer,
            "exp": int(time.time()) + exp_offset,
            "gitgateway-admin": is_admin,
            "gitgateway-access": has_access,
        }
        return pyjwt.encode(payload, private_key_pem, algorithm="RS256")

    return _make


class TestGetIdpPublicKey:
    """Tests for get_idp_public_key function."""

    def test_fetches_and_caches_key(self, metadata_xml) -> None:
        """Fetches public key from metadata and caches it."""
        def fetch(url):
            return metadata_xml

        key = get_idp_public_key("https://idp.botthouse.net", fetch)
        assert "BEGIN PUBLIC KEY" in key

        # Second call should use cache (fetch not called again)
        call_count = {"n": 0}

        def fetch_counting(url):
            call_count["n"] += 1
            return metadata_xml

        get_idp_public_key("https://idp.botthouse.net", fetch_counting)
        assert call_count["n"] == 0

    def test_refetches_after_cache_expires(self, metadata_xml) -> None:
        """Refetches key after TTL expires."""
        import gitgateway.auth as auth_mod  # pylint: disable=import-outside-toplevel

        def fetch(url):
            return metadata_xml

        # Populate cache
        get_idp_public_key("https://idp.expire.test", fetch)

        # Manually expire the cache entry
        cache_key = "https://idp.expire.test"
        cached_key, _ = auth_mod._PUBLIC_KEY_CACHE[cache_key]
        auth_mod._PUBLIC_KEY_CACHE[cache_key] = (cached_key, 0)

        # Should refetch
        call_count = {"n": 0}

        def fetch_counting(url):
            call_count["n"] += 1
            return metadata_xml

        get_idp_public_key("https://idp.expire.test", fetch_counting)
        assert call_count["n"] == 1

    def test_raises_on_fetch_error(self) -> None:
        """Raises AuthError when metadata cannot be fetched."""
        def fetch_error(url):
            raise OSError("connection refused")

        with pytest.raises(AuthError, match="Cannot fetch IdP metadata"):
            get_idp_public_key("https://bad.host", fetch_error)

    def test_raises_on_missing_certificate(self) -> None:
        """Raises AuthError when metadata has no certificate."""
        def fetch(url):
            return "<EntityDescriptor>no cert here</EntityDescriptor>"

        with pytest.raises(AuthError, match="Cannot find X509Certificate"):
            get_idp_public_key("https://idp.example.com", fetch)

    def test_uses_requests_when_no_fetch_func(
        self, metadata_xml, monkeypatch,
    ) -> None:
        """Falls back to requests.get when fetch_func is None."""
        class FakeResponse:
            """Fake requests response."""

            text = metadata_xml
            status_code = 200

            def raise_for_status(self):
                """No-op for success."""

        monkeypatch.setattr(
            "gitgateway.auth.requests.get",
            lambda url, timeout: FakeResponse(),
        )
        key = get_idp_public_key("https://idp.test.net")
        assert "BEGIN PUBLIC KEY" in key

    def test_requests_failure_raises(self, monkeypatch) -> None:
        """Raises AuthError when requests.get fails."""
        monkeypatch.setattr(
            "gitgateway.auth.requests.get",
            lambda url, timeout: (_ for _ in ()).throw(
                requests.RequestException("timeout"),
            ),
        )
        with pytest.raises(AuthError, match="Cannot fetch IdP metadata"):
            get_idp_public_key("https://idp.fail.net")


class TestExtractPublicKeyFromMetadata:
    """Tests for _extract_public_key_from_metadata."""

    def test_extracts_with_ds_prefix(self, rsa_keypair) -> None:
        """Extracts certificate with ds: namespace prefix."""
        _, _, cert_der_b64 = rsa_keypair
        xml = f"<ds:X509Certificate>{cert_der_b64}</ds:X509Certificate>"
        result = _extract_public_key_from_metadata(xml)
        assert "BEGIN PUBLIC KEY" in result

    def test_extracts_without_prefix(self, rsa_keypair) -> None:
        """Extracts certificate without namespace prefix."""
        _, _, cert_der_b64 = rsa_keypair
        xml = f"<X509Certificate>{cert_der_b64}</X509Certificate>"
        result = _extract_public_key_from_metadata(xml)
        assert "BEGIN PUBLIC KEY" in result

    def test_raises_on_malformed_end_tag(self) -> None:
        """Raises AuthError when end tag is missing."""
        xml = "<ds:X509Certificate>ABCDEF"
        with pytest.raises(AuthError, match="Malformed"):
            _extract_public_key_from_metadata(xml)

    def test_raises_on_no_certificate(self) -> None:
        """Raises AuthError when no certificate element exists."""
        xml = "<Metadata>nothing</Metadata>"
        with pytest.raises(AuthError, match="Cannot find"):
            _extract_public_key_from_metadata(xml)

    def test_raises_on_invalid_cert_data(self) -> None:
        """Raises AuthError when certificate data is invalid."""
        xml = "<ds:X509Certificate>not_valid_base64!!!</ds:X509Certificate>"
        with pytest.raises(AuthError, match="Cannot parse certificate"):
            _extract_public_key_from_metadata(xml)


class TestValidateJwt:
    """Tests for validate_jwt function."""

    def test_valid_token(self, metadata_xml, make_token) -> None:
        """Validates a correct token and extracts claims."""
        def fetch(url):
            return metadata_xml

        token = make_token(sub="alice", is_admin=True)
        user = validate_jwt(token, "https://idp.botthouse.net", fetch)
        assert user.username == "alice"
        assert user.is_admin is True
        assert user.has_access is True
        assert user.token == token

    def test_expired_token(self, metadata_xml, make_token) -> None:
        """Rejects expired token."""
        def fetch(url):
            return metadata_xml

        token = make_token(exp_offset=-100)
        with pytest.raises(AuthError, match="expired"):
            validate_jwt(token, "https://idp.botthouse.net", fetch)

    def test_invalid_issuer(self, metadata_xml, rsa_keypair) -> None:
        """Rejects token with wrong issuer."""
        private_key_pem, _, _ = rsa_keypair

        def fetch(url):
            return metadata_xml

        # Create token with wrong issuer that will trigger InvalidIssuerError
        payload = {
            "sub": "baduser",
            "iss": "https://evil.com/metadata",
            "exp": int(time.time()) + 3600,
        }
        token = pyjwt.encode(payload, private_key_pem, algorithm="RS256")
        with pytest.raises(AuthError, match="Invalid token"):
            validate_jwt(token, "https://idp.botthouse.net", fetch)

    def test_malformed_token(self, metadata_xml) -> None:
        """Rejects completely malformed token."""
        def fetch(url):
            return metadata_xml

        with pytest.raises(AuthError, match="Invalid token"):
            validate_jwt("not.a.jwt", "https://idp.botthouse.net", fetch)

    def test_missing_sub_claim(self, metadata_xml, rsa_keypair) -> None:
        """Rejects token without sub claim."""
        private_key_pem, _, _ = rsa_keypair

        def fetch(url):
            return metadata_xml

        payload = {
            "iss": "https://idp.botthouse.net/metadata",
            "exp": int(time.time()) + 3600,
        }
        token = pyjwt.encode(payload, private_key_pem, algorithm="RS256")
        with pytest.raises(AuthError, match="missing 'sub'"):
            validate_jwt(token, "https://idp.botthouse.net", fetch)

    def test_non_admin_user(self, metadata_xml, make_token) -> None:
        """Regular user has is_admin=False."""
        def fetch(url):
            return metadata_xml

        token = make_token(sub="bob", is_admin=False)
        user = validate_jwt(token, "https://idp.botthouse.net", fetch)
        assert user.is_admin is False

    def test_explicit_algorithms_param(self, metadata_xml, make_token) -> None:
        """Works when algorithms parameter is explicitly provided."""
        def fetch(url):
            return metadata_xml

        token = make_token(sub="dave")
        user = validate_jwt(
            token, "https://idp.botthouse.net", fetch,
            algorithms=["RS256"],
        )
        assert user.username == "dave"


class TestParseBasicAuth:
    """Tests for parse_basic_auth function."""

    def test_valid_basic_auth(self) -> None:
        """Parses valid Basic auth header."""
        creds = base64.b64encode(b"__token__:myjwt123").decode()
        username, password = parse_basic_auth(f"Basic {creds}")
        assert username == "__token__"
        assert password == "myjwt123"

    def test_password_with_colon(self) -> None:
        """Handles passwords containing colons."""
        creds = base64.b64encode(b"user:pass:with:colons").decode()
        username, password = parse_basic_auth(f"Basic {creds}")
        assert username == "user"
        assert password == "pass:with:colons"

    def test_rejects_non_basic_scheme(self) -> None:
        """Rejects non-Basic auth headers."""
        with pytest.raises(AuthError, match="Invalid authorization"):
            parse_basic_auth("Bearer token123")

    def test_rejects_invalid_base64(self) -> None:
        """Rejects invalid base64 encoding."""
        with pytest.raises(AuthError, match="Cannot decode"):
            parse_basic_auth("Basic !!!invalid!!!")

    def test_rejects_missing_colon(self) -> None:
        """Rejects credentials without colon separator."""
        creds = base64.b64encode(b"nocolonhere").decode()
        with pytest.raises(AuthError, match="Malformed"):
            parse_basic_auth(f"Basic {creds}")


class TestAuthenticateRequest:
    """Tests for authenticate_request function."""

    def test_bearer_token(self, metadata_xml, make_token) -> None:
        """Authenticates via Bearer token."""
        def fetch(url):
            return metadata_xml

        token = make_token(sub="alice")
        headers = {"Authorization": f"Bearer {token}"}
        user = authenticate_request(
            headers, "https://idp.botthouse.net", fetch,
        )
        assert user is not None
        assert user.username == "alice"

    def test_basic_auth_with_token(self, metadata_xml, make_token) -> None:
        """Authenticates via Basic auth with __token__ username."""
        def fetch(url):
            return metadata_xml

        token = make_token(sub="bob")
        creds = base64.b64encode(
            f"__token__:{token}".encode(),
        ).decode()
        headers = {"Authorization": f"Basic {creds}"}
        user = authenticate_request(
            headers, "https://idp.botthouse.net", fetch,
        )
        assert user is not None
        assert user.username == "bob"

    def test_basic_auth_wrong_username(self, metadata_xml) -> None:
        """Rejects Basic auth with non-__token__ username."""
        def fetch(url):
            return metadata_xml

        creds = base64.b64encode(b"baduser:somepass").decode()
        headers = {"Authorization": f"Basic {creds}"}
        with pytest.raises(AuthError, match="requires username '__token__'"):
            authenticate_request(
                headers, "https://idp.botthouse.net", fetch,
            )

    def test_no_auth_header(self) -> None:
        """Returns None when no auth header present."""
        result = authenticate_request({}, "https://idp.botthouse.net")
        assert result is None

    def test_empty_auth_header(self) -> None:
        """Returns None when auth header is empty string."""
        result = authenticate_request(
            {"Authorization": ""}, "https://idp.botthouse.net",
        )
        assert result is None

    def test_unsupported_scheme(self) -> None:
        """Raises AuthError on unsupported auth scheme."""
        headers = {"Authorization": "Digest realm=test"}
        with pytest.raises(AuthError, match="Unsupported authorization"):
            authenticate_request(headers, "https://idp.botthouse.net")

    def test_case_insensitive_headers(self, metadata_xml, make_token) -> None:
        """Handles case-insensitive header keys."""
        def fetch(url):
            return metadata_xml

        token = make_token(sub="charlie")
        headers = {"AUTHORIZATION": f"Bearer {token}"}
        user = authenticate_request(
            headers, "https://idp.botthouse.net", fetch,
        )
        assert user is not None
        assert user.username == "charlie"


class TestOAuthRedirectUrl:
    """Tests for generate_oauth_redirect_url function."""

    def test_basic_url(self) -> None:
        """Generates basic redirect URL."""
        url = generate_oauth_redirect_url("https://idp.botthouse.net")
        assert url == "https://idp.botthouse.net/gitgateway"

    def test_with_callback(self) -> None:
        """Includes callback parameter."""
        url = generate_oauth_redirect_url(
            "https://idp.botthouse.net",
            callback_url="https://git.example.com/auth/callback",
        )
        assert "callback=" in url
        assert "git.example.com" in url

    def test_custom_service_name(self) -> None:
        """Uses custom service name."""
        url = generate_oauth_redirect_url(
            "https://idp.botthouse.net",
            service_name="myapp",
        )
        assert url == "https://idp.botthouse.net/myapp"


class TestSessionCookie:
    """Tests for session cookie creation and validation."""

    def test_create_and_validate(self) -> None:
        """Creates and validates a session cookie."""
        user = AuthenticatedUser(username="alice", is_admin=True)
        cookie = create_session_cookie(user, "secret123")
        result = validate_session_cookie(cookie, "secret123")
        assert result is not None
        assert result.username == "alice"
        assert result.is_admin is True

    def test_expired_cookie(self) -> None:
        """Rejects expired session cookie."""
        user = AuthenticatedUser(username="alice")
        cookie = create_session_cookie(user, "secret", max_age=-100)
        result = validate_session_cookie(cookie, "secret")
        assert result is None

    def test_invalid_signature(self) -> None:
        """Rejects cookie with wrong signature."""
        user = AuthenticatedUser(username="alice")
        cookie = create_session_cookie(user, "secret1")
        result = validate_session_cookie(cookie, "secret2")
        assert result is None

    def test_malformed_cookie_no_dot(self) -> None:
        """Rejects cookie without dot separator."""
        result = validate_session_cookie("nodothere", "secret")
        assert result is None

    def test_malformed_cookie_bad_base64(self) -> None:
        """Rejects cookie with invalid base64 payload."""
        result = validate_session_cookie("!!!bad.sig", "secret")
        assert result is None

    def test_non_admin_cookie(self) -> None:
        """Non-admin user cookie has is_admin=False."""
        user = AuthenticatedUser(username="bob", is_admin=False)
        cookie = create_session_cookie(user, "key")
        result = validate_session_cookie(cookie, "key")
        assert result is not None
        assert result.is_admin is False

    def test_corrupted_payload_valid_signature(self) -> None:
        """Rejects cookie with corrupted but decodable payload."""
        # Create a payload that's valid base64 but not valid JSON
        payload_b64 = base64.urlsafe_b64encode(b"not json").decode()
        import hashlib as _hashlib
        import hmac as _hmac

        sig = _hmac.new(
            b"secret",
            payload_b64.encode(),
            _hashlib.sha256,
        ).hexdigest()
        cookie = f"{payload_b64}.{sig}"
        result = validate_session_cookie(cookie, "secret")
        assert result is None
