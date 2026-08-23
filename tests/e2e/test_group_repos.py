"""E2E tests: Group detail page with repository creation.

Verifies that /groups/<name> shows repos, allows creation
for users with contributor+ access, and denies for readonly.
"""

from __future__ import annotations

import os
import uuid

import pytest
import requests
from playwright.sync_api import Page, expect


BASE_URL = os.environ.get("GITGATEWAY_BASE_URL", "https://git.botthouse.net")


def _create_session_cookie(username: str, is_admin: bool = False) -> str:
    """Create a session cookie."""
    from gitgateway.auth import (  # pylint: disable=import-outside-toplevel
        create_session_cookie, AuthenticatedUser,
    )
    user = AuthenticatedUser(username=username, is_admin=is_admin)
    secret = os.environ.get("SESSION_SECRET", "gitgateway-session-secret")
    return create_session_cookie(user, secret, max_age=3600)


def _set_cookie(page: Page, username: str, is_admin: bool = False) -> None:
    """Set session cookie on the page."""
    cookie_value = _create_session_cookie(username, is_admin)
    page.context.add_cookies([{
        "name": "session",
        "value": cookie_value,
        "domain": "git.botthouse.net",
        "path": "/",
        "secure": True,
        "httpOnly": True,
    }])


def _unique_name(prefix: str = "rp") -> str:
    """Generate unique name."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def test_group():
    """Create a test group and clean up."""
    name = _unique_name("grp")
    requests.post(
        f"{BASE_URL}/api/groups",
        json={"name": name, "description": "Repo test group"},
        timeout=10,
    )
    yield name
    requests.delete(f"{BASE_URL}/api/groups/{name}", timeout=10)


@pytest.mark.e2e
class TestGroupDetailPage:
    """Test the group detail page renders correctly."""

    def test_page_loads_for_contributor(
        self, page: Page, test_group: str,
    ) -> None:
        """Group page loads for user with contributor access."""
        username = _unique_name("usr")
        requests.post(
            f"{BASE_URL}/api/access",
            json={
                "username": username,
                "resource": test_group,
                "level": "contributor",
            },
            timeout=10,
        )

        _set_cookie(page, username)
        page.goto(f"{BASE_URL}/groups/{test_group}")
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert test_group in content
        assert "Repositories" in content

        # Cleanup
        requests.delete(
            f"{BASE_URL}/api/access",
            json={"username": username, "resource": test_group},
            timeout=10,
        )

    def test_create_form_visible_for_contributor(
        self, page: Page, test_group: str,
    ) -> None:
        """Create repo form is visible for contributor."""
        username = _unique_name("usr")
        requests.post(
            f"{BASE_URL}/api/access",
            json={
                "username": username,
                "resource": test_group,
                "level": "contributor",
            },
            timeout=10,
        )

        _set_cookie(page, username)
        page.goto(f"{BASE_URL}/groups/{test_group}")
        page.wait_for_load_state("domcontentloaded")

        form = page.locator("#create-repo-form")
        expect(form).to_be_visible()

        name_input = page.locator("input#repo-name")
        expect(name_input).to_be_visible()

        # Cleanup
        requests.delete(
            f"{BASE_URL}/api/access",
            json={"username": username, "resource": test_group},
            timeout=10,
        )

    def test_create_form_hidden_for_readonly(
        self, page: Page, test_group: str,
    ) -> None:
        """Create repo form is hidden for readonly user."""
        username = _unique_name("usr")
        requests.post(
            f"{BASE_URL}/api/access",
            json={
                "username": username,
                "resource": test_group,
                "level": "readonly",
            },
            timeout=10,
        )

        _set_cookie(page, username)
        page.goto(f"{BASE_URL}/groups/{test_group}")
        page.wait_for_load_state("domcontentloaded")

        form = page.locator("#create-repo-form")
        expect(form).to_have_count(0)

        # Cleanup
        requests.delete(
            f"{BASE_URL}/api/access",
            json={"username": username, "resource": test_group},
            timeout=10,
        )

    def test_no_access_shows_forbidden(
        self, page: Page, test_group: str,
    ) -> None:
        """User without access gets 403."""
        username = _unique_name("noaccess")
        _set_cookie(page, username)
        page.goto(f"{BASE_URL}/groups/{test_group}")
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert "403" in content or "Forbidden" in content


@pytest.mark.e2e
class TestCreateRepo:
    """Test creating repositories from the group page."""

    def test_create_repo_via_form(
        self, page: Page, test_group: str,
    ) -> None:
        """Creating a repo via the form adds it to the table."""
        username = _unique_name("usr")
        repo_name = _unique_name("repo")

        requests.post(
            f"{BASE_URL}/api/access",
            json={
                "username": username,
                "resource": test_group,
                "level": "admin",
            },
            timeout=10,
        )

        _set_cookie(page, username)
        page.goto(f"{BASE_URL}/groups/{test_group}")
        page.wait_for_load_state("domcontentloaded")

        # Fill and submit
        page.fill("input#repo-name", repo_name)
        page.fill("input#repo-desc", "E2E test repo")
        page.click("#create-repo-form button[type='submit']")

        # Wait for reload
        page.wait_for_timeout(1500)
        page.goto(f"{BASE_URL}/groups/{test_group}")
        page.wait_for_load_state("domcontentloaded")

        # Verify repo appears
        content = page.content()
        assert repo_name in content
        assert "E2E test repo" in content

        # Cleanup
        requests.delete(
            f"{BASE_URL}/api/repos/{test_group}/{repo_name}",
            timeout=10,
        )
        requests.delete(
            f"{BASE_URL}/api/access",
            json={"username": username, "resource": test_group},
            timeout=10,
        )

    def test_create_repo_via_api(
        self, page: Page, test_group: str,
    ) -> None:
        """Repos created via API appear on the group page."""
        username = _unique_name("usr")
        repo_name = _unique_name("repo")

        requests.post(
            f"{BASE_URL}/api/access",
            json={
                "username": username,
                "resource": test_group,
                "level": "contributor",
            },
            timeout=10,
        )

        # Create via API
        resp = requests.post(
            f"{BASE_URL}/api/repos/{test_group}",
            json={"name": repo_name, "description": "API created"},
            timeout=10,
        )
        assert resp.status_code == 201

        # Check page
        _set_cookie(page, username)
        page.goto(f"{BASE_URL}/groups/{test_group}")
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert repo_name in content

        # Cleanup
        requests.delete(
            f"{BASE_URL}/api/repos/{test_group}/{repo_name}",
            timeout=10,
        )
        requests.delete(
            f"{BASE_URL}/api/access",
            json={"username": username, "resource": test_group},
            timeout=10,
        )

    def test_duplicate_repo_shows_error(
        self, page: Page, test_group: str,
    ) -> None:
        """Creating a duplicate repo shows error message."""
        username = _unique_name("usr")
        repo_name = _unique_name("repo")

        requests.post(
            f"{BASE_URL}/api/access",
            json={
                "username": username,
                "resource": test_group,
                "level": "admin",
            },
            timeout=10,
        )

        # Create first via API
        requests.post(
            f"{BASE_URL}/api/repos/{test_group}",
            json={"name": repo_name},
            timeout=10,
        )

        # Try creating via form
        _set_cookie(page, username)
        page.goto(f"{BASE_URL}/groups/{test_group}")
        page.wait_for_load_state("domcontentloaded")

        page.fill("input#repo-name", repo_name)
        page.click("#create-repo-form button[type='submit']")

        page.wait_for_timeout(1000)
        message = page.locator("#repo-message").inner_text()
        assert "already exists" in message.lower() or "error" in message.lower()

        # Cleanup
        requests.delete(
            f"{BASE_URL}/api/repos/{test_group}/{repo_name}",
            timeout=10,
        )
        requests.delete(
            f"{BASE_URL}/api/access",
            json={"username": username, "resource": test_group},
            timeout=10,
        )
