"""E2E tests: Group management via REST API.

Tests create, list, and delete group operations against
the deployed GitGateway instance.
"""

from __future__ import annotations

import os
import uuid

import pytest
import requests


BASE_URL = os.environ.get("GITGATEWAY_BASE_URL", "https://git.botthouse.net")


@pytest.mark.e2e
class TestGroupCrud:
    """Test group create, list, and delete via API."""

    def _unique_name(self) -> str:
        """Generate a unique group name for testing."""
        return f"e2e-test-{uuid.uuid4().hex[:8]}"

    def test_create_group(self) -> None:
        """POST /api/groups creates a new group."""
        name = self._unique_name()
        response = requests.post(
            f"{BASE_URL}/api/groups",
            json={"name": name, "description": "E2E test group"},
            timeout=10,
        )
        assert response.status_code in (200, 201), (
            f"Create failed: {response.status_code} {response.text}"
        )
        data = response.json()
        assert data.get("name") == name

        # Cleanup
        requests.delete(f"{BASE_URL}/api/groups/{name}", timeout=10)

    def test_list_groups(self) -> None:
        """GET /api/groups returns a list of groups."""
        # Create a group first
        name = self._unique_name()
        requests.post(
            f"{BASE_URL}/api/groups",
            json={"name": name},
            timeout=10,
        )

        # List groups
        response = requests.get(f"{BASE_URL}/api/groups", timeout=10)
        assert response.status_code == 200, (
            f"List failed: {response.status_code} {response.text}"
        )
        data = response.json()
        assert isinstance(data, list)
        names = [g.get("name") for g in data]
        assert name in names

        # Cleanup
        requests.delete(f"{BASE_URL}/api/groups/{name}", timeout=10)

    def test_delete_group(self) -> None:
        """DELETE /api/groups/<name> removes the group."""
        name = self._unique_name()
        # Create
        requests.post(
            f"{BASE_URL}/api/groups",
            json={"name": name},
            timeout=10,
        )

        # Delete
        response = requests.delete(
            f"{BASE_URL}/api/groups/{name}",
            timeout=10,
        )
        assert response.status_code in (200, 204), (
            f"Delete failed: {response.status_code} {response.text}"
        )

        # Verify gone
        response = requests.get(f"{BASE_URL}/api/groups", timeout=10)
        data = response.json()
        names = [g.get("name") for g in data]
        assert name not in names

    def test_create_duplicate_group_fails(self) -> None:
        """Creating a group with an existing name fails."""
        name = self._unique_name()
        # Create first
        requests.post(
            f"{BASE_URL}/api/groups",
            json={"name": name},
            timeout=10,
        )

        # Try duplicate
        response = requests.post(
            f"{BASE_URL}/api/groups",
            json={"name": name},
            timeout=10,
        )
        assert response.status_code == 409, (
            f"Expected 409 conflict, got: {response.status_code}"
        )

        # Cleanup
        requests.delete(f"{BASE_URL}/api/groups/{name}", timeout=10)

    def test_delete_nonexistent_group_fails(self) -> None:
        """Deleting a non-existent group returns 404."""
        response = requests.delete(
            f"{BASE_URL}/api/groups/nonexistent-{uuid.uuid4().hex[:8]}",
            timeout=10,
        )
        assert response.status_code == 404
