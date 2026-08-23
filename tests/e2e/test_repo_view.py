"""E2E tests: GitHub-style repository view.

Tests the repo page with tabs, clone URL, branch selector,
file tree, and settings gear icon.
"""

from __future__ import annotations

import os
import uuid

import boto3
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
    """Set session cookie."""
    page.context.add_cookies([{
        "name": "session",
        "value": _create_session_cookie(username, is_admin),
        "domain": "git.botthouse.net",
        "path": "/",
        "secure": True,
        "httpOnly": True,
    }])


def _unique(prefix: str = "rv") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def repo_with_files():
    """Create a group, repo, grant access, and seed file tree in DynamoDB."""
    group = _unique("grp")
    repo = _unique("repo")
    username = _unique("usr")

    # Create group and repo via API
    requests.post(
        f"{BASE_URL}/api/groups", json={"name": group}, timeout=10,
    )
    requests.post(
        f"{BASE_URL}/api/repos/{group}",
        json={"name": repo, "description": "Test repository"},
        timeout=10,
    )
    # Grant admin access
    requests.post(
        f"{BASE_URL}/api/access",
        json={"username": username, "resource": group, "level": "admin"},
        timeout=10,
    )

    # Seed file tree in DynamoDB
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    table = ddb.Table("gitgateway-repos")
    files = [
        {
            "path": "README.md", "type": "file", "name": "README.md",
            "last_commit_message": "Initial commit",
            "last_commit_author": "alice",
            "content": "# My Project\n\nThis is a test repo.",
        },
        {
            "path": "src/main.py", "type": "file", "name": "main.py",
            "last_commit_message": "Add main module",
            "last_commit_author": "bob",
            "content": "def main():\n    print('hello')\n",
        },
        {
            "path": "src/__init__.py", "type": "file",
            "name": "__init__.py",
            "last_commit_message": "Add package",
            "last_commit_author": "alice",
            "content": "",
        },
        {
            "path": "pyproject.toml", "type": "file",
            "name": "pyproject.toml",
            "last_commit_message": "Add build config",
            "last_commit_author": "bob",
            "content": "[project]\nname = 'test'\n",
        },
    ]
    for f in files:
        table.put_item(Item={
            "PK": f"TREE#{group}/{repo}#main",
            "SK": f"FILE#{f['path']}",
            **f,
        })

    yield {
        "group": group, "repo": repo, "username": username,
        "files": files,
    }

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
class TestRepoPageShell:
    """Test the repo page structure: tabs, clone URL, branch selector."""

    def test_repo_page_loads(
        self, page: Page, repo_with_files: dict,
    ) -> None:
        """Repo page loads with group/repo header."""
        data = repo_with_files
        _set_cookie(page, data["username"])
        page.goto(f"{BASE_URL}/groups/{data['group']}/{data['repo']}")
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert data["group"] in content
        assert data["repo"] in content

    def test_has_code_tab(
        self, page: Page, repo_with_files: dict,
    ) -> None:
        """Repo page has a Code tab."""
        data = repo_with_files
        _set_cookie(page, data["username"])
        page.goto(f"{BASE_URL}/groups/{data['group']}/{data['repo']}")
        page.wait_for_load_state("domcontentloaded")

        code_tab = page.locator("a:has-text('Code')")
        expect(code_tab).to_be_visible()

    def test_has_pull_requests_tab(
        self, page: Page, repo_with_files: dict,
    ) -> None:
        """Repo page has a Pull Requests tab."""
        data = repo_with_files
        _set_cookie(page, data["username"])
        page.goto(f"{BASE_URL}/groups/{data['group']}/{data['repo']}")
        page.wait_for_load_state("domcontentloaded")

        pr_tab = page.locator("a:has-text('Pull Requests')")
        expect(pr_tab).to_be_visible()

    def test_has_clone_url(
        self, page: Page, repo_with_files: dict,
    ) -> None:
        """Repo page shows the clone URL."""
        data = repo_with_files
        _set_cookie(page, data["username"])
        page.goto(f"{BASE_URL}/groups/{data['group']}/{data['repo']}")
        page.wait_for_load_state("domcontentloaded")

        clone_input = page.locator("input#clone-url")
        expect(clone_input).to_be_visible()
        value = clone_input.input_value()
        assert f"{data['group']}/{data['repo']}.git" in value

    def test_has_branch_selector(
        self, page: Page, repo_with_files: dict,
    ) -> None:
        """Repo page has a branch selector dropdown."""
        data = repo_with_files
        _set_cookie(page, data["username"])
        page.goto(f"{BASE_URL}/groups/{data['group']}/{data['repo']}")
        page.wait_for_load_state("domcontentloaded")

        branch_select = page.locator("select#branch-select")
        expect(branch_select).to_be_visible()

    def test_admin_sees_settings_gear(
        self, page: Page, repo_with_files: dict,
    ) -> None:
        """Admin user sees settings gear icon."""
        data = repo_with_files
        _set_cookie(page, data["username"])
        page.goto(f"{BASE_URL}/groups/{data['group']}/{data['repo']}")
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert "⚙️" in content or "settings" in content.lower()

    def test_readonly_no_settings_gear(
        self, page: Page, repo_with_files: dict,
    ) -> None:
        """Readonly user does not see settings gear."""
        data = repo_with_files
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
        page.goto(f"{BASE_URL}/groups/{data['group']}/{data['repo']}")
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert "⚙️" not in content

        requests.delete(
            f"{BASE_URL}/api/access",
            json={"username": readonly_user, "resource": data["group"]},
            timeout=10,
        )


@pytest.mark.e2e
class TestRepoFileTree:
    """Test the file tree display."""

    def test_shows_root_files(
        self, page: Page, repo_with_files: dict,
    ) -> None:
        """Shows files at the root level."""
        data = repo_with_files
        _set_cookie(page, data["username"])
        page.goto(f"{BASE_URL}/groups/{data['group']}/{data['repo']}")
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert "README.md" in content
        assert "pyproject.toml" in content

    def test_shows_directories(
        self, page: Page, repo_with_files: dict,
    ) -> None:
        """Shows directories with folder icon."""
        data = repo_with_files
        _set_cookie(page, data["username"])
        page.goto(f"{BASE_URL}/groups/{data['group']}/{data['repo']}")
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        # Should show 'src' as a directory
        assert "src" in content
        assert "📁" in content

    def test_shows_last_commit_info(
        self, page: Page, repo_with_files: dict,
    ) -> None:
        """Shows last commit message and author per file."""
        data = repo_with_files
        _set_cookie(page, data["username"])
        page.goto(f"{BASE_URL}/groups/{data['group']}/{data['repo']}")
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert "Initial commit" in content
        assert "alice" in content

    def test_empty_repo_shows_message(self, page: Page) -> None:
        """Empty repo shows 'repository is empty' message."""
        group = _unique("grp")
        repo = _unique("repo")
        username = _unique("usr")

        requests.post(
            f"{BASE_URL}/api/groups", json={"name": group}, timeout=10,
        )
        requests.post(
            f"{BASE_URL}/api/repos/{group}",
            json={"name": repo}, timeout=10,
        )
        requests.post(
            f"{BASE_URL}/api/access",
            json={"username": username, "resource": group, "level": "admin"},
            timeout=10,
        )

        _set_cookie(page, username)
        page.goto(f"{BASE_URL}/groups/{group}/{repo}")
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert "empty" in content.lower()

        # Cleanup
        requests.delete(
            f"{BASE_URL}/api/access",
            json={"username": username, "resource": group},
            timeout=10,
        )
        requests.delete(f"{BASE_URL}/api/repos/{group}/{repo}", timeout=10)
        requests.delete(f"{BASE_URL}/api/groups/{group}", timeout=10)


@pytest.mark.e2e
class TestRepoReadme:
    """Test README rendering below the file tree."""

    def test_readme_rendered(
        self, page: Page, repo_with_files: dict,
    ) -> None:
        """README.md content is rendered below the file tree."""
        data = repo_with_files
        _set_cookie(page, data["username"])
        page.goto(f"{BASE_URL}/groups/{data['group']}/{data['repo']}")
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert "My Project" in content
        assert "This is a test repo" in content
