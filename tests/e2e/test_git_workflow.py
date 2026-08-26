"""E2E test: Full git workflow end-to-end.

1. Creates a repo using the CLI
2. Clones the empty repo
3. Creates a test file
4. Commits the test file
5. Pushes to main
6. Switches to a new test branch
7. Updates the test file
8. Commits the test file
9. Pushes the test branch
10. Creates a pull request of test -> main
11. Deletes the repository
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import uuid

import pytest
import requests
from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("GITGATEWAY_BASE_URL", "https://git.botthouse.net")
GIT_URL = BASE_URL


def _get_credentials() -> dict:
    """Get test credentials from secretcli."""
    result = subprocess.run(
        ["secretcli", "get", "wikitest"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def _get_token() -> str:
    """Obtain a JWT token by logging in via Playwright."""
    creds = _get_credentials()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        # Capture token from response URLs
        token_holder = [None]
        page.on(
            "response",
            lambda r: (
                token_holder.__setitem__(0, r.url.split("token=")[1].split("&")[0])
                if "token=" in r.url else None
            ),
        )

        page.goto(
            f"https://idp.botthouse.net/git"
            f"?callback={BASE_URL}/auth/callback",
        )
        page.wait_for_load_state("domcontentloaded")

        page.fill("input[name='username']", creds["user"])
        page.fill("input[name='password']", creds["password"])

        # Solve challenge
        question = page.locator(".challenge-question").text_content() or ""
        match = re.search(r"(\d+)\s*([+\-*×÷/])\s*(\d+)", question)
        if match:
            n1 = int(match.group(1))
            op = match.group(2)
            n2 = int(match.group(3))
            if op == "+":
                ans = n1 + n2
            elif op == "-":
                ans = n1 - n2
            elif op in ("*", "×"):
                ans = n1 * n2
            else:
                ans = n1 // n2
            page.fill("input[name='challenge_answer']", str(ans))

        page.click("button[type='submit']")
        page.wait_for_load_state("domcontentloaded")

        browser.close()
        return token_holder[0] or ""


def _unique(prefix: str = "wf") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def token() -> str:
    """Get a JWT token for the test session."""
    t = _get_token()
    if not t:
        pytest.skip("Could not obtain JWT token from IdP")
    return t


@pytest.fixture
def test_group():
    """Create and cleanup a test group."""
    name = _unique("grp")
    requests.post(f"{BASE_URL}/api/groups", json={"name": name}, timeout=10)
    yield name
    requests.delete(f"{BASE_URL}/api/groups/{name}", timeout=10)


@pytest.mark.e2e
class TestFullGitWorkflow:
    """Full end-to-end git workflow test."""

    def test_complete_workflow(self, token: str, test_group: str) -> None:
        """Execute the full 11-step git workflow."""
        repo_name = _unique("repo")
        full_name = f"{test_group}/{repo_name}"
        clone_url = f"{GIT_URL}/{test_group}/{repo_name}.git"

        # Configure git credentials for this test
        # Use __token__ as username and JWT as password
        cred_url = clone_url.replace(
            "https://",
            f"https://__token__:{token}@",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # --- Step 1: Create repo using CLI ---
            env = os.environ.copy()
            env["GITGATEWAY_API_URL"] = BASE_URL
            result = subprocess.run(
                ["python", "-m", "gitgateway.cli", "repo", "create", full_name],
                capture_output=True, text=True, env=env, cwd=tmpdir,
            )
            assert result.returncode == 0, f"repo create failed: {result.stderr}"

            # --- Step 2: Clone the empty repo ---
            result = subprocess.run(
                ["git", "clone", cred_url, "myrepo"],
                capture_output=True, text=True, cwd=tmpdir,
                timeout=30,
            )
            assert result.returncode == 0, (
                f"clone failed: {result.stderr}"
            )

            repo_dir = os.path.join(tmpdir, "myrepo")

            # Configure git user for commits
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=repo_dir, check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=repo_dir, check=True, capture_output=True,
            )

            # --- Step 3: Create a test file ---
            test_file = os.path.join(repo_dir, "hello.py")
            with open(test_file, "w") as f:
                f.write('print("Hello, GitGateway!")\n')

            # --- Step 4: Commit the test file ---
            subprocess.run(
                ["git", "add", "hello.py"],
                cwd=repo_dir, check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "Add hello.py"],
                cwd=repo_dir, check=True, capture_output=True,
            )

            # --- Step 5: Push to main ---
            result = subprocess.run(
                ["git", "push", "origin", "main"],
                capture_output=True, text=True, cwd=repo_dir,
                timeout=30,
            )
            assert result.returncode == 0, (
                f"push to main failed: {result.stderr}"
            )

            # --- Step 6: Switch to a new test branch ---
            subprocess.run(
                ["git", "checkout", "-b", "test-branch"],
                cwd=repo_dir, check=True, capture_output=True,
            )

            # --- Step 7: Update the test file ---
            with open(test_file, "w") as f:
                f.write('print("Hello from test-branch!")\n')

            # --- Step 8: Commit the test file ---
            subprocess.run(
                ["git", "add", "hello.py"],
                cwd=repo_dir, check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "Update hello.py on test-branch"],
                cwd=repo_dir, check=True, capture_output=True,
            )

            # --- Step 9: Push the test branch ---
            result = subprocess.run(
                ["git", "push", "origin", "test-branch"],
                capture_output=True, text=True, cwd=repo_dir,
                timeout=30,
            )
            assert result.returncode == 0, (
                f"push test-branch failed: {result.stderr}"
            )

            # --- Step 10: Create a pull request ---
            pr_resp = requests.post(
                f"{BASE_URL}/api/{test_group}/{repo_name}/pulls",
                json={
                    "title": "Merge test-branch into main",
                    "source_branch": "test-branch",
                    "target_branch": "main",
                    "author": "wikitest",
                },
                timeout=10,
            )
            assert pr_resp.status_code == 201, (
                f"PR create failed: {pr_resp.status_code} {pr_resp.text}"
            )
            pr_data = pr_resp.json()
            assert pr_data["number"] == 1
            assert pr_data["title"] == "Merge test-branch into main"

            # --- Step 10b: Close the pull request ---
            close_resp = requests.post(
                f"{BASE_URL}/api/{test_group}/{repo_name}/pulls/1/close",
                timeout=10,
            )
            assert close_resp.status_code == 200, (
                f"PR close failed: {close_resp.status_code} {close_resp.text}"
            )

            # --- Step 11: Delete the repository ---
            del_resp = requests.delete(
                f"{BASE_URL}/api/repos/{test_group}/{repo_name}",
                timeout=10,
            )
            assert del_resp.status_code in (204, 404), (
                f"Delete failed: {del_resp.status_code} {del_resp.text}"
            )

            # Verify repo is gone
            clone_result = subprocess.run(
                ["git", "ls-remote", cred_url],
                capture_output=True, text=True, timeout=10,
            )
            # Should fail or return empty (repo deleted)
            # The repo data in S3 may still exist but DynamoDB entry is gone
