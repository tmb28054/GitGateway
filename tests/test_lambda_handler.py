"""Tests for gitgateway.lambda_handler module."""

from __future__ import annotations

import base64
import json

from gitgateway.lambda_handler import (
    Request,
    Response,
    _dispatch,
    cors_headers,
    error_response,
    handle_options,
    handler,
    parse_api_gateway_event,
    route_request,
)


class TestParseApiGatewayEvent:
    """Tests for parse_api_gateway_event function."""

    def test_basic_get_request(self) -> None:
        """Parses a basic GET request."""
        event = {
            "requestContext": {
                "http": {
                    "method": "GET",
                    "path": "/api/repos",
                    "sourceIp": "1.2.3.4",
                },
            },
            "rawQueryString": "page=1",
            "headers": {"Content-Type": "application/json"},
            "body": None,
            "isBase64Encoded": False,
        }
        request = parse_api_gateway_event(event)
        assert request.method == "GET"
        assert request.path == "/api/repos"
        assert request.query_string == "page=1"
        assert request.headers["content-type"] == "application/json"
        assert request.body == b""
        assert request.source_ip == "1.2.3.4"

    def test_post_with_body(self) -> None:
        """Parses POST request with text body."""
        event = {
            "requestContext": {"http": {"method": "POST", "path": "/api/pr"}},
            "rawQueryString": "",
            "headers": {},
            "body": '{"title": "fix bug"}',
            "isBase64Encoded": False,
        }
        request = parse_api_gateway_event(event)
        assert request.method == "POST"
        assert request.body == b'{"title": "fix bug"}'

    def test_base64_encoded_body(self) -> None:
        """Parses request with base64-encoded body."""
        raw_body = b"\x00\x01\x02PACK"
        event = {
            "requestContext": {
                "http": {"method": "POST", "path": "/team/app.git/git-upload-pack"},
            },
            "rawQueryString": "",
            "headers": {},
            "body": base64.b64encode(raw_body).decode(),
            "isBase64Encoded": True,
        }
        request = parse_api_gateway_event(event)
        assert request.body == raw_body

    def test_missing_request_context(self) -> None:
        """Handles event with minimal fields."""
        event = {
            "rawPath": "/fallback",
            "httpMethod": "PUT",
            "rawQueryString": "",
            "headers": {},
        }
        request = parse_api_gateway_event(event)
        assert request.method == "PUT"
        assert request.path == "/fallback"

    def test_empty_event(self) -> None:
        """Handles completely empty event."""
        request = parse_api_gateway_event({})
        assert request.method == "GET"
        assert request.path == "/"
        assert request.body == b""


class TestRouteRequest:
    """Tests for route_request function."""

    def test_lfs_route(self) -> None:
        """LFS paths routed to lfs handler."""
        request = Request(path="/team/app.git/info/lfs/objects/batch")
        assert route_request(request) == "lfs"

    def test_git_route(self) -> None:
        """Git paths routed to git handler."""
        request = Request(path="/team/app.git/info/refs")
        assert route_request(request) == "git"

    def test_git_upload_pack(self) -> None:
        """Git upload-pack routed to git handler."""
        request = Request(path="/team/app.git/git-upload-pack")
        assert route_request(request) == "git"

    def test_api_route(self) -> None:
        """API paths routed to api handler."""
        request = Request(path="/api/groups")
        assert route_request(request) == "api"

    def test_auth_route(self) -> None:
        """Auth paths routed to auth handler."""
        request = Request(path="/auth/callback")
        assert route_request(request) == "auth"

    def test_web_route(self) -> None:
        """Other paths routed to web handler."""
        request = Request(path="/dashboard")
        assert route_request(request) == "web"

    def test_root_is_web(self) -> None:
        """Root path goes to web handler."""
        request = Request(path="/")
        assert route_request(request) == "web"

    def test_lfs_takes_priority_over_git(self) -> None:
        """LFS paths match before git paths."""
        request = Request(path="/team/app.git/info/lfs/locks")
        assert route_request(request) == "lfs"


class TestResponse:
    """Tests for Response dataclass."""

    def test_string_body_response(self) -> None:
        """Converts string body to API Gateway format."""
        response = Response(
            status_code=200,
            body='{"ok": true}',
            headers={"Content-Type": "application/json"},
        )
        result = response.to_api_gateway_response()
        assert result["statusCode"] == 200
        assert result["body"] == '{"ok": true}'
        assert result["isBase64Encoded"] is False

    def test_bytes_body_response(self) -> None:
        """Converts bytes body to base64-encoded API Gateway format."""
        response = Response(
            status_code=200,
            body=b"\x00\x01PACK",
            headers={"Content-Type": "application/octet-stream"},
        )
        result = response.to_api_gateway_response()
        assert result["isBase64Encoded"] is True
        assert base64.b64decode(result["body"]) == b"\x00\x01PACK"

    def test_empty_body(self) -> None:
        """Handles empty string body."""
        response = Response(status_code=204)
        result = response.to_api_gateway_response()
        assert result["body"] == ""


class TestErrorResponse:
    """Tests for error_response function."""

    def test_json_error(self) -> None:
        """Creates JSON error response."""
        resp = error_response(404, "Not found")
        assert resp.status_code == 404
        body = json.loads(resp.body)
        assert body["error"] == "Not found"
        assert resp.headers["Content-Type"] == "application/json"
        assert resp.headers["X-GitGateway-Error"] == "true"

    def test_text_error(self) -> None:
        """Creates text error response."""
        resp = error_response(500, "Internal error", content_type="text/plain")
        assert resp.body == "Internal error"
        assert resp.headers["Content-Type"] == "text/plain"


class TestCorsHeaders:
    """Tests for cors_headers function."""

    def test_no_wildcard_origin(self) -> None:
        """CORS never returns a wildcard origin; method/header lists stand."""
        headers = cors_headers()
        assert headers.get("Access-Control-Allow-Origin") != "*"
        assert "Access-Control-Allow-Origin" not in headers
        assert "GET" in headers["Access-Control-Allow-Methods"]
        assert "Authorization" in headers["Access-Control-Allow-Headers"]
        assert headers["Vary"] == "Origin"

    def test_reflects_allowlisted_origin(self) -> None:
        """An origin in ALLOWED_ORIGINS is reflected back."""
        from unittest.mock import patch

        req = Request(
            path="/api/x",
            headers={"origin": "https://git.example.com"},
        )
        with patch.dict(
            "os.environ",
            {"ALLOWED_ORIGINS": "https://git.example.com,https://ci.example.com"},
        ):
            headers = cors_headers(req)
        assert headers["Access-Control-Allow-Origin"] == "https://git.example.com"
        assert headers["Access-Control-Allow-Credentials"] == "true"

    def test_rejects_unlisted_origin(self) -> None:
        """An origin not in the allowlist is not reflected."""
        from unittest.mock import patch

        req = Request(
            path="/api/x", headers={"origin": "https://evil.example.com"},
        )
        with patch.dict(
            "os.environ", {"ALLOWED_ORIGINS": "https://git.example.com"},
        ):
            headers = cors_headers(req)
        assert "Access-Control-Allow-Origin" not in headers

    def test_domain_name_fallback(self) -> None:
        """With no ALLOWED_ORIGINS, the DOMAIN_NAME origin is allowed."""
        from unittest.mock import patch

        req = Request(
            path="/api/x", headers={"origin": "https://git.example.com"},
        )
        with patch.dict(
            "os.environ",
            {"ALLOWED_ORIGINS": "", "DOMAIN_NAME": "git.example.com"},
            clear=False,
        ):
            headers = cors_headers(req)
        assert headers["Access-Control-Allow-Origin"] == "https://git.example.com"

    def test_no_config_allows_no_origin(self) -> None:
        """With neither env var set, no cross-origin access is granted."""
        from unittest.mock import patch

        req = Request(
            path="/api/x", headers={"origin": "https://git.example.com"},
        )
        with patch.dict(
            "os.environ",
            {"ALLOWED_ORIGINS": "", "DOMAIN_NAME": ""},
            clear=False,
        ):
            headers = cors_headers(req)
        assert "Access-Control-Allow-Origin" not in headers


class TestHandleOptions:
    """Tests for handle_options function."""

    def test_returns_204_with_cors(self) -> None:
        """Returns 204 with CORS headers (no wildcard origin)."""
        from unittest.mock import patch

        request = Request(
            method="OPTIONS", path="/api/repos",
            headers={"origin": "https://git.example.com"},
        )
        with patch.dict(
            "os.environ", {"ALLOWED_ORIGINS": "https://git.example.com"},
        ):
            response = handle_options(request)
        assert response.status_code == 204
        assert (
            response.headers["Access-Control-Allow-Origin"]
            == "https://git.example.com"
        )


class TestHandler:
    """Tests for main Lambda handler function."""

    def test_options_returns_cors(self) -> None:
        """OPTIONS requests get CORS response."""
        event = {
            "requestContext": {"http": {"method": "OPTIONS", "path": "/api/repos"}},
            "rawQueryString": "",
            "headers": {},
        }
        result = handler(event)
        assert result["statusCode"] == 204

    def test_api_route(self) -> None:
        """API GET /api/groups returns the group list as JSON."""
        from unittest.mock import patch

        from gitgateway.auth import AuthenticatedUser, create_session_cookie

        # The API surface is now fail-closed: an authenticated session is
        # required. Attach a valid session cookie so the list route runs.
        cookie = create_session_cookie(
            AuthenticatedUser(username="alice", is_admin=True),
            "gitgateway-session-secret",
        )
        event = {
            "requestContext": {"http": {"method": "GET", "path": "/api/groups"}},
            "rawQueryString": "",
            "headers": {"cookie": f"session={cookie}"},
        }
        with patch("gitgateway.db.list_groups", return_value=[]):
            result = handler(event)
        assert result["statusCode"] == 200
        assert result["headers"]["Content-Type"] == "application/json"
        assert json.loads(result["body"]) == []

    def test_git_route_requires_auth(self) -> None:
        """Unauthenticated git requests are rejected with 401."""
        event = {
            "requestContext": {
                "http": {"method": "GET", "path": "/team/app.git/info/refs"},
            },
            "rawQueryString": "service=git-upload-pack",
            "headers": {},
        }
        result = handler(event)
        assert result["statusCode"] == 401
        assert "WWW-Authenticate" in result["headers"]

    def test_git_route_invalid_url(self) -> None:
        """A git path that doesn't match group/repo.git returns 400."""
        event = {
            "requestContext": {
                "http": {"method": "GET", "path": "/notgit/info/refs"},
            },
            "rawQueryString": "",
            "headers": {},
        }
        result = handler(event)
        # Falls through to web handling (not a valid git URL), so not 401.
        assert result["statusCode"] in (200, 302, 404)

    def test_lfs_route(self) -> None:
        """LFS batch requests return the git-lfs+json content type."""
        event = {
            "requestContext": {
                "http": {
                    "method": "POST",
                    "path": "/team/app.git/info/lfs/objects/batch",
                },
            },
            "rawQueryString": "",
            "headers": {},
        }
        result = handler(event)
        assert result["headers"]["Content-Type"] == (
            "application/vnd.git-lfs+json"
        )

    def test_auth_route_get_serves_fragment_bootstrap(self) -> None:
        """A tokenless GET auth callback serves the fragment bootstrap (200).

        The IdP returns the token in the URL fragment, invisible to the
        server, so the first hit returns an HTML page that recovers it.
        """
        event = {
            "requestContext": {"http": {"method": "GET", "path": "/auth/callback"}},
            "rawQueryString": "",
            "headers": {},
        }
        result = handler(event)
        assert result["statusCode"] == 200
        assert "location.hash" in result["body"]

    def test_auth_route_post_missing_token(self) -> None:
        """A POST auth callback with no token returns 400."""
        event = {
            "requestContext": {"http": {"method": "POST", "path": "/auth/callback"}},
            "rawQueryString": "",
            "headers": {},
            "body": "",
        }
        result = handler(event)
        assert result["statusCode"] == 400
        assert "Missing token" in result["body"]

    def test_web_route_redirects_to_idp_when_unauthenticated(self) -> None:
        """Unauthenticated web request redirects to the IdP login."""
        from unittest.mock import patch

        event = {
            "requestContext": {"http": {"method": "GET", "path": "/"}},
            "rawQueryString": "",
            "headers": {},
        }
        with patch.dict(
            "os.environ",
            {"IDP_URL": "https://idp.example.com", "DOMAIN_NAME": "git.example.com"},
        ):
            result = handler(event)
        assert result["statusCode"] == 302
        assert "idp.example.com" in result["headers"]["Location"]

    def test_handler_with_context(self) -> None:
        """Handler accepts a context argument."""
        from unittest.mock import patch

        event = {
            "requestContext": {"http": {"method": "GET", "path": "/"}},
            "rawQueryString": "",
            "headers": {},
        }
        with patch.dict(
            "os.environ",
            {"IDP_URL": "https://idp.example.com", "DOMAIN_NAME": "git.example.com"},
        ):
            result = handler(event, context=object())
        assert result["statusCode"] == 302


class TestDispatch:
    """Tests for _dispatch function."""

    def test_api_dispatch(self) -> None:
        """API handler returns JSON with CORS method/header metadata.

        With no Origin header the response carries no
        Access-Control-Allow-Origin (origins are allowlisted, not
        wildcarded), but the method/header/Vary metadata is always present.
        """
        request = Request(path="/api/test")
        response = _dispatch("api", request)
        assert response.headers["Content-Type"] == "application/json"
        assert "Access-Control-Allow-Methods" in response.headers
        assert response.headers["Vary"] == "Origin"
        assert "Access-Control-Allow-Origin" not in response.headers

    def test_git_dispatch_requires_auth(self) -> None:
        """Git dispatch without credentials returns 401."""
        request = Request(path="/team/app.git/info/refs")
        response = _dispatch("git", request)
        assert response.status_code == 401
        assert "WWW-Authenticate" in response.headers

    def test_git_dispatch_invalid_url(self) -> None:
        """Git dispatch with a non-git path returns 400."""
        request = Request(path="/not-a-git-url")
        response = _dispatch("git", request)
        assert response.status_code == 400

    def test_lfs_dispatch(self) -> None:
        """LFS handler returns git-lfs+json."""
        request = Request(path="/team/app.git/info/lfs/objects/batch")
        response = _dispatch("lfs", request)
        assert response.headers["Content-Type"] == "application/vnd.git-lfs+json"

    def test_auth_dispatch_serves_fragment_bootstrap(self) -> None:
        """Auth callback without a query token serves the fragment bootstrap.

        The IdP delivers the token in the URL fragment, which the server
        cannot see, so a tokenless hit returns an HTML page that recovers the
        token from location.hash (and shows "Missing token parameter" only if
        the fragment has none).
        """
        request = Request(path="/auth/callback")
        response = _dispatch("auth", request)
        assert response.status_code == 200
        assert "location.hash" in response.body
        assert "Missing token parameter" in response.body

    def test_web_dispatch(self) -> None:
        """Web handler returns HTML."""
        request = Request(path="/dashboard")
        response = _dispatch("web", request)
        assert response.headers["Content-Type"] == "text/html"
        assert "GitGateway" in response.body


class TestAdminAuditPage:
    """Tests for the admin audit log page."""

    def test_route_dispatches_to_audit_page(self) -> None:
        """/admin/audit routes to the audit renderer."""
        from unittest.mock import patch

        from gitgateway.audit import AuditEvent
        from gitgateway.lambda_handler import _get_admin_content

        event = AuditEvent(
            timestamp="2025-01-01T00:00:00Z",
            actor="alice",
            action="repo.created",
            resource="team/app",
            ip_address="10.0.0.1",
            details={"description": "New app"},
        )
        with patch(
            "gitgateway.audit.list_all_events",
            return_value=[event],
        ):
            html = _get_admin_content("/admin/audit")

        assert "Path:" not in html
        assert "Audit Log" in html
        assert "alice" in html
        assert "repo.created" in html
        assert "team/app" in html
        assert "10.0.0.1" in html
        assert "description=New app" in html

    def test_empty_events(self) -> None:
        """Renders empty-state message with no events."""
        from unittest.mock import patch

        from gitgateway.lambda_handler import _render_admin_audit_page

        with patch(
            "gitgateway.audit.list_all_events",
            return_value=[],
        ):
            html = _render_admin_audit_page()

        assert "No audit events recorded yet." in html
        assert "0 events" in html

    def test_single_event_count_label(self) -> None:
        """Uses singular label for exactly one event."""
        from unittest.mock import patch

        from gitgateway.audit import AuditEvent
        from gitgateway.lambda_handler import _render_admin_audit_page

        with patch(
            "gitgateway.audit.list_all_events",
            return_value=[AuditEvent(actor="a", action="x", resource="r")],
        ):
            html = _render_admin_audit_page()

        assert "1 event." in html

    def test_escapes_event_content(self) -> None:
        """Escapes HTML in event fields to prevent injection."""
        from unittest.mock import patch

        from gitgateway.audit import AuditEvent
        from gitgateway.lambda_handler import _render_admin_audit_page

        event = AuditEvent(
            timestamp="2025-01-01T00:00:00Z",
            actor="<script>alert(1)</script>",
            action="push",
            resource="team/app",
        )
        with patch(
            "gitgateway.audit.list_all_events",
            return_value=[event],
        ):
            html = _render_admin_audit_page()

        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_handles_load_error(self) -> None:
        """Shows an error banner when events cannot be loaded."""
        from unittest.mock import patch

        from gitgateway.lambda_handler import _render_admin_audit_page

        with patch(
            "gitgateway.audit.list_all_events",
            side_effect=RuntimeError("boom"),
        ):
            html = _render_admin_audit_page()

        assert "Could not load audit events." in html
