"""Shared fixtures for Playwright e2e tests."""

from __future__ import annotations

import os

import pytest


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
