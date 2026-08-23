"""E2E tests: Admin link in navigation bar.

Verifies that the Admin link appears in the nav bar on all pages
for admin users, and that clicking it navigates to /admin.
"""

from __future__ import annotations

import os
import uuid

import pytest
import requests
from playwright.sync_api import Page, expect


BASE_URL = os.environ.get("GITGATEWAY_BASE_URL", "https://git.botthouse.net")


def _create_admin_cookie() -> str:
    """Create an admin session cookie."""
    from gitgateway.auth import (  # pylint: disable=import-outside-toplevel
        create_session_cookie, AuthenticatedUser,
    )
    user = AuthenticatedUser(username="nav-admin", is_admin=True)
    secret = os.environ.get("SESSION_SECRET", "gitgateway-session-secret")
    return create_session_cookie(user, secret, max_age=3600)


def _create_regular_cookie() -> str:
    """Create a non-admin session cookie."""
    from gitgateway.auth import (  # pylint: disable=import-outside-toplevel
        create_session_cookie, AuthenticatedUser,
    )
    user = AuthenticatedUser(username="nav-regular", is_admin=False)
    secret = os.environ.get("SESSION_SECRET", "gitgateway-session-secret")
    return create_session_cookie(user, secret, max_age=3600)


@pytest.fixture
def admin_page(page: Page) -> Page:
    """Page with admin session cookie."""
    page.context.add_cookies([{
        "name": "session",
        "value": _create_admin_cookie(),
        "domain": "git.botthouse.net",
        "path": "/",
        "secure": True,
        "httpOnly": True,
    }])
    return page


@pytest.fixture
def regular_page(page: Page) -> Page:
    """Page with non-admin session cookie."""
    page.context.add_cookies([{
        "name": "session",
        "value": _create_regular_cookie(),
        "domain": "git.botthouse.net",
        "path": "/",
        "secure": True,
        "httpOnly": True,
    }])
    return page


@pytest.mark.e2e
class TestAdminLinkVisibility:
    """Test that the Admin link appears for admins and not for regular users."""

    def test_admin_link_visible_on_homepage(
        self, admin_page: Page,
    ) -> None:
        """Admin link is visible on the homepage for admin users."""
        admin_page.goto(BASE_URL)
        admin_page.wait_for_load_state("domcontentloaded")

        admin_link = admin_page.locator("nav a:has-text('Admin')")
        expect(admin_link).to_be_visible()

    def test_admin_link_visible_on_admin_groups(
        self, admin_page: Page,
    ) -> None:
        """Admin link is visible on the admin groups page."""
        admin_page.goto(f"{BASE_URL}/admin/groups")
        admin_page.wait_for_load_state("domcontentloaded")

        admin_link = admin_page.locator("nav a:has-text('Admin')")
        expect(admin_link).to_be_visible()

    def test_admin_link_visible_on_admin_access(
        self, admin_page: Page,
    ) -> None:
        """Admin link is visible on the admin access page."""
        admin_page.goto(f"{BASE_URL}/admin/access")
        admin_page.wait_for_load_state("domcontentloaded")

        admin_link = admin_page.locator("nav a:has-text('Admin')")
        expect(admin_link).to_be_visible()

    def test_admin_link_hidden_for_regular_user(
        self, regular_page: Page,
    ) -> None:
        """Admin link is NOT visible for non-admin users."""
        regular_page.goto(BASE_URL)
        regular_page.wait_for_load_state("domcontentloaded")

        admin_link = regular_page.locator("nav a:has-text('Admin')")
        expect(admin_link).to_have_count(0)


@pytest.mark.e2e
class TestAdminLinkNavigation:
    """Test that clicking the Admin link navigates to /admin."""

    def test_admin_link_goes_to_admin_from_homepage(
        self, admin_page: Page,
    ) -> None:
        """Clicking Admin link from homepage navigates to /admin."""
        admin_page.goto(BASE_URL)
        admin_page.wait_for_load_state("domcontentloaded")

        admin_link = admin_page.locator("nav a:has-text('Admin')")
        admin_link.click()
        admin_page.wait_for_load_state("domcontentloaded")

        assert "/admin" in admin_page.url

    def test_admin_link_goes_to_admin_from_groups(
        self, admin_page: Page,
    ) -> None:
        """Clicking Admin link from groups page navigates to /admin."""
        admin_page.goto(f"{BASE_URL}/admin/groups")
        admin_page.wait_for_load_state("domcontentloaded")

        admin_link = admin_page.locator("nav a:has-text('Admin')")
        admin_link.click()
        admin_page.wait_for_load_state("domcontentloaded")

        assert "/admin" in admin_page.url

    def test_admin_link_goes_to_admin_from_access(
        self, admin_page: Page,
    ) -> None:
        """Clicking Admin link from access page navigates to /admin."""
        admin_page.goto(f"{BASE_URL}/admin/access")
        admin_page.wait_for_load_state("domcontentloaded")

        admin_link = admin_page.locator("nav a:has-text('Admin')")
        admin_link.click()
        admin_page.wait_for_load_state("domcontentloaded")

        assert "/admin" in admin_page.url

    def test_admin_page_has_manage_groups_link(
        self, admin_page: Page,
    ) -> None:
        """The /admin page has a link to Manage Groups."""
        admin_page.goto(f"{BASE_URL}/admin")
        admin_page.wait_for_load_state("domcontentloaded")

        content = admin_page.content()
        assert "Manage Groups" in content
        assert "/admin/groups" in content

    def test_admin_page_has_access_control_link(
        self, admin_page: Page,
    ) -> None:
        """The /admin page has a link to Access Control."""
        admin_page.goto(f"{BASE_URL}/admin")
        admin_page.wait_for_load_state("domcontentloaded")

        content = admin_page.content()
        assert "Access Control" in content
        assert "/admin/access" in content

    def test_admin_link_href_is_admin(
        self, admin_page: Page,
    ) -> None:
        """The Admin link's href points to /admin."""
        admin_page.goto(BASE_URL)
        admin_page.wait_for_load_state("domcontentloaded")

        admin_link = admin_page.locator("nav a:has-text('Admin')")
        href = admin_link.get_attribute("href")
        assert href == "/admin" or href == "/admin/"
