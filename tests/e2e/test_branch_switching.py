"""E2E tests: Branch switching in repository view.

Tests that the branch dropdown lists available branches and
switching branches updates the file tree view.
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


def _unique(prefix: str = "br") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def repo_with_branches():
    """Create a repo with multiple branches and different file trees."""
    group = _unique("grp")
    repo = _unique("repo")
    username = _unique("usr")

    requests.post(
        f"{BASE_URL}/api/groups", json={"name": group}, timeout=10,
    )
    requests.post(
        f"{BASE_URL}/api/repos/{group}",
        json={"name": repo, "description": "Branch test"},
        timeout=10,
    )
    requests.post(
        f"{BASE_URL}/api/access",
        json={"username": username, "resource": group, "level": "admin"},
        timeout=10,
    )

    # Register branches
    for branch in ["main", "develop", "feature-login"]:
        requests.post(
            f"{BASE_URL}/api/repos/{group}/{repo}/branches",
            json={"branch": branch},
            timeout=10,
        )

    # Seed different file trees for each branch
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    table = ddb.Table("gitgateway-repos")

    # main branch files
    main_files = [
        {"path": "README.md", "type": "file", "name": "README.md",
         "content": "# Main Branch\n", "last_commit_message": "Init",
         "last_commit_author": "alice"},
        {"path": "app.py", "type": "file", "name": "app.py",
         "content": "print('main')\n", "last_commit_message": "Add app",
         "last_commit_author": "alice"},
    ]
    for f in main_files:
        table.put_item(Item={
            "PK": f"TREE#{group}/{repo}#main",
            "SK": f"FILE#{f['path']}", **f,
        })

    # develop branch files
    dev_files = [
        {"path": "README.md", "type": "file", "name": "README.md",
         "content": "# Develop Branch\n", "last_commit_message": "Update",
         "last_commit_author": "bob"},
        {"path": "app.py", "type": "file", "name": "app.py",
         "content": "print('develop')\n", "last_commit_message": "Dev work",
         "last_commit_author": "bob"},
        {"path": "new_feature.py", "type": "file", "name": "new_feature.py",
         "content": "# new\n", "last_commit_message": "Add feature",
         "last_commit_author": "bob"},
    ]
    for f in dev_files:
        table.put_item(Item={
            "PK": f"TREE#{group}/{repo}#develop",
            "SK": f"FILE#{f['path']}", **f,
        })

    # feature-login branch files
    feat_files = [
        {"path": "login.py", "type": "file", "name": "login.py",
         "content": "def login(): pass\n",
         "last_commit_message": "Add login",
         "last_commit_author": "charlie"},
    ]
    for f in feat_files:
        table.put_item(Item={
            "PK": f"TREE#{group}/{repo}#feature-login",
            "SK": f"FILE#{f['path']}", **f,
        })

    yield {
        "group": group, "repo": repo, "username": username,
        "main_files": main_files, "dev_files": dev_files,
        "feat_files": feat_files,
    }

    # Cleanup
    for f in main_files:
        table.delete_item(Key={
            "PK": f"TREE#{group}/{repo}#main", "SK": f"FILE#{f['path']}",
        })
    for f in dev_files:
        table.delete_item(Key={
            "PK": f"TREE#{group}/{repo}#develop", "SK": f"FILE#{f['path']}",
        })
    for f in feat_files:
        table.delete_item(Key={
            "PK": f"TREE#{group}/{repo}#feature-login",
            "SK": f"FILE#{f['path']}",
        })
    for branch in ["main", "develop", "feature-login"]:
        table.delete_item(Key={
            "PK": f"BRANCHES#{group}/{repo}",
            "SK": f"BRANCH#{branch}",
        })
    requests.delete(
        f"{BASE_URL}/api/access",
        json={"username": username, "resource": group},
        timeout=10,
    )
    requests.delete(f"{BASE_URL}/api/repos/{group}/{repo}", timeout=10)
    requests.delete(f"{BASE_URL}/api/groups/{group}", timeout=10)


@pytest.mark.e2e
class TestBranchDropdown:
    """Test that the branch dropdown lists all available branches."""

    def test_dropdown_shows_all_branches(
        self, page: Page, repo_with_branches: dict,
    ) -> None:
        """Branch dropdown contains all registered branches."""
        data = repo_with_branches
        _set_cookie(page, data["username"])
        page.goto(f"{BASE_URL}/groups/{data['group']}/{data['repo']}")
        page.wait_for_load_state("domcontentloaded")

        branch_select = page.locator("select#branch-select")
        options = branch_select.locator("option").all_text_contents()
        assert "main" in options
        assert "develop" in options
        assert "feature-login" in options

    def test_default_branch_is_selected(
        self, page: Page, repo_with_branches: dict,
    ) -> None:
        """Default branch (main) is selected by default."""
        data = repo_with_branches
        _set_cookie(page, data["username"])
        page.goto(f"{BASE_URL}/groups/{data['group']}/{data['repo']}")
        page.wait_for_load_state("domcontentloaded")

        branch_select = page.locator("select#branch-select")
        selected = branch_select.input_value()
        assert selected == "main"


@pytest.mark.e2e
class TestBranchSwitching:
    """Test that switching branches changes the file tree."""

    def test_switch_to_develop_shows_different_files(
        self, page: Page, repo_with_branches: dict,
    ) -> None:
        """Switching to develop branch shows develop's files."""
        data = repo_with_branches
        _set_cookie(page, data["username"])

        # Start on main
        page.goto(f"{BASE_URL}/groups/{data['group']}/{data['repo']}")
        page.wait_for_load_state("domcontentloaded")

        # main should NOT have new_feature.py
        content = page.content()
        assert "new_feature.py" not in content

        # Switch to develop via navigation
        page.goto(
            f"{BASE_URL}/groups/{data['group']}/{data['repo']}/tree/develop",
        )
        page.wait_for_load_state("domcontentloaded")

        # develop should have new_feature.py
        content = page.content()
        assert "new_feature.py" in content

    def test_switch_to_feature_branch(
        self, page: Page, repo_with_branches: dict,
    ) -> None:
        """Switching to feature-login shows only that branch's files."""
        data = repo_with_branches
        _set_cookie(page, data["username"])

        # Navigate to feature-login branch
        page.goto(
            f"{BASE_URL}/groups/{data['group']}/{data['repo']}"
            f"/tree/feature-login",
        )
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert "login.py" in content
        # Should NOT have app.py (that's on main/develop)
        assert "app.py" not in content

    def test_url_reflects_selected_branch(
        self, page: Page, repo_with_branches: dict,
    ) -> None:
        """Selecting a branch via dropdown navigates to the branch URL."""
        data = repo_with_branches
        _set_cookie(page, data["username"])

        page.goto(f"{BASE_URL}/groups/{data['group']}/{data['repo']}")
        page.wait_for_load_state("domcontentloaded")

        # Use select which triggers JS navigation
        page.select_option("select#branch-select", "develop")

        # Wait for navigation to complete
        page.wait_for_url("**/tree/develop**", timeout=5000)
        assert "/tree/develop" in page.url

    def test_direct_branch_url_works(
        self, page: Page, repo_with_branches: dict,
    ) -> None:
        """Navigating directly to a branch URL shows correct files."""
        data = repo_with_branches
        _set_cookie(page, data["username"])

        page.goto(
            f"{BASE_URL}/groups/{data['group']}/{data['repo']}/tree/develop",
        )
        page.wait_for_load_state("domcontentloaded")

        content = page.content()
        assert "new_feature.py" in content
        assert "app.py" in content


@pytest.mark.e2e
class TestBranchesApi:
    """Test the branches API directly."""

    def test_list_branches(self, repo_with_branches: dict) -> None:
        """GET /api/repos/<group>/<repo>/branches returns list."""
        data = repo_with_branches
        resp = requests.get(
            f"{BASE_URL}/api/repos/{data['group']}/{data['repo']}/branches",
            timeout=10,
        )
        assert resp.status_code == 200
        branches = resp.json()
        assert "main" in branches
        assert "develop" in branches
        assert "feature-login" in branches

    def test_add_branch(self, repo_with_branches: dict) -> None:
        """POST /api/repos/<group>/<repo>/branches registers a branch."""
        data = repo_with_branches
        resp = requests.post(
            f"{BASE_URL}/api/repos/{data['group']}/{data['repo']}/branches",
            json={"branch": "hotfix/urgent"},
            timeout=10,
        )
        assert resp.status_code == 201

        # Verify it appears in list
        resp = requests.get(
            f"{BASE_URL}/api/repos/{data['group']}/{data['repo']}/branches",
            timeout=10,
        )
        assert "hotfix/urgent" in resp.json()

        # Cleanup
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.Table("gitgateway-repos")
        table.delete_item(Key={
            "PK": f"BRANCHES#{data['group']}/{data['repo']}",
            "SK": "BRANCH#hotfix/urgent",
        })
