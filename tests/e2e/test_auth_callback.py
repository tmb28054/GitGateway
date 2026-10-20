"""E2E tests: OAuth callback / JWT validation against the deployed Lambda.

These tests exercise the full /auth/callback path on the live stack — the
exact path two production incidents slipped through:

1. A missing `defusedxml` dependency in the deployed Lambda layer caused the
   callback to raise ModuleNotFoundError and return HTTP 500.
2. An issuer/origin mismatch (idp_url carrying a "/git" path) caused valid
   tokens to be rejected on the issuer check.

Unit tests passed in both cases because they ran against a local venv and
mocked IdP metadata. Only an end-to-end test that posts a *real* IdP JWT to
the *deployed* callback would have caught them before they reached a user.

Provide a real, currently-valid IdP token via GITGATEWAY_TEST_JWT to run the
"valid token" assertions; the unauthenticated/invalid cases always run.

    export GITGATEWAY_TEST_JWT="<a real IdP-issued JWT for this service>"
    pytest -m e2e tests/e2e/test_auth_callback.py
"""

from __future__ import annotations

import os

import pytest
import requests


BASE_URL = os.environ.get("GITGATEWAY_BASE_URL", "https://git.botthouse.net")
CALLBACK_URL = f"{BASE_URL}/auth/callback"


@pytest.mark.e2e
class TestAuthCallbackReachable:
    """The callback endpoint must never 500 — it should always respond
    with a usable page or a clean auth decision."""

    def test_get_without_token_serves_bootstrap_not_500(self) -> None:
        """A tokenless GET returns the fragment-recovery bootstrap (200).

        Regression guard for the defusedxml ModuleNotFoundError incident:
        the callback must not raise an unhandled exception (500). The IdP
        delivers the token in the URL fragment, so a tokenless GET returns
        an HTML bootstrap that recovers it client-side.
        """
        resp = requests.get(CALLBACK_URL, timeout=30)
        assert resp.status_code == 200, (
            f"Callback GET should serve the bootstrap page, "
            f"got {resp.status_code}: {resp.text[:200]}"
        )
        assert "location.hash" in resp.text, (
            "Bootstrap page should read the URL fragment"
        )

    def test_post_invalid_token_is_rejected_not_500(self) -> None:
        """A POSTed invalid token is rejected cleanly (401), never 500.

        This guards both incidents at once: a 500 would mean the validation
        code path itself is broken (e.g. a missing dependency), whereas a
        401 means validation ran and correctly rejected a bad token.
        """
        resp = requests.post(
            CALLBACK_URL,
            data={"token": "not-a-valid-jwt"},
            timeout=30,
            allow_redirects=False,
        )
        assert resp.status_code == 401, (
            f"Invalid token should be rejected with 401, "
            f"got {resp.status_code}: {resp.text[:200]}"
        )
        assert "set-cookie" not in {k.lower() for k in resp.headers}, (
            "No session cookie should be issued for an invalid token"
        )


@pytest.mark.e2e
class TestAuthCallbackValidToken:
    """Verify a real IdP token completes sign-in end-to-end.

    Requires GITGATEWAY_TEST_JWT to be a current, valid token the IdP issued
    for this service. These assertions are the ones that would have caught
    the issuer/origin bug (which rejected valid tokens with 401) and the
    defusedxml bug (which 500'd before validation could succeed).
    """

    @staticmethod
    def _jwt() -> str:
        token = os.environ.get("GITGATEWAY_TEST_JWT", "")
        if not token:
            pytest.skip("GITGATEWAY_TEST_JWT not set")
        return token

    def test_post_valid_token_sets_session_and_redirects(self) -> None:
        """A valid JWT POSTed to the callback yields 302 + session cookie."""
        resp = requests.post(
            CALLBACK_URL,
            data={"token": self._jwt()},
            timeout=30,
            allow_redirects=False,
        )
        assert resp.status_code == 302, (
            f"Valid token should redirect (302); got {resp.status_code}. "
            f"A 500 indicates a broken validation path (e.g. a missing "
            f"Lambda-layer dependency); a 401 indicates the token was "
            f"rejected (e.g. issuer/audience mismatch). Body: "
            f"{resp.text[:200]}"
        )
        assert resp.headers.get("Location") == "/", (
            "Successful sign-in should redirect to the dashboard"
        )
        set_cookie = resp.headers.get("Set-Cookie", "")
        assert "session=" in set_cookie, (
            "A session cookie must be set on successful sign-in"
        )
        # The session cookie must be hardened.
        assert "HttpOnly" in set_cookie and "Secure" in set_cookie, (
            f"Session cookie should be HttpOnly+Secure: {set_cookie}"
        )

    def test_authenticated_session_reaches_dashboard(self) -> None:
        """The issued session cookie authenticates a follow-up web request.

        Confirms the whole round trip works: validate token -> set session
        -> session is accepted on a subsequent request (no IdP redirect).
        """
        login = requests.post(
            CALLBACK_URL,
            data={"token": self._jwt()},
            timeout=30,
            allow_redirects=False,
        )
        if login.status_code != 302:
            pytest.skip(
                f"Could not establish a session (status "
                f"{login.status_code}); covered by the 302 test"
            )
        session_cookie = login.cookies.get("session")
        assert session_cookie, "Expected a session cookie from the callback"

        home = requests.get(
            f"{BASE_URL}/",
            cookies={"session": session_cookie},
            timeout=30,
            allow_redirects=False,
        )
        # With a valid session the home page renders (200) rather than
        # redirecting to the IdP (302 to the IdP login).
        assert home.status_code == 200, (
            f"Authenticated home request should render (200), got "
            f"{home.status_code} (location={home.headers.get('Location')})"
        )
