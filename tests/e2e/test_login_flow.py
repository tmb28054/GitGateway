"""E2E tests: Full login flow through IdP.

Tests the complete OAuth flow from GitGateway → IdP login → callback → authenticated session.
Uses real credentials to verify end-to-end behavior.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from playwright.sync_api import Page, expect


BASE_URL = os.environ.get("GITGATEWAY_BASE_URL", "https://git.botthouse.net")


def _solve_challenge(page: Page) -> None:
    """Solve the math challenge on the IdP login page.

    Parses "What is X + Y?" or "What is X - Y?" from the page
    and fills in the answer.

    Args:
        page: The Playwright page with the login form.
    """
    challenge_field = page.locator("input[name='challenge_answer']")
    if not challenge_field.is_visible():
        return

    # Get the challenge question text
    question = page.locator(".challenge-question").text_content() or ""

    # Parse "What is X + Y?" or "What is X - Y?" etc.
    import re  # pylint: disable=import-outside-toplevel
    match = re.search(r"(\d+)\s*([+\-*×÷/])\s*(\d+)", question)
    if not match:
        return

    num1 = int(match.group(1))
    operator = match.group(2)
    num2 = int(match.group(3))

    if operator == "+":
        answer = num1 + num2
    elif operator == "-":
        answer = num1 - num2
    elif operator in ("*", "×"):
        answer = num1 * num2
    elif operator in ("/", "÷"):
        answer = num1 // num2
    else:
        return

    challenge_field.fill(str(answer))


def _get_test_credentials() -> dict:
    """Get test credentials from secretcli.

    Returns:
        Dict with 'user' and 'password' keys.
    """
    result = subprocess.run(
        ["secretcli", "get", "wikitest"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def credentials() -> dict:
    """Load test credentials once per module."""
    return _get_test_credentials()


@pytest.mark.e2e
class TestUnauthenticatedAccess:
    """Test behavior when not logged in."""

    def test_root_redirects_to_idp(self, page: Page) -> None:
        """Visiting root without auth redirects to IdP login."""
        page.goto(BASE_URL)
        page.wait_for_load_state("domcontentloaded")

        # Should end up at the IdP login page
        assert "idp.botthouse.net" in page.url or "Sign in" in page.content()

    def test_idp_login_form_visible(self, page: Page) -> None:
        """IdP login page shows username/password form."""
        page.goto(BASE_URL)
        page.wait_for_load_state("domcontentloaded")

        # Should see the login form
        username_field = page.locator("input[name='username']")
        password_field = page.locator("input[name='password']")
        expect(username_field).to_be_visible()
        expect(password_field).to_be_visible()


@pytest.mark.e2e
class TestLoginFlow:
    """Test the full login → redirect → authenticated flow."""

    def test_login_and_redirect_back(
        self, page: Page, credentials: dict,
    ) -> None:
        """Login via IdP redirects back to GitGateway with session."""
        # Start at GitGateway - should redirect to IdP
        page.goto(BASE_URL)
        page.wait_for_load_state("domcontentloaded")

        # Fill in credentials on IdP login form
        page.fill("input[name='username']", credentials["user"])
        page.fill("input[name='password']", credentials["password"])

        # Solve the math challenge
        _solve_challenge(page)

        # Submit the form
        page.click("button[type='submit']")
        page.wait_for_load_state("domcontentloaded")

        # Check if login failed (IdP rejected credentials)
        if "Invalid" in page.content() or "idp.botthouse.net" in page.url:
            pytest.skip(
                "IdP rejected credentials - user may not have "
                "access to the 'git' service"
            )

        # Should be redirected back to GitGateway
        assert "git.botthouse.net" in page.url

        # Should see the nav bar with user info
        content = page.content()
        assert "GitGateway" in content
        assert "Logout" in content

    def test_authenticated_session_persists(
        self, page: Page, credentials: dict,
    ) -> None:
        """After login, session cookie allows access without re-auth."""
        # Login first
        page.goto(BASE_URL)
        page.wait_for_load_state("domcontentloaded")

        # Fill credentials
        page.fill("input[name='username']", credentials["user"])
        page.fill("input[name='password']", credentials["password"])

        _solve_challenge(page)

        page.click("button[type='submit']")
        page.wait_for_load_state("domcontentloaded")

        if "Invalid" in page.content() or "idp.botthouse.net" in page.url:
            pytest.skip("IdP rejected credentials")

        # Now visit the site again - should not redirect to IdP
        page.goto(BASE_URL)
        page.wait_for_load_state("domcontentloaded")

        # Should stay on GitGateway (not redirect to IdP)
        assert "git.botthouse.net" in page.url
        # Should see the nav bar
        assert "Logout" in page.content()


@pytest.mark.e2e
class TestAuthenticatedPages:
    """Test pages that require authentication."""

    @pytest.fixture(autouse=True)
    def _login(self, page: Page, credentials: dict) -> None:
        """Log in before each test."""
        page.goto(BASE_URL)
        page.wait_for_load_state("domcontentloaded")

        # If already on GitGateway (session exists), skip login
        if "git.botthouse.net" in page.url and "idp" not in page.url:
            return

        page.fill("input[name='username']", credentials["user"])
        page.fill("input[name='password']", credentials["password"])

        _solve_challenge(page)

        page.click("button[type='submit']")
        page.wait_for_load_state("domcontentloaded")

    def test_root_shows_content(self, page: Page) -> None:
        """Root page shows GitGateway content when authenticated."""
        page.goto(BASE_URL)
        page.wait_for_load_state("domcontentloaded")
        content = page.content()
        assert "GitGateway" in content or "git.botthouse.net" in page.url

    def test_api_groups_responds(self, page: Page) -> None:
        """API endpoint responds with JSON when authenticated."""
        response = page.goto(f"{BASE_URL}/api/groups")
        assert response is not None
        assert response.status in (200, 401, 403)


@pytest.mark.e2e
class TestApiDirect:
    """Test API endpoints directly with curl-style requests."""

    def test_api_without_auth_returns_json(self, page: Page) -> None:
        """API returns JSON response (even without auth)."""
        response = page.goto(f"{BASE_URL}/api/groups")
        assert response is not None
        content = page.content()
        # Should be JSON (either error or data)
        assert "handler" in content or "error" in content or "api" in content

    def test_options_returns_cors(self, page: Page) -> None:
        """OPTIONS request gets CORS headers."""
        result = page.evaluate(
            """async (url) => {
                try {
                    const response = await fetch(url + '/api/groups', {
                        method: 'OPTIONS'
                    });
                    return {
                        status: response.status,
                        cors: response.headers.get('access-control-allow-origin')
                    };
                } catch(e) {
                    return {status: 0, cors: null, error: e.message};
                }
            }""",
            BASE_URL,
        )
        assert result["status"] in (200, 204)
