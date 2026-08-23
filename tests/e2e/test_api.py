"""E2E tests: REST API operations.

Tests the JSON API endpoints for proper responses,
authentication enforcement, and data operations.
"""

from __future__ import annotations

import os

import pytest
import requests


GITGATEWAY_URL = os.environ.get(
    "GITGATEWAY_BASE_URL", "https://git.botthouse.net",
)
GITGATEWAY_TOKEN = os.environ.get("GITGATEWAY_TOKEN", "")


@pytest.mark.e2e
class TestApiAuthentication:
    """Test API authentication enforcement."""

    def test_groups_requires_auth(self) -> None:
        """GET /api/groups requires authentication."""
        response = requests.get(
            f"{GITGATEWAY_URL}/api/groups",
            timeout=10,
        )
        assert response.status_code in (401, 403)

    def test_repos_requires_auth(self) -> None:
        """GET /api/repos requires authentication."""
        response = requests.get(
            f"{GITGATEWAY_URL}/api/repos",
            timeout=10,
        )
        assert response.status_code in (401, 403)

    def test_invalid_token_rejected(self) -> None:
        """Invalid Bearer token is rejected."""
        response = requests.get(
            f"{GITGATEWAY_URL}/api/groups",
            headers={"Authorization": "Bearer invalid-token"},
            timeout=10,
        )
        assert response.status_code in (401, 403)


@pytest.mark.e2e
@pytest.mark.skipif(
    not GITGATEWAY_TOKEN,
    reason="GITGATEWAY_TOKEN not set",
)
class TestApiOperations:
    """Test API operations with valid authentication."""

    @pytest.fixture
    def auth_headers(self) -> dict[str, str]:
        """Build authenticated headers.

        Returns:
            Headers dict with Bearer token.
        """
        return {"Authorization": f"Bearer {GITGATEWAY_TOKEN}"}

    def test_list_groups(self, auth_headers: dict) -> None:
        """GET /api/groups returns list."""
        response = requests.get(
            f"{GITGATEWAY_URL}/api/groups",
            headers=auth_headers,
            timeout=10,
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, (list, dict))

    def test_create_and_delete_group(self, auth_headers: dict) -> None:
        """Create and delete a test group."""
        # Create
        response = requests.post(
            f"{GITGATEWAY_URL}/api/groups",
            headers=auth_headers,
            json={"name": "e2e-test-group", "description": "E2E test"},
            timeout=10,
        )
        assert response.status_code in (200, 201)

        # Verify
        response = requests.get(
            f"{GITGATEWAY_URL}/api/groups",
            headers=auth_headers,
            timeout=10,
        )
        assert response.status_code == 200

        # Cleanup
        requests.delete(
            f"{GITGATEWAY_URL}/api/groups/e2e-test-group",
            headers=auth_headers,
            timeout=10,
        )

    def test_create_and_delete_repo(self, auth_headers: dict) -> None:
        """Create and delete a test repository."""
        # Ensure group exists
        requests.post(
            f"{GITGATEWAY_URL}/api/groups",
            headers=auth_headers,
            json={"name": "e2e-test"},
            timeout=10,
        )

        # Create repo
        response = requests.post(
            f"{GITGATEWAY_URL}/api/e2e-test/e2e-repo",
            headers=auth_headers,
            json={"description": "E2E test repo"},
            timeout=10,
        )
        assert response.status_code in (200, 201)

        # Cleanup
        requests.delete(
            f"{GITGATEWAY_URL}/api/e2e-test/e2e-repo",
            headers=auth_headers,
            timeout=10,
        )
        requests.delete(
            f"{GITGATEWAY_URL}/api/groups/e2e-test",
            headers=auth_headers,
            timeout=10,
        )


@pytest.mark.e2e
class TestApiCors:
    """Test CORS headers on API responses."""

    def test_options_returns_cors_headers(self) -> None:
        """OPTIONS request returns proper CORS headers."""
        response = requests.options(
            f"{GITGATEWAY_URL}/api/groups",
            timeout=10,
        )
        assert response.status_code in (200, 204)
        assert "access-control-allow-origin" in {
            k.lower() for k in response.headers.keys()
        }
