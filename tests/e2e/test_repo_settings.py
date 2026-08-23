"""E2E tests: Repository settings page.

Tests the settings page accessible via the gear icon, including
default branch override and repository deletion.
"""

from __future__ import annotations

import os
import uuid

import pytest
import requests
from playwright.sync_api import Page, expect


BASE_URL = os.environ.get("GITGATEWAY_BASE_URL", "https://git.botthouse.net")


def _create_session_cookie(username: str, is_admin: bool = False) -> str:
    from gitgateway.auth import (  # pylint: disable=import-outside-toplevel
        create_session_cookie, AuthenticatedUser,
    )
    user = AuthenticatedUser(username=username, is_admin=is_admin)
    secret = os.environ.get("SESSION_SECRET", "gitgateway-session-secret")
    return create_session_cookie(user, secret, max_age=3600)


def _set_cookie(page: Page, username: str, is_admin: bool = False) -> None:
    page.context.add_cookies([{
        "name": "session",
        "value": _create_session_cookie(username, is_admin),
        "domain": "git.botthouse.net",
        "path": "/",
        "secure": True,
        "httpOnly": True,
    }])


def _unique(prefix: str = "st") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def test_repo():
    """Create a group and repo for settings tests."""
    group = _unique("grp")
    repo = _unique("repo")
    username = _unique("admin")

    requests.post(
        f"{BASE_URL}/api/groups", json={"name": group}, timeout=10,
    )
    requests.post(
        f"{BASE_URL}/api/repos/{group}",
        json={"name": repo, "description": "Settings test repo"},
        timeout=10,
    )
    requests.post(
        f"{BASE_URL}/api/access",
        json={"username": username, "resource": group, "level": "admin"},
        timeout=10,
    )

    yield {"group": group, "repo": repo, "username": username}

    # Cleanup
    requests.delete(
        f"{BASE_URL}/api/access",
        json={"username": username, "resource": group},
        timeout=10,
    )
    requests.delete(f"{BASE_URL}/api/repos/{group}/{repo}", timeout=10)
    requests.delete(f"{BASE_URL}/api/groups/{group}", timeout=10)


@pytest.mark.e2e
class TestSettingsPageAccess:
    """Test access control for the settings page."""

    def test_admin_can_access_settings(
        self, page: Page, test_repo: dict,
    ) -> None:
        """Admin user can access the settings page."""
        data = test_repo
        _set_cookie(page, data["username"])
        page.goto(
            f"{BASE_URL}/groups/{data['group']}/{data['repo']}/settings",
        )
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert "Settings" in content
        assert "Default Branch" in content

    def test_non_admin_gets_forbidden(
        self, page: Page, test_repo: dict,
    ) -> None:
        """Non-admin user gets 403 on settings page."""
        data = test_repo
        readonly_user = _unique("ro")
        requests.post(
            f"{BASE_URL}/api/access",
            json={
                "username": readonly_user,
                "resource": data["group"],
                "level": "readonly",
            },
            timeout=10,
        )

        _set_cookie(page, readonly_user)
        page.goto(
            f"{BASE_URL}/groups/{data['group']}/{data['repo']}/settings",
        )
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert "403" in content or "Forbidden" in content

        requests.delete(
            f"{BASE_URL}/api/access",
            json={"username": readonly_user, "resource": data["group"]},
            timeout=10,
        )

    def test_gear_icon_links_to_settings(
        self, page: Page, test_repo: dict,
    ) -> None:
        """Gear icon on repo page links to settings."""
        data = test_repo
        _set_cookie(page, data["username"])
        page.goto(f"{BASE_URL}/groups/{data['group']}/{data['repo']}")
        page.wait_for_load_state("domcontentloaded")

        gear = page.locator("a[title='Settings']")
        expect(gear).to_be_visible()
        gear.click()
        page.wait_for_load_state("domcontentloaded")

        assert "/settings" in page.url
        assert "Default Branch" in page.content()


@pytest.mark.e2e
class TestSettingsForm:
    """Test the settings form functionality."""

    def test_shows_current_default_branch(
        self, page: Page, test_repo: dict,
    ) -> None:
        """Settings page shows current default branch value."""
        data = test_repo
        _set_cookie(page, data["username"])
        page.goto(
            f"{BASE_URL}/groups/{data['group']}/{data['repo']}/settings",
        )
        page.wait_for_load_state("domcontentloaded")

        branch_input = page.locator("input#default-branch")
        expect(branch_input).to_be_visible()
        assert branch_input.input_value() == "main"

    def test_change_default_branch(
        self, page: Page, test_repo: dict,
    ) -> None:
        """Changing default branch saves successfully."""
        data = test_repo
        _set_cookie(page, data["username"])
        page.goto(
            f"{BASE_URL}/groups/{data['group']}/{data['repo']}/settings",
        )
        page.wait_for_load_state("domcontentloaded")

        # Clear and type new branch
        page.fill("input#default-branch", "develop")
        page.click("#settings-form button[type='submit']")
        page.wait_for_timeout(1000)

        # Check success message
        message = page.locator("#settings-message").inner_text()
        assert "saved" in message.lower()

        # Reload and verify it persisted
        page.goto(
            f"{BASE_URL}/groups/{data['group']}/{data['repo']}/settings",
        )
        page.wait_for_load_state("domcontentloaded")
        branch_input = page.locator("input#default-branch")
        assert branch_input.input_value() == "develop"

        # Reset to main for other tests
        page.fill("input#default-branch", "main")
        page.click("#settings-form button[type='submit']")
        page.wait_for_timeout(500)

    def test_change_description(
        self, page: Page, test_repo: dict,
    ) -> None:
        """Changing description saves successfully."""
        data = test_repo
        _set_cookie(page, data["username"])
        page.goto(
            f"{BASE_URL}/groups/{data['group']}/{data['repo']}/settings",
        )
        page.wait_for_load_state("domcontentloaded")

        page.fill("input#repo-description", "Updated description")
        page.click("#settings-form button[type='submit']")
        page.wait_for_timeout(1000)

        message = page.locator("#settings-message").inner_text()
        assert "saved" in message.lower()

    def test_has_danger_zone(
        self, page: Page, test_repo: dict,
    ) -> None:
        """Settings page has a danger zone with delete button."""
        data = test_repo
        _set_cookie(page, data["username"])
        page.goto(
            f"{BASE_URL}/groups/{data['group']}/{data['repo']}/settings",
        )
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert "Danger Zone" in content
        assert "Delete Repository" in content


@pytest.mark.e2e
class TestSettingsApi:
    """Test the settings API endpoint directly."""

    def test_patch_default_branch(self, test_repo: dict) -> None:
        """PATCH /api/repos/<group>/<repo>/settings updates branch."""
        data = test_repo
        resp = requests.patch(
            f"{BASE_URL}/api/repos/{data['group']}/{data['repo']}/settings",
            json={"default_branch": "release"},
            timeout=10,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["default_branch"] == "release"

        # Verify via GET
        resp = requests.get(
            f"{BASE_URL}/api/repos/{data['group']}",
            timeout=10,
        )
        repos = resp.json()
        target = next(r for r in repos if r["name"] == data["repo"])
        assert target["default_branch"] == "release"

    def test_patch_nonexistent_repo(self) -> None:
        """PATCH on non-existent repo returns 404."""
        resp = requests.patch(
            f"{BASE_URL}/api/repos/ghost/nope/settings",
            json={"default_branch": "x"},
            timeout=10,
        )
        assert resp.status_code == 404
