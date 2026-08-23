"""E2E tests: Admin group management UI.

Tests the admin groups page for creating, viewing, and deleting
groups via the web interface. Uses a session cookie for auth
and the API for setup/verification.
"""

from __future__ import annotations

import os
import uuid

import pytest
import requests
from playwright.sync_api import Page, expect


BASE_URL = os.environ.get("GITGATEWAY_BASE_URL", "https://git.botthouse.net")


def _create_admin_session_cookie() -> str:
    """Create a valid admin session cookie.

    Calls the auth callback endpoint with a minimal token to get
    a session cookie with admin privileges.

    Returns:
        The session cookie value.
    """
    from gitgateway.auth import (  # pylint: disable=import-outside-toplevel
        create_session_cookie, AuthenticatedUser,
    )
    user = AuthenticatedUser(username="e2e-admin", is_admin=True)
    secret = os.environ.get("SESSION_SECRET", "gitgateway-session-secret")
    return create_session_cookie(user, secret, max_age=3600)


@pytest.fixture
def admin_page(page: Page) -> Page:
    """Create a page with admin session cookie set.

    Args:
        page: Playwright page.

    Returns:
        Page with authentication cookie.
    """
    cookie_value = _create_admin_session_cookie()
    # Set cookie on the domain
    page.context.add_cookies([{
        "name": "session",
        "value": cookie_value,
        "domain": "git.botthouse.net",
        "path": "/",
        "secure": True,
        "httpOnly": True,
    }])
    return page


def _unique_name() -> str:
    """Generate a unique group name."""
    return f"ui-test-{uuid.uuid4().hex[:8]}"


@pytest.mark.e2e
class TestAdminGroupsPageLoad:
    """Test that the admin groups page renders correctly."""

    def test_admin_groups_page_loads(self, admin_page: Page) -> None:
        """Admin groups page renders with heading and create form."""
        admin_page.goto(f"{BASE_URL}/admin/groups")
        admin_page.wait_for_load_state("domcontentloaded")

        content = admin_page.content()
        assert "Manage Groups" in content
        assert "Create New Group" in content

    def test_admin_groups_has_create_form(self, admin_page: Page) -> None:
        """Admin page has a create group form with name input."""
        admin_page.goto(f"{BASE_URL}/admin/groups")
        admin_page.wait_for_load_state("domcontentloaded")

        name_input = admin_page.locator("input#group-name")
        expect(name_input).to_be_visible()

        desc_input = admin_page.locator("input#group-desc")
        expect(desc_input).to_be_visible()

        submit_btn = admin_page.locator(
            "#create-group-form button[type='submit']",
        )
        expect(submit_btn).to_be_visible()

    def test_admin_groups_has_table(self, admin_page: Page) -> None:
        """Admin page has a groups table."""
        admin_page.goto(f"{BASE_URL}/admin/groups")
        admin_page.wait_for_load_state("domcontentloaded")

        table = admin_page.locator("table")
        expect(table).to_be_visible()

    def test_non_admin_gets_forbidden(self, page: Page) -> None:
        """Non-admin user sees 403 on admin pages."""
        from gitgateway.auth import (  # pylint: disable=import-outside-toplevel
            create_session_cookie, AuthenticatedUser,
        )
        user = AuthenticatedUser(username="regular-user", is_admin=False)
        secret = os.environ.get(
            "SESSION_SECRET", "gitgateway-session-secret",
        )
        cookie_value = create_session_cookie(user, secret, max_age=3600)
        page.context.add_cookies([{
            "name": "session",
            "value": cookie_value,
            "domain": "git.botthouse.net",
            "path": "/",
            "secure": True,
            "httpOnly": True,
        }])

        page.goto(f"{BASE_URL}/admin/groups")
        page.wait_for_load_state("domcontentloaded")

        assert "403" in page.content() or "Forbidden" in page.content()


@pytest.mark.e2e
class TestAdminGroupsCrud:
    """Test creating and deleting groups via the admin UI."""

    def test_create_group_via_form(self, admin_page: Page) -> None:
        """Creating a group via the form adds it to the table."""
        name = _unique_name()

        admin_page.goto(f"{BASE_URL}/admin/groups")
        admin_page.wait_for_load_state("domcontentloaded")

        # Fill and submit the create form
        admin_page.fill("input#group-name", name)
        admin_page.fill("input#group-desc", "Created by e2e test")
        admin_page.click("#create-group-form button[type='submit']")

        # Wait for reload after creation
        admin_page.wait_for_load_state("domcontentloaded")
        admin_page.wait_for_timeout(1000)  # Allow JS reload

        # Refresh to see updated table
        admin_page.goto(f"{BASE_URL}/admin/groups")
        admin_page.wait_for_load_state("domcontentloaded")

        # Verify group appears in the table
        content = admin_page.content()
        assert name in content

        # Cleanup via API
        requests.delete(f"{BASE_URL}/api/groups/{name}", timeout=10)

    def test_created_group_shows_in_list(self, admin_page: Page) -> None:
        """Groups created via API appear in the admin page table."""
        name = _unique_name()

        # Create via API
        requests.post(
            f"{BASE_URL}/api/groups",
            json={"name": name, "description": "API created"},
            timeout=10,
        )

        # Check admin page
        admin_page.goto(f"{BASE_URL}/admin/groups")
        admin_page.wait_for_load_state("domcontentloaded")

        content = admin_page.content()
        assert name in content
        assert "API created" in content

        # Cleanup
        requests.delete(f"{BASE_URL}/api/groups/{name}", timeout=10)

    def test_delete_group_via_button(self, admin_page: Page) -> None:
        """Clicking delete button removes the group."""
        name = _unique_name()

        # Create via API
        requests.post(
            f"{BASE_URL}/api/groups",
            json={"name": name},
            timeout=10,
        )

        # Load admin page
        admin_page.goto(f"{BASE_URL}/admin/groups")
        admin_page.wait_for_load_state("domcontentloaded")
        assert name in admin_page.content()

        # Set up dialog handler BEFORE triggering it
        admin_page.once("dialog", lambda dialog: dialog.accept())

        # Click the delete button for this group using xpath
        # Find the row containing our group name and click its delete button
        row = admin_page.locator(f"tr:has-text('{name}')")
        row.locator("button:has-text('Delete')").click()

        # Wait for navigation/reload
        admin_page.wait_for_timeout(2000)

        # Refresh and verify gone
        admin_page.goto(f"{BASE_URL}/admin/groups")
        admin_page.wait_for_load_state("domcontentloaded")

        content = admin_page.content()
        assert name not in content

    def test_duplicate_group_shows_error(self, admin_page: Page) -> None:
        """Creating a duplicate group shows an error message."""
        name = _unique_name()

        # Create via API first
        requests.post(
            f"{BASE_URL}/api/groups",
            json={"name": name},
            timeout=10,
        )

        # Try creating via form
        admin_page.goto(f"{BASE_URL}/admin/groups")
        admin_page.wait_for_load_state("domcontentloaded")

        admin_page.fill("input#group-name", name)
        admin_page.click("#create-group-form button[type='submit']")

        # Wait for error message
        admin_page.wait_for_timeout(1000)

        # Should show error
        message_div = admin_page.locator("#create-message")
        message_text = message_div.inner_text()
        assert "already exists" in message_text.lower() or "error" in message_text.lower()

        # Cleanup
        requests.delete(f"{BASE_URL}/api/groups/{name}", timeout=10)
