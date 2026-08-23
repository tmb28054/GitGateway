"""Tests for gitgateway.web.pr_views module."""

from __future__ import annotations

import pytest

from gitgateway.web.pr_views import render_pr_detail, render_pr_list
from gitgateway.web.routes import reset_env


@pytest.fixture(autouse=True)
def _reset():
    """Reset Jinja2 environment."""
    reset_env()
    yield
    reset_env()


class TestRenderPrList:
    """Tests for render_pr_list function."""

    def test_renders_with_prs(self) -> None:
        """Renders PR list with items."""
        repo = {"group": "team", "name": "app"}
        prs = [
            {
                "number": 1, "title": "Fix bug",
                "status": "open", "author": "alice",
                "source_branch": "fix/bug", "target_branch": "main",
            },
            {
                "number": 2, "title": "Add feature",
                "status": "merged", "author": "bob",
                "source_branch": "feat/new", "target_branch": "main",
            },
        ]
        html = render_pr_list(
            repo, prs, current_status="open",
            counts={"open": 1, "merged": 1, "closed": 0},
        )
        assert "Fix bug" in html
        assert "Add feature" in html
        assert "#1" in html
        assert "alice" in html

    def test_empty_list(self) -> None:
        """Renders empty state."""
        repo = {"group": "t", "name": "r"}
        html = render_pr_list(repo, [])
        assert "No pull requests" in html

    def test_default_counts(self) -> None:
        """Uses default counts when not provided."""
        repo = {"group": "t", "name": "r"}
        html = render_pr_list(repo, [])
        assert "0" in html


class TestRenderPrDetail:
    """Tests for render_pr_detail function."""

    def test_renders_open_pr(self) -> None:
        """Renders an open PR with merge button."""
        repo = {"group": "team", "name": "app"}
        pr = {
            "number": 1, "title": "Fix bug",
            "status": "open", "author": "alice",
            "source_branch": "fix/bug", "target_branch": "main",
            "description": "Fixes the login bug",
        }
        html = render_pr_detail(
            repo, pr,
            status_checks=[
                {"name": "ci/tests", "status": "success", "description": "Passed"},
            ],
            reviews=[
                {"author": "bob", "status": "approved"},
            ],
            can_merge=True,
            checks_passing=True,
        )
        assert "Fix bug" in html
        assert "#1" in html
        assert "Fixes the login bug" in html
        assert "Merge Pull Request" in html
        assert "ci/tests" in html
        assert "bob" in html

    def test_merge_disabled_when_checks_failing(self) -> None:
        """Merge button disabled when checks not passing."""
        repo = {"group": "t", "name": "r"}
        pr = {
            "number": 2, "title": "WIP",
            "status": "open", "author": "x",
            "source_branch": "a", "target_branch": "b",
            "description": "",
        }
        html = render_pr_detail(
            repo, pr, can_merge=True, checks_passing=False,
        )
        assert "disabled" in html
        assert "Checks pending" in html

    def test_no_merge_button_for_merged(self) -> None:
        """No merge button for already-merged PR."""
        repo = {"group": "t", "name": "r"}
        pr = {
            "number": 3, "title": "Done",
            "status": "merged", "author": "x",
            "source_branch": "a", "target_branch": "b",
            "description": "",
        }
        html = render_pr_detail(repo, pr, can_merge=False)
        assert "Merge Pull Request" not in html

    def test_no_description(self) -> None:
        """Renders without description block when empty."""
        repo = {"group": "t", "name": "r"}
        pr = {
            "number": 4, "title": "T",
            "status": "open", "author": "x",
            "source_branch": "a", "target_branch": "b",
            "description": "",
        }
        html = render_pr_detail(repo, pr)
        # The description div should not appear
        assert "f6f8fa" not in html
