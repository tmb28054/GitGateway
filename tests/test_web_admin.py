"""Tests for gitgateway.web.admin_views module."""

from __future__ import annotations

import pytest

from gitgateway.web.admin_views import (
    render_admin_access,
    render_admin_audit,
    render_admin_groups,
    render_repo_settings,
)
from gitgateway.web.routes import reset_env


@pytest.fixture(autouse=True)
def _reset():
    """Reset Jinja2 environment."""
    reset_env()
    yield
    reset_env()


class TestRenderAdminGroups:
    """Tests for render_admin_groups function."""

    def test_with_groups(self) -> None:
        """Renders groups table."""
        groups = [
            {"name": "team", "description": "Dev team", "member_count": 5},
            {"name": "ops", "description": "Operations", "member_count": 3},
        ]
        html = render_admin_groups(groups)
        assert "team" in html
        assert "Dev team" in html
        assert "5" in html
        assert "Create Group" in html

    def test_empty_groups(self) -> None:
        """Renders empty state."""
        html = render_admin_groups([])
        assert "No groups" in html

    def test_non_admin(self) -> None:
        """Hides create button for non-admin."""
        html = render_admin_groups([], is_admin=False)
        assert "Create Group" not in html


class TestRenderAdminAccess:
    """Tests for render_admin_access function."""

    def test_with_users(self) -> None:
        """Renders access table."""
        users = [
            {"username": "alice", "resource": "team", "level": "admin"},
            {"username": "bob", "resource": "team/app", "level": "contributor"},
        ]
        html = render_admin_access(users, resource="team")
        assert "alice" in html
        assert "admin" in html
        assert "team" in html

    def test_empty_users(self) -> None:
        """Renders empty state."""
        html = render_admin_access([])
        assert "No access entries" in html


class TestRenderAdminAudit:
    """Tests for render_admin_audit function."""

    def test_with_events(self) -> None:
        """Renders audit log table."""
        events = [
            {
                "timestamp": "2025-01-01T00:00:00Z",
                "actor": "alice",
                "action": "repo.created",
                "resource": "team/app",
            },
        ]
        html = render_admin_audit(events, resource="team/app")
        assert "alice" in html
        assert "repo.created" in html
        assert "team/app" in html

    def test_empty_events(self) -> None:
        """Renders empty state."""
        html = render_admin_audit([])
        assert "No audit events" in html

    def test_no_resource_filter(self) -> None:
        """Renders without resource filter."""
        html = render_admin_audit([])
        assert "Audit Log" in html


class TestRenderRepoSettings:
    """Tests for render_repo_settings function."""

    def test_with_all_settings(self) -> None:
        """Renders settings with mirror and protections."""
        repo = {
            "group": "team", "name": "app",
            "description": "Main app",
            "ai_enabled": True, "sast_enabled": True,
        }
        mirror = {
            "target_url": "https://github.com/u/r.git",
            "last_sync_status": "success",
            "last_sync_time": "2025-01-01T00:00:00Z",
        }
        protections = [
            {
                "branch": "main",
                "required_approvals": 2,
                "force_push_allowed": False,
                "require_linear_history": True,
            },
        ]
        html = render_repo_settings(repo, mirror, protections)
        assert "Main app" in html
        assert "checked" in html  # ai_enabled
        assert "github.com" in html
        assert "success" in html
        assert "2 approval" in html
        assert "linear history" in html

    def test_without_mirror(self) -> None:
        """Renders without mirror section."""
        repo = {
            "group": "t", "name": "r",
            "description": "", "ai_enabled": False,
            "sast_enabled": False,
        }
        html = render_repo_settings(repo)
        assert "Mirror" not in html

    def test_empty_protections(self) -> None:
        """Renders with no branch protections."""
        repo = {
            "group": "t", "name": "r",
            "description": "", "ai_enabled": False,
            "sast_enabled": False,
        }
        html = render_repo_settings(repo, branch_protections=[])
        assert "No branch protections" in html
