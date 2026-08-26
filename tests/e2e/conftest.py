"""Shared fixtures for Playwright e2e tests."""

from __future__ import annotations

import os

import pytest
import requests


BASE_URL = os.environ.get(
    "GITGATEWAY_BASE_URL", "https://git.botthouse.net",
)


@pytest.fixture(scope="session")
def base_url() -> str:
    """Base URL for the deployed GitGateway instance.

    Override with GITGATEWAY_BASE_URL environment variable.

    Returns:
        The base URL string.
    """
    return BASE_URL


@pytest.fixture(scope="session")
def api_url(base_url: str) -> str:
    """API base URL.

    Returns:
        The API URL string.
    """
    return f"{base_url}/api"


@pytest.fixture
def pr_cleanup():
    """Fixture that tracks and cleans up PRs created during a test.

    Usage:
        def test_something(pr_cleanup):
            # Create a PR
            pr_cleanup.track("group", "repo", 1)
            ...
        # PR is automatically closed after test

    Yields:
        A PrCleanup helper instance.
    """
    tracker = _PrCleanup()
    yield tracker
    tracker.cleanup()


class _PrCleanup:
    """Helper to track and close PRs created during tests."""

    def __init__(self) -> None:
        self._prs: list[tuple[str, str, int]] = []

    def track(self, group: str, repo: str, number: int) -> None:
        """Register a PR for cleanup.

        Args:
            group: Group name.
            repo: Repository name.
            number: PR number.
        """
        self._prs.append((group, repo, number))

    def cleanup(self) -> None:
        """Close all tracked PRs (best effort)."""
        for group, repo, number in self._prs:
            try:
                requests.post(
                    f"{BASE_URL}/api/{group}/{repo}/pulls/{number}/close",
                    timeout=10,
                )
            except Exception:  # pylint: disable=broad-except
                pass
