"""E2E tests: Git LFS Batch API and Lock API.

Verifies the functional LFS flow against the deployed server:
- Auth enforcement
- Batch API presigned URL generation (upload/download)
- Scope enforcement (read-only cannot upload)
- Full round-trip: upload an object via presigned URL then download it
- Lock create/list/verify/unlock

Valid-credential tests require GITGATEWAY_TEST_TOKEN (a read/write PAT).
"""

from __future__ import annotations

import hashlib
import os

import pytest
import requests


BASE_URL = os.environ.get("GITGATEWAY_BASE_URL", "https://git.botthouse.net")
TEST_REPO = "botthouse/GitGateway"
LFS_URL = f"{BASE_URL}/{TEST_REPO}.git/info/lfs"
LFS_CT = "application/vnd.git-lfs+json"
TOKEN = os.environ.get("GITGATEWAY_TEST_TOKEN", "")


def _auth() -> tuple[str, str]:
    """Return Basic auth tuple for the test token."""
    return ("__token__", TOKEN)


@pytest.mark.e2e
class TestLfsAuthEnforcement:
    """LFS endpoints require authentication."""

    def test_batch_requires_auth(self) -> None:
        """Batch endpoint returns 401 without credentials."""
        resp = requests.post(
            f"{LFS_URL}/objects/batch",
            json={"operation": "download", "objects": []},
            headers={"Content-Type": LFS_CT},
            timeout=10,
        )
        assert resp.status_code == 401

    def test_batch_invalid_token(self) -> None:
        """Batch endpoint rejects an invalid token."""
        resp = requests.post(
            f"{LFS_URL}/objects/batch",
            auth=("__token__", "gg_invalid00000000000000000000000"),
            json={"operation": "download", "objects": []},
            headers={"Content-Type": LFS_CT},
            timeout=10,
        )
        assert resp.status_code == 401

    def test_locks_require_auth(self) -> None:
        """Lock listing requires auth."""
        resp = requests.get(f"{LFS_URL}/locks", timeout=10)
        assert resp.status_code == 401


@pytest.mark.e2e
class TestLfsBatch:
    """LFS Batch API with valid credentials."""

    def test_download_missing_object_returns_error(self) -> None:
        """Download batch for a nonexistent object returns a 404 error."""
        if not TOKEN:
            pytest.skip("GITGATEWAY_TEST_TOKEN not set")
        fake_oid = "0" * 64
        resp = requests.post(
            f"{LFS_URL}/objects/batch",
            auth=_auth(),
            json={
                "operation": "download",
                "transfers": ["basic"],
                "objects": [{"oid": fake_oid, "size": 123}],
            },
            headers={"Content-Type": LFS_CT},
            timeout=15,
        )
        assert resp.status_code == 200
        obj = resp.json()["objects"][0]
        assert obj["oid"] == fake_oid
        assert obj.get("error", {}).get("code") == 404

    def test_upload_returns_presigned_url(self) -> None:
        """Upload batch returns a presigned PUT URL."""
        if not TOKEN:
            pytest.skip("GITGATEWAY_TEST_TOKEN not set")
        oid = hashlib.sha256(b"e2e-upload-test").hexdigest()
        resp = requests.post(
            f"{LFS_URL}/objects/batch",
            auth=_auth(),
            json={
                "operation": "upload",
                "transfers": ["basic"],
                "objects": [{"oid": oid, "size": 15}],
            },
            headers={"Content-Type": LFS_CT},
            timeout=15,
        )
        assert resp.status_code == 200
        obj = resp.json()["objects"][0]
        assert "upload" in obj["actions"]
        assert obj["actions"]["upload"]["href"].startswith("https://")


@pytest.mark.e2e
class TestLfsRoundTrip:
    """Full upload then download via presigned URLs."""

    def test_upload_then_download(self) -> None:
        """Upload an object via presigned URL, then download it back."""
        if not TOKEN:
            pytest.skip("GITGATEWAY_TEST_TOKEN not set")

        content = b"GitGateway LFS round-trip test payload"
        oid = hashlib.sha256(content).hexdigest()
        size = len(content)

        # 1. Request upload URL
        up_resp = requests.post(
            f"{LFS_URL}/objects/batch",
            auth=_auth(),
            json={
                "operation": "upload",
                "transfers": ["basic"],
                "objects": [{"oid": oid, "size": size}],
            },
            headers={"Content-Type": LFS_CT},
            timeout=15,
        )
        assert up_resp.status_code == 200
        up_obj = up_resp.json()["objects"][0]
        upload_href = up_obj["actions"]["upload"]["href"]

        # 2. PUT the content to the presigned URL
        put_resp = requests.put(upload_href, data=content, timeout=30)
        assert put_resp.status_code in (200, 201)

        # 3. Request download URL
        down_resp = requests.post(
            f"{LFS_URL}/objects/batch",
            auth=_auth(),
            json={
                "operation": "download",
                "transfers": ["basic"],
                "objects": [{"oid": oid, "size": size}],
            },
            headers={"Content-Type": LFS_CT},
            timeout=15,
        )
        assert down_resp.status_code == 200
        down_obj = down_resp.json()["objects"][0]
        assert "download" in down_obj["actions"], (
            f"Expected download action, got: {down_obj}"
        )
        download_href = down_obj["actions"]["download"]["href"]

        # 4. GET the content back and verify it matches
        get_resp = requests.get(download_href, timeout=30)
        assert get_resp.status_code == 200
        assert get_resp.content == content


@pytest.mark.e2e
class TestLfsScopeEnforcement:
    """Read-only tokens cannot upload or lock."""

    def test_readonly_cannot_upload(self) -> None:
        """A read-only token is rejected for upload operations."""
        ro_token = os.environ.get("GITGATEWAY_TEST_READ_TOKEN", "")
        if not ro_token:
            pytest.skip("GITGATEWAY_TEST_READ_TOKEN not set")
        oid = hashlib.sha256(b"ro-test").hexdigest()
        resp = requests.post(
            f"{LFS_URL}/objects/batch",
            auth=("__token__", ro_token),
            json={
                "operation": "upload",
                "transfers": ["basic"],
                "objects": [{"oid": oid, "size": 7}],
            },
            headers={"Content-Type": LFS_CT},
            timeout=15,
        )
        assert resp.status_code == 403


@pytest.mark.e2e
class TestLfsLocks:
    """LFS file locking API."""

    def test_lock_lifecycle(self) -> None:
        """Create, list, verify, and unlock a file lock."""
        if not TOKEN:
            pytest.skip("GITGATEWAY_TEST_TOKEN not set")

        path = "assets/e2e-lock-test.bin"

        # Create a lock
        create_resp = requests.post(
            f"{LFS_URL}/locks",
            auth=_auth(),
            json={"path": path},
            headers={"Content-Type": LFS_CT},
            timeout=15,
        )
        # 201 created, or 409 if a stale lock exists from a prior run
        assert create_resp.status_code in (201, 409)

        # List locks — our path should be present
        list_resp = requests.get(
            f"{LFS_URL}/locks", auth=_auth(), timeout=15,
        )
        assert list_resp.status_code == 200
        locks = list_resp.json().get("locks", [])
        lock = next((lk for lk in locks if lk.get("path") == path), None)
        assert lock is not None, "Created lock not found in list"
        lock_id = lock["id"]

        # Verify — the lock should be listed under 'ours'
        verify_resp = requests.post(
            f"{LFS_URL}/locks/verify",
            auth=_auth(),
            json={},
            headers={"Content-Type": LFS_CT},
            timeout=15,
        )
        assert verify_resp.status_code == 200
        ours = verify_resp.json().get("ours", [])
        assert any(lk.get("id") == lock_id for lk in ours)

        # Unlock
        unlock_resp = requests.post(
            f"{LFS_URL}/locks/{lock_id}/unlock",
            auth=_auth(),
            json={},
            headers={"Content-Type": LFS_CT},
            timeout=15,
        )
        assert unlock_resp.status_code == 200
