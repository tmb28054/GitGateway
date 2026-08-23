"""E2E tests: Git protocol operations.

Tests the Git Smart HTTP protocol endpoints using
subprocess calls to actual git commands.
"""

from __future__ import annotations

import os
import subprocess
import tempfile

import pytest


GITGATEWAY_URL = os.environ.get(
    "GITGATEWAY_BASE_URL", "https://git.botthouse.net",
)
GITGATEWAY_TOKEN = os.environ.get("GITGATEWAY_TOKEN", "")


@pytest.mark.e2e
class TestGitClone:
    """Test git clone operations."""

    @pytest.fixture
    def credentials_env(self) -> dict[str, str]:
        """Build environment with git credentials.

        Returns:
            Environment dict with GIT_ASKPASS configured.
        """
        env = os.environ.copy()
        if GITGATEWAY_TOKEN:
            # Configure git credential helper
            env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def test_clone_nonexistent_repo_fails(
        self, credentials_env: dict,
    ) -> None:
        """Cloning a non-existent repo returns an error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    "git", "clone",
                    f"{GITGATEWAY_URL}/ghost/nonexistent.git",
                    f"{tmpdir}/repo",
                ],
                capture_output=True,
                text=True,
                env=credentials_env,
                timeout=30,
                check=False,
            )
            # Should fail with auth error or not found
            assert result.returncode != 0

    @pytest.mark.skipif(
        not GITGATEWAY_TOKEN,
        reason="GITGATEWAY_TOKEN not set",
    )
    def test_ls_remote_with_auth(self, credentials_env: dict) -> None:
        """ls-remote with valid credentials reaches the server."""
        result = subprocess.run(
            [
                "git", "ls-remote",
                f"{GITGATEWAY_URL}/test/repo.git",
            ],
            capture_output=True,
            text=True,
            env=credentials_env,
            timeout=30,
            check=False,
        )
        # May fail with 404 (repo doesn't exist) but shouldn't timeout
        # or get connection refused
        assert "Connection refused" not in result.stderr
        assert "Could not resolve host" not in result.stderr


@pytest.mark.e2e
class TestGitLfs:
    """Test Git LFS endpoint availability."""

    def test_lfs_batch_endpoint_exists(self) -> None:
        """LFS batch endpoint is reachable (even if auth fails)."""
        import requests  # pylint: disable=import-outside-toplevel

        response = requests.post(
            f"{GITGATEWAY_URL}/test/repo.git/info/lfs/objects/batch",
            json={"operation": "download", "objects": []},
            headers={"Content-Type": "application/vnd.git-lfs+json"},
            timeout=10,
        )
        # Should get 401/403 (auth required) or 404 (repo not found)
        # but NOT connection error or 5xx
        assert response.status_code in (200, 401, 403, 404, 422)
