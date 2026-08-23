"""Tests for gitgateway.web.routes module."""

from __future__ import annotations

import pytest

from gitgateway.web.routes import (
    render_dashboard,
    render_error_page,
    render_login_page,
    render_repo_page,
    render_template,
    reset_env,
)


@pytest.fixture(autouse=True)
def _reset():
    """Reset Jinja2 environment between tests."""
    reset_env()
    yield
    reset_env()


class TestRenderTemplate:
    """Tests for render_template function."""

    def test_renders_base_template(self) -> None:
        """Renders the base template."""
        html = render_template("base.html", username="alice", notifications_count=3)
        assert "GitGateway" in html
        assert "alice" in html
        assert "3" in html

    def test_renders_without_user(self) -> None:
        """Renders base without authenticated user."""
        html = render_template("base.html")
        assert "Sign In" in html


class TestRenderLoginPage:
    """Tests for render_login_page function."""

    def test_basic_login(self) -> None:
        """Renders login page with IdP URL."""
        html = render_login_page(idp_url="https://idp.example.com/gitgateway")
        assert "Sign in with SSO" in html
        assert "https://idp.example.com/gitgateway" in html

    def test_login_with_error(self) -> None:
        """Renders login page with error message."""
        html = render_login_page(error="Invalid credentials")
        assert "Invalid credentials" in html

    def test_login_no_error(self) -> None:
        """Login page without error doesn't show error div."""
        html = render_login_page()
        assert "Invalid" not in html


class TestRenderDashboard:
    """Tests for render_dashboard function."""

    def test_with_repos(self) -> None:
        """Renders dashboard with groups and repos."""
        groups = [
            {
                "name": "team",
                "repos": [
                    {"name": "app", "description": "Main application"},
                    {"name": "lib", "description": ""},
                ],
            },
        ]
        html = render_dashboard("alice", groups, notifications_count=2)
        assert "team" in html
        assert "app" in html
        assert "Main application" in html
        assert "alice" in html

    def test_empty_dashboard(self) -> None:
        """Renders empty state when no repos."""
        html = render_dashboard("bob", [])
        assert "No repositories" in html

    def test_notification_badge(self) -> None:
        """Shows notification count when > 0."""
        html = render_dashboard("alice", [], notifications_count=5)
        assert "5" in html


class TestRenderRepoPage:
    """Tests for render_repo_page function."""

    def test_with_files(self) -> None:
        """Renders repo page with file listing."""
        repo = {"group": "team", "name": "app", "description": "My app"}
        files = [
            {"name": "README.md", "type": "file", "path": "README.md"},
            {"name": "src", "type": "directory", "path": "src"},
        ]
        html = render_repo_page(
            repo, files, branches=["main", "develop"],
            current_branch="main",
        )
        assert "team/app" in html
        assert "My app" in html
        assert "README.md" in html
        assert "src" in html
        assert "main" in html
        assert "develop" in html

    def test_branch_selection(self) -> None:
        """Current branch is selected in dropdown."""
        repo = {"group": "g", "name": "r", "description": ""}
        html = render_repo_page(
            repo, [], branches=["main", "feat"],
            current_branch="feat",
        )
        assert 'selected' in html


class TestRenderErrorPage:
    """Tests for render_error_page function."""

    def test_404_error(self) -> None:
        """Renders 404 error page."""
        html = render_error_page(404, "Page not found")
        assert "404" in html
        assert "Page not found" in html

    def test_500_error(self) -> None:
        """Renders 500 error page."""
        html = render_error_page(500, "Internal server error")
        assert "500" in html
        assert "Internal server error" in html


class TestEnvCaching:
    """Tests for Jinja2 environment caching."""

    def test_env_is_reused(self) -> None:
        """Environment is cached across calls."""
        from gitgateway.web.routes import _get_env  # pylint: disable=import-outside-toplevel

        env1 = _get_env()
        env2 = _get_env()
        assert env1 is env2
