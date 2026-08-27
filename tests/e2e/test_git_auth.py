"""E2E tests: Git authentication enforcement.

Verifies that the git server requires valid credentials for all
operations. Unauthenticated requests must return 401. Invalid
tokens must be rejected. Valid tokens must succeed.
"""

from __future__ import annotations

import os
import subprocess
import tempfile

import pytest
import requests


BASE_URL = os.environ.get("GITGATEWAY_BASE_URL", "https://git.botthouse.net")
TEST_REPO = "botthouse/GitGateway"
REPO_GIT_URL = f"{BASE_URL}/{TEST_REPO}.git"


@pytest.mark.e2e
class TestGitAuthEnforcement:
    """Verify git operations are not open to the world."""

    def test_info_refs_returns_401_without_credentials(self) -> None:
        """GET /info/refs without auth returns 401."""
        resp = requests.get(
            f"{REPO_GIT_URL}/info/refs?service=git-upload-pack",
            timeout=10,
        )
        assert resp.status_code == 401, (
            f"Expected 401 without auth, got {resp.status_code}"
        )

    def test_info_refs_returns_www_authenticate_header(self) -> None:
        """401 response includes WWW-Authenticate: Basic header."""
        resp = requests.get(
            f"{REPO_GIT_URL}/info/refs?service=git-upload-pack",
            timeout=10,
        )
        assert resp.status_code == 401
        www_auth = resp.headers.get("WWW-Authenticate", "")
        assert "Basic" in www_auth, (
            f"Expected WWW-Authenticate: Basic, got: {www_auth}"
        )

    def test_upload_pack_returns_401_without_credentials(self) -> None:
        """POST /git-upload-pack without auth returns 401."""
        resp = requests.post(
            f"{REPO_GIT_URL}/git-upload-pack",
            data=b"",
            timeout=10,
        )
        assert resp.status_code == 401, (
            f"Expected 401 without auth, got {resp.status_code}"
        )

    def test_receive_pack_returns_401_without_credentials(self) -> None:
        """POST /git-receive-pack without auth returns 401."""
        resp = requests.post(
            f"{REPO_GIT_URL}/git-receive-pack",
            data=b"",
            timeout=10,
        )
        assert resp.status_code == 401, (
            f"Expected 401 without auth, got {resp.status_code}"
        )

    def test_git_clone_fails_without_credentials(self) -> None:
        """git clone without credentials fails with auth error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    "git", "-c", "credential.helper=",
                    "clone", REPO_GIT_URL, "repo",
                ],
                capture_output=True,
                text=True,
                cwd=tmpdir,
                timeout=15,
                env={
                    **os.environ,
                    "GIT_TERMINAL_PROMPT": "0",
                    "GIT_ASKPASS": "echo",
                },
            )
            assert result.returncode != 0, "Clone should fail without auth"
            combined = result.stderr + result.stdout
            assert (
                "Authentication failed" in combined
                or "could not read Username" in combined
                or "terminal prompts disabled" in combined
                or "401" in combined
            ), f"Unexpected error: {combined}"

    def test_git_ls_remote_fails_without_credentials(self) -> None:
        """git ls-remote without credentials fails."""
        result = subprocess.run(
            [
                "git", "-c", "credential.helper=",
                "ls-remote", REPO_GIT_URL,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            env={
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": "echo",
            },
        )
        assert result.returncode != 0, "ls-remote should fail without auth"


@pytest.mark.e2e
class TestGitAuthInvalidCredentials:
    """Verify invalid credentials are rejected."""

    def test_info_refs_with_invalid_token_returns_401(self) -> None:
        """Invalid token is rejected."""
        resp = requests.get(
            f"{REPO_GIT_URL}/info/refs?service=git-upload-pack",
            auth=("__token__", "gg_invalidtoken000000000000000000"),
            timeout=10,
        )
        assert resp.status_code == 401, (
            f"Expected 401 with invalid token, got {resp.status_code}"
        )

    def test_info_refs_with_expired_jwt_returns_401(self) -> None:
        """Expired JWT is rejected."""
        # A clearly expired/malformed JWT
        fake_jwt = "eyJhbGciOiJSUzI1NiJ9.eyJleHAiOjB9.invalid"
        resp = requests.get(
            f"{REPO_GIT_URL}/info/refs?service=git-upload-pack",
            auth=("__token__", fake_jwt),
            timeout=10,
        )
        assert resp.status_code == 401, (
            f"Expected 401 with expired JWT, got {resp.status_code}"
        )

    def test_info_refs_with_wrong_username_returns_401(self) -> None:
        """Non-__token__ username is rejected."""
        resp = requests.get(
            f"{REPO_GIT_URL}/info/refs?service=git-upload-pack",
            auth=("someuser", "somepassword"),
            timeout=10,
        )
        assert resp.status_code == 401, (
            f"Expected 401 with wrong username, got {resp.status_code}"
        )

    def test_git_clone_with_invalid_token_fails(self) -> None:
        """git clone with an invalid token fails."""
        bad_url = REPO_GIT_URL.replace(
            "https://",
            "https://__token__:gg_badtoken1234567890bad12345678@",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    "git", "-c", "credential.helper=",
                    "clone", bad_url, "repo",
                ],
                capture_output=True,
                text=True,
                cwd=tmpdir,
                timeout=15,
                env={
                    **os.environ,
                    "GIT_TERMINAL_PROMPT": "0",
                },
            )
            assert result.returncode != 0, (
                "Clone should fail with invalid token"
            )


@pytest.mark.e2e
class TestGitAuthValidCredentials:
    """Verify valid credentials allow access."""

    def test_info_refs_with_valid_token_succeeds(self) -> None:
        """Valid PAT returns git ref advertisement."""
        token = os.environ.get("GITGATEWAY_TEST_TOKEN", "")
        if not token:
            pytest.skip("GITGATEWAY_TEST_TOKEN not set")

        resp = requests.get(
            f"{REPO_GIT_URL}/info/refs?service=git-upload-pack",
            auth=("__token__", token),
            timeout=10,
        )
        assert resp.status_code == 200, (
            f"Expected 200 with valid token, got {resp.status_code}"
        )
        assert b"refs/heads/" in resp.content, (
            "Response should contain git refs"
        )

    def test_git_ls_remote_with_valid_token_succeeds(self) -> None:
        """git ls-remote with valid token lists refs."""
        token = os.environ.get("GITGATEWAY_TEST_TOKEN", "")
        if not token:
            pytest.skip("GITGATEWAY_TEST_TOKEN not set")

        auth_url = REPO_GIT_URL.replace(
            "https://",
            f"https://__token__:{token}@",
        )
        result = subprocess.run(
            [
                "git", "-c", "credential.helper=",
                "ls-remote", auth_url,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, (
            f"ls-remote should succeed with valid token: {result.stderr}"
        )
        assert "refs/heads/" in result.stdout, (
            "Should list at least one branch ref"
        )

    def test_git_clone_with_valid_token_succeeds(self) -> None:
        """git clone with valid token clones successfully."""
        token = os.environ.get("GITGATEWAY_TEST_TOKEN", "")
        if not token:
            pytest.skip("GITGATEWAY_TEST_TOKEN not set")

        auth_url = REPO_GIT_URL.replace(
            "https://",
            f"https://__token__:{token}@",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    "git", "-c", "credential.helper=",
                    "clone", "--depth=1", auth_url, "repo",
                ],
                capture_output=True,
                text=True,
                cwd=tmpdir,
                timeout=30,
            )
            assert result.returncode == 0, (
                f"Clone should succeed with valid token: {result.stderr}"
            )
            # Verify files exist
            assert os.path.isdir(os.path.join(tmpdir, "repo", ".git"))
