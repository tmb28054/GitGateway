"""Tests for gitgateway.lfs module."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from gitgateway.lfs import (
    LfsAction,
    LfsError,
    LfsLock,
    LfsObject,
    LfsObjectResponse,
    _get_s3_client,
    _lfs_s3_key,
    clear_locks,
    create_lock,
    delete_lock,
    handle_batch_request,
    list_locks,
    verify_locks,
)


BUCKET = "test-lfs-bucket"


@pytest.fixture(autouse=True)
def _clear_lock_store():
    """Clear lock store between tests."""
    clear_locks()
    yield
    clear_locks()


@pytest.fixture
def s3():
    """Create a mocked S3 client with a test bucket."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


class TestLfsS3Key:
    """Tests for _lfs_s3_key helper."""

    def test_basic_key(self) -> None:
        """Builds correct S3 key with fan-out."""
        oid = "abcdef1234567890"
        key = _lfs_s3_key("team", "app", oid)
        assert key == "lfs/team/app/ab/cd/abcdef1234567890"

    def test_short_oid(self) -> None:
        """Handles short OIDs gracefully."""
        key = _lfs_s3_key("g", "r", "ab")
        assert key == "lfs/g/r/ab//ab"


class TestGetS3Client:
    """Tests for _get_s3_client helper."""

    @mock_aws
    def test_default_client(self) -> None:
        """Creates default boto3 client when none provided."""
        client = _get_s3_client()
        assert client is not None

    def test_returns_provided_client(self, s3) -> None:
        """Returns the provided client."""
        result = _get_s3_client(s3)
        assert result is s3


class TestLfsAction:
    """Tests for LfsAction dataclass."""

    def test_to_dict_basic(self) -> None:
        """Serializes with no header."""
        action = LfsAction(href="https://s3.example.com/obj")
        result = action.to_dict()
        assert result["href"] == "https://s3.example.com/obj"
        assert result["expires_in"] == 3600
        assert "header" not in result

    def test_to_dict_with_header(self) -> None:
        """Serializes with headers."""
        action = LfsAction(
            href="https://s3.example.com/obj",
            header={"Authorization": "Bearer xyz"},
        )
        result = action.to_dict()
        assert result["header"]["Authorization"] == "Bearer xyz"


class TestLfsObjectResponse:
    """Tests for LfsObjectResponse dataclass."""

    def test_to_dict_with_actions(self) -> None:
        """Serializes with actions."""
        resp = LfsObjectResponse(
            oid="abc123",
            size=1024,
            actions={"upload": LfsAction(href="https://url")},
        )
        result = resp.to_dict()
        assert result["oid"] == "abc123"
        assert result["size"] == 1024
        assert result["authenticated"] is True
        assert "upload" in result["actions"]

    def test_to_dict_with_error(self) -> None:
        """Serializes with error."""
        resp = LfsObjectResponse(
            oid="abc123",
            size=0,
            error={"code": 404, "message": "Not found"},
        )
        result = resp.to_dict()
        assert result["error"]["code"] == 404
        assert "actions" not in result

    def test_to_dict_no_actions_no_error(self) -> None:
        """Serializes with neither actions nor error."""
        resp = LfsObjectResponse(oid="abc", size=100)
        result = resp.to_dict()
        assert "actions" not in result
        assert "error" not in result


class TestLfsLock:
    """Tests for LfsLock dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to LFS lock format."""
        lock = LfsLock(
            lock_id="uuid-1",
            path="large.bin",
            owner="alice",
            locked_at="2025-01-01T00:00:00Z",
        )
        result = lock.to_dict()
        assert result["id"] == "uuid-1"
        assert result["path"] == "large.bin"
        assert result["owner"]["name"] == "alice"
        assert result["locked_at"] == "2025-01-01T00:00:00Z"


class TestHandleBatchRequest:
    """Tests for handle_batch_request function."""

    def test_upload_generates_presigned_urls(self, s3) -> None:
        """Upload operation generates presigned PUT URLs."""
        result = handle_batch_request(
            "team", "app", "upload",
            [{"oid": "abcdef1234", "size": 512}],
            bucket=BUCKET, s3_client=s3,
        )
        assert result["transfer"] == "basic"
        assert len(result["objects"]) == 1
        obj = result["objects"][0]
        assert obj["oid"] == "abcdef1234"
        assert "upload" in obj["actions"]
        assert "https" in obj["actions"]["upload"]["href"] or "http" in obj["actions"]["upload"]["href"]

    def test_download_existing_object(self, s3) -> None:
        """Download generates presigned GET URL for existing object."""
        # Put an object first
        s3_key = "lfs/team/app/ab/cd/abcdef1234"
        s3.put_object(Bucket=BUCKET, Key=s3_key, Body=b"data")

        result = handle_batch_request(
            "team", "app", "download",
            [{"oid": "abcdef1234", "size": 4}],
            bucket=BUCKET, s3_client=s3,
        )
        obj = result["objects"][0]
        assert "download" in obj["actions"]

    def test_download_missing_object(self, s3) -> None:
        """Download returns error for missing object."""
        result = handle_batch_request(
            "team", "app", "download",
            [{"oid": "nonexistent1", "size": 100}],
            bucket=BUCKET, s3_client=s3,
        )
        obj = result["objects"][0]
        assert obj["error"]["code"] == 404

    def test_invalid_operation_raises(self, s3) -> None:
        """Raises LfsError for invalid operation."""
        with pytest.raises(LfsError, match="Invalid operation"):
            handle_batch_request(
                "team", "app", "invalid",
                [{"oid": "abc", "size": 1}],
                bucket=BUCKET, s3_client=s3,
            )

    def test_missing_oid(self, s3) -> None:
        """Returns error for object with missing OID."""
        result = handle_batch_request(
            "team", "app", "upload",
            [{"size": 100}],
            bucket=BUCKET, s3_client=s3,
        )
        obj = result["objects"][0]
        assert obj["error"]["code"] == 422

    def test_multiple_objects(self, s3) -> None:
        """Handles multiple objects in one batch."""
        result = handle_batch_request(
            "team", "app", "upload",
            [
                {"oid": "aaa111", "size": 100},
                {"oid": "bbb222", "size": 200},
            ],
            bucket=BUCKET, s3_client=s3,
        )
        assert len(result["objects"]) == 2


class TestLockApi:
    """Tests for LFS lock operations."""

    def test_create_lock(self) -> None:
        """Creates a lock successfully."""
        lock = create_lock("team", "app", "large.bin", "alice")
        assert lock.path == "large.bin"
        assert lock.owner == "alice"
        assert lock.lock_id

    def test_create_duplicate_lock_raises(self) -> None:
        """Raises LfsError when file already locked."""
        create_lock("team", "app", "file.dat", "alice")
        with pytest.raises(LfsError, match="already locked"):
            create_lock("team", "app", "file.dat", "bob")

    def test_list_locks(self) -> None:
        """Lists all locks for a repo."""
        create_lock("team", "app", "a.bin", "alice")
        create_lock("team", "app", "b.bin", "bob")
        locks = list_locks("team", "app")
        assert len(locks) == 2

    def test_list_locks_filtered_by_path(self) -> None:
        """Filters locks by path."""
        create_lock("team", "app", "a.bin", "alice")
        create_lock("team", "app", "b.bin", "bob")
        locks = list_locks("team", "app", path="a.bin")
        assert len(locks) == 1
        assert locks[0].path == "a.bin"

    def test_list_locks_empty(self) -> None:
        """Returns empty list for repo with no locks."""
        locks = list_locks("team", "app")
        assert locks == []

    def test_verify_locks(self) -> None:
        """Separates locks into ours and theirs."""
        create_lock("team", "app", "mine.bin", "alice")
        create_lock("team", "app", "yours.bin", "bob")
        ours, theirs = verify_locks("team", "app", "alice")
        assert len(ours) == 1
        assert len(theirs) == 1
        assert ours[0].owner == "alice"
        assert theirs[0].owner == "bob"

    def test_verify_locks_empty_repo(self) -> None:
        """Returns empty lists for repo with no locks."""
        ours, theirs = verify_locks("team", "app", "alice")
        assert ours == []
        assert theirs == []

    def test_delete_lock_by_owner(self) -> None:
        """Owner can delete their own lock."""
        lock = create_lock("team", "app", "file.bin", "alice")
        deleted = delete_lock("team", "app", lock.lock_id, "alice")
        assert deleted.lock_id == lock.lock_id
        assert list_locks("team", "app") == []

    def test_delete_lock_wrong_owner_raises(self) -> None:
        """Non-owner cannot delete lock without force."""
        lock = create_lock("team", "app", "file.bin", "alice")
        with pytest.raises(LfsError, match="Lock owned by"):
            delete_lock("team", "app", lock.lock_id, "bob")

    def test_delete_lock_force(self) -> None:
        """Force allows non-owner to delete lock."""
        lock = create_lock("team", "app", "file.bin", "alice")
        deleted = delete_lock(
            "team", "app", lock.lock_id, "bob", force=True,
        )
        assert deleted.lock_id == lock.lock_id

    def test_delete_nonexistent_lock_raises(self) -> None:
        """Raises LfsError for missing lock."""
        with pytest.raises(LfsError, match="Lock not found"):
            delete_lock("team", "app", "fake-id", "alice")


class TestLfsError:
    """Tests for LfsError exception."""

    def test_default_status(self) -> None:
        """Default status is 400."""
        err = LfsError("bad request")
        assert err.status_code == 400

    def test_custom_status(self) -> None:
        """Custom status code works."""
        err = LfsError("not found", status_code=404)
        assert err.status_code == 404


class TestLfsObject:
    """Tests for LfsObject dataclass."""

    def test_creation(self) -> None:
        """Creates LfsObject with oid and size."""
        obj = LfsObject(oid="abc123", size=1024)
        assert obj.oid == "abc123"
        assert obj.size == 1024
