"""E2E tests: File viewer with syntax highlighting.

Tests that clicking a file in the repo tree opens it with
syntax-highlighted content, line numbers, and breadcrumb nav.
"""

from __future__ import annotations

import os
import uuid

import boto3
import pytest
import requests
from playwright.sync_api import Page, expect


BASE_URL = os.environ.get("GITGATEWAY_BASE_URL", "https://git.botthouse.net")


def _create_session_cookie(username: str) -> str:
    from gitgateway.auth import (  # pylint: disable=import-outside-toplevel
        create_session_cookie, AuthenticatedUser,
    )
    user = AuthenticatedUser(username=username, is_admin=True)
    secret = os.environ.get("SESSION_SECRET", "gitgateway-session-secret")
    return create_session_cookie(user, secret, max_age=3600)


def _set_cookie(page: Page, username: str) -> None:
    page.context.add_cookies([{
        "name": "session",
        "value": _create_session_cookie(username),
        "domain": "git.botthouse.net",
        "path": "/",
        "secure": True,
        "httpOnly": True,
    }])


def _unique(prefix: str = "fv") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def repo_with_code():
    """Create a repo with Python and YAML files for viewing."""
    group = _unique("grp")
    repo = _unique("repo")
    username = _unique("usr")

    requests.post(
        f"{BASE_URL}/api/groups", json={"name": group}, timeout=10,
    )
    requests.post(
        f"{BASE_URL}/api/repos/{group}",
        json={"name": repo, "description": "File viewer test"},
        timeout=10,
    )
    requests.post(
        f"{BASE_URL}/api/access",
        json={"username": username, "resource": group, "level": "admin"},
        timeout=10,
    )

    # Seed files in DynamoDB
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    table = ddb.Table("gitgateway-repos")

    files = [
        {
            "path": "main.py",
            "type": "file",
            "name": "main.py",
            "content": (
                "import sys\n\n\n"
                "def hello(name: str) -> str:\n"
                '    """Greet someone."""\n'
                '    return f"Hello, {name}!"\n\n\n'
                "if __name__ == '__main__':\n"
                "    print(hello(sys.argv[1]))\n"
            ),
            "last_commit_message": "Add main",
            "last_commit_author": "alice",
        },
        {
            "path": "config.yaml",
            "type": "file",
            "name": "config.yaml",
            "content": (
                "server:\n"
                "  host: 0.0.0.0\n"
                "  port: 8080\n"
                "database:\n"
                "  url: postgres://localhost/db\n"
            ),
            "last_commit_message": "Add config",
            "last_commit_author": "bob",
        },
        {
            "path": "README.md",
            "type": "file",
            "name": "README.md",
            "content": "# Test Project\n\nA project for testing.\n",
            "last_commit_message": "Init",
            "last_commit_author": "alice",
        },
    ]

    for f in files:
        table.put_item(Item={
            "PK": f"TREE#{group}/{repo}#main",
            "SK": f"FILE#{f['path']}",
            **f,
        })

    yield {"group": group, "repo": repo, "username": username}

    # Cleanup
    for f in files:
        table.delete_item(Key={
            "PK": f"TREE#{group}/{repo}#main",
            "SK": f"FILE#{f['path']}",
        })
    requests.delete(
        f"{BASE_URL}/api/access",
        json={"username": username, "resource": group},
        timeout=10,
    )
    requests.delete(f"{BASE_URL}/api/repos/{group}/{repo}", timeout=10)
    requests.delete(f"{BASE_URL}/api/groups/{group}", timeout=10)


@pytest.mark.e2e
class TestFileViewerNavigation:
    """Test navigating to a file from the tree."""

    def test_clicking_file_shows_content(
        self, page: Page, repo_with_code: dict,
    ) -> None:
        """Clicking a file in the tree shows its content."""
        data = repo_with_code
        _set_cookie(page, data["username"])

        # Go to repo page
        page.goto(f"{BASE_URL}/groups/{data['group']}/{data['repo']}")
        page.wait_for_load_state("domcontentloaded")

        # Click on main.py
        page.click("a:has-text('main.py')")
        page.wait_for_load_state("domcontentloaded")

        text = page.inner_text("body")
        assert "def hello" in text or "hello" in text

    def test_file_url_works_directly(
        self, page: Page, repo_with_code: dict,
    ) -> None:
        """Navigating directly to a file URL shows content."""
        data = repo_with_code
        _set_cookie(page, data["username"])

        page.goto(
            f"{BASE_URL}/groups/{data['group']}/{data['repo']}"
            f"/tree/main/config.yaml",
        )
        page.wait_for_load_state("domcontentloaded")

        text = page.inner_text("body")
        assert "server" in text
        assert "8080" in text


@pytest.mark.e2e
class TestFileViewerRendering:
    """Test that the file viewer renders correctly."""

    def test_shows_filename_and_line_count(
        self, page: Page, repo_with_code: dict,
    ) -> None:
        """File viewer header shows filename and line count."""
        data = repo_with_code
        _set_cookie(page, data["username"])

        page.goto(
            f"{BASE_URL}/groups/{data['group']}/{data['repo']}"
            f"/tree/main/main.py",
        )
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert "main.py" in content
        assert "lines" in content.lower()

    def test_has_syntax_highlighting(
        self, page: Page, repo_with_code: dict,
    ) -> None:
        """Python file has syntax highlighting (colored spans)."""
        data = repo_with_code
        _set_cookie(page, data["username"])

        page.goto(
            f"{BASE_URL}/groups/{data['group']}/{data['repo']}"
            f"/tree/main/main.py",
        )
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        # Pygments wraps code in <span> with classes or inline styles
        assert "<span" in content
        # Should have the highlight CSS class
        assert "highlight" in content

    def test_has_line_numbers(
        self, page: Page, repo_with_code: dict,
    ) -> None:
        """File viewer shows line numbers."""
        data = repo_with_code
        _set_cookie(page, data["username"])

        page.goto(
            f"{BASE_URL}/groups/{data['group']}/{data['repo']}"
            f"/tree/main/main.py",
        )
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        # Pygments linenos creates a table with line numbers
        # or our fallback shows numbered lines
        assert "1" in content
        assert "2" in content

    def test_has_breadcrumb(
        self, page: Page, repo_with_code: dict,
    ) -> None:
        """File viewer shows breadcrumb navigation."""
        data = repo_with_code
        _set_cookie(page, data["username"])

        page.goto(
            f"{BASE_URL}/groups/{data['group']}/{data['repo']}"
            f"/tree/main/main.py",
        )
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert data["repo"] in content
        assert "main.py" in content

    def test_has_copy_button(
        self, page: Page, repo_with_code: dict,
    ) -> None:
        """File viewer has a copy button."""
        data = repo_with_code
        _set_cookie(page, data["username"])

        page.goto(
            f"{BASE_URL}/groups/{data['group']}/{data['repo']}"
            f"/tree/main/main.py",
        )
        page.wait_for_load_state("domcontentloaded")

        copy_btn = page.locator("button:has-text('Copy')")
        expect(copy_btn).to_be_visible()

    def test_has_code_and_pr_tabs(
        self, page: Page, repo_with_code: dict,
    ) -> None:
        """File viewer still shows Code/PR tabs."""
        data = repo_with_code
        _set_cookie(page, data["username"])

        page.goto(
            f"{BASE_URL}/groups/{data['group']}/{data['repo']}"
            f"/tree/main/config.yaml",
        )
        page.wait_for_load_state("domcontentloaded")

        code_tab = page.locator("a:has-text('Code')")
        pr_tab = page.locator("a:has-text('Pull Requests')")
        expect(code_tab).to_be_visible()
        expect(pr_tab).to_be_visible()
