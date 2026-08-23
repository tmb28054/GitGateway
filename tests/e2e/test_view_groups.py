"""E2E tests: View Groups page.

Verifies that the /groups page shows only groups the authenticated
user has been granted access to.
"""

from __future__ import annotations

import os
import uuid

import pytest
import requests
from playwright.sync_api import Page, expect


BASE_URL = os.environ.get("GITGATEWAY_BASE_URL", "https://git.botthouse.net")


def _create_session_cookie(username: str, is_admin: bool = False) -> str:
    """Create a session cookie for a given user."""
    from gitgateway.auth import (  # pylint: disable=import-outside-toplevel
        create_session_cookie, AuthenticatedUser,
    )
    user = AuthenticatedUser(username=username, is_admin=is_admin)
    secret = os.environ.get("SESSION_SECRET", "gitgateway-session-secret")
    return create_session_cookie(user, secret, max_age=3600)


def _unique_name(prefix: str = "vg") -> str:
    """Generate unique name."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _set_cookie(page: Page, username: str, is_admin: bool = False) -> None:
    """Set a session cookie on the page context."""
    cookie_value = _create_session_cookie(username, is_admin)
    page.context.add_cookies([{
        "name": "session",
        "value": cookie_value,
        "domain": "git.botthouse.net",
        "path": "/",
        "secure": True,
        "httpOnly": True,
    }])


@pytest.mark.e2e
class TestViewGroupsPageRenders:
    """Test that the View Groups page renders correctly."""

    def test_groups_page_loads(self, page: Page) -> None:
        """The /groups page renders with heading."""
        _set_cookie(page, "viewer")
        page.goto(f"{BASE_URL}/groups")
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert "Your Groups" in content

    def test_groups_page_has_table(self, page: Page) -> None:
        """The /groups page has a groups table."""
        _set_cookie(page, "viewer")
        page.goto(f"{BASE_URL}/groups")
        page.wait_for_load_state("domcontentloaded")

        table = page.locator("table")
        expect(table).to_be_visible()

    def test_no_access_shows_empty_message(self, page: Page) -> None:
        """User with no access sees empty message."""
        username = _unique_name("nogroups")
        _set_cookie(page, username)
        page.goto(f"{BASE_URL}/groups")
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert "don't have access" in content.lower() or "no" in content.lower()

    def test_homepage_links_to_groups(self, page: Page) -> None:
        """Homepage has a link to /groups."""
        _set_cookie(page, "viewer")
        page.goto(BASE_URL)
        page.wait_for_load_state("domcontentloaded")

        link = page.locator("a:has-text('View Groups')")
        expect(link).to_be_visible()
        href = link.get_attribute("href")
        assert href == "/groups"


@pytest.mark.e2e
class TestViewGroupsShowsAccessibleGroups:
    """Test that users only see groups they have access to."""

    @pytest.fixture
    def setup_groups(self):
        """Create test groups and clean up after."""
        group_a = _unique_name("grpA")
        group_b = _unique_name("grpB")
        group_c = _unique_name("grpC")

        # Create all three groups
        for name in (group_a, group_b, group_c):
            requests.post(
                f"{BASE_URL}/api/groups",
                json={"name": name, "description": f"Test group {name}"},
                timeout=10,
            )

        yield {"a": group_a, "b": group_b, "c": group_c}

        # Cleanup
        for name in (group_a, group_b, group_c):
            requests.delete(f"{BASE_URL}/api/groups/{name}", timeout=10)

    def test_user_sees_only_granted_groups(
        self, page: Page, setup_groups: dict,
    ) -> None:
        """User sees only groups they have access to, not all groups."""
        username = _unique_name("usr")
        group_a = setup_groups["a"]
        group_b = setup_groups["b"]
        group_c = setup_groups["c"]

        # Grant access to group_a and group_b only
        requests.post(
            f"{BASE_URL}/api/access",
            json={
                "username": username,
                "resource": group_a,
                "level": "readonly",
            },
            timeout=10,
        )
        requests.post(
            f"{BASE_URL}/api/access",
            json={
                "username": username,
                "resource": group_b,
                "level": "contributor",
            },
            timeout=10,
        )

        # View groups page as that user
        _set_cookie(page, username)
        page.goto(f"{BASE_URL}/groups")
        page.wait_for_load_state("domcontentloaded")

        content = page.content()

        # Should see group_a and group_b
        assert group_a in content
        assert group_b in content

        # Should NOT see group_c (no access granted)
        assert group_c not in content

        # Cleanup access entries
        requests.delete(
            f"{BASE_URL}/api/access",
            json={"username": username, "resource": group_a},
            timeout=10,
        )
        requests.delete(
            f"{BASE_URL}/api/access",
            json={"username": username, "resource": group_b},
            timeout=10,
        )

    def test_user_sees_group_description(
        self, page: Page, setup_groups: dict,
    ) -> None:
        """Group descriptions are shown on the page."""
        username = _unique_name("usr")
        group_a = setup_groups["a"]

        # Grant access
        requests.post(
            f"{BASE_URL}/api/access",
            json={
                "username": username,
                "resource": group_a,
                "level": "admin",
            },
            timeout=10,
        )

        _set_cookie(page, username)
        page.goto(f"{BASE_URL}/groups")
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert group_a in content
        assert f"Test group {group_a}" in content

        # Cleanup
        requests.delete(
            f"{BASE_URL}/api/access",
            json={"username": username, "resource": group_a},
            timeout=10,
        )

    def test_different_users_see_different_groups(
        self, page: Page, setup_groups: dict,
    ) -> None:
        """Two users with different access see different groups."""
        user1 = _unique_name("u1")
        user2 = _unique_name("u2")
        group_a = setup_groups["a"]
        group_b = setup_groups["b"]

        # user1 gets group_a, user2 gets group_b
        requests.post(
            f"{BASE_URL}/api/access",
            json={"username": user1, "resource": group_a, "level": "readonly"},
            timeout=10,
        )
        requests.post(
            f"{BASE_URL}/api/access",
            json={"username": user2, "resource": group_b, "level": "readonly"},
            timeout=10,
        )

        # Check user1
        _set_cookie(page, user1)
        page.goto(f"{BASE_URL}/groups")
        page.wait_for_load_state("domcontentloaded")
        content1 = page.content()
        assert group_a in content1
        assert group_b not in content1

        # Check user2 (new context needed for different cookie)
        page.context.clear_cookies()
        _set_cookie(page, user2)
        page.goto(f"{BASE_URL}/groups")
        page.wait_for_load_state("domcontentloaded")
        content2 = page.content()
        assert group_b in content2
        assert group_a not in content2

        # Cleanup
        requests.delete(
            f"{BASE_URL}/api/access",
            json={"username": user1, "resource": group_a},
            timeout=10,
        )
        requests.delete(
            f"{BASE_URL}/api/access",
            json={"username": user2, "resource": group_b},
            timeout=10,
        )
