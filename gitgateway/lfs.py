"""Git LFS (Large File Storage) support for GitGateway.

Implements the Git LFS Batch API for upload/download operations using
S3 presigned URLs, and the LFS lock API for file locking.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field

import boto3

LOG = logging.getLogger(__name__)

DEFAULT_LFS_BUCKET = "gitgateway-lfs"
PRESIGNED_URL_EXPIRY = 3600  # 1 hour


class LfsError(Exception):
    """Raised when an LFS operation fails."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        """Initialize with message and HTTP status code.

        Args:
            message: Error description.
            status_code: HTTP status code to return.
        """
        super().__init__(message)
        self.status_code = status_code


@dataclass
class LfsObject:
    """Represents an LFS object in a batch request/response.

    Attributes:
        oid: The object identifier (SHA-256 hash).
        size: The object size in bytes.
    """

    oid: str
    size: int


@dataclass
class LfsAction:
    """Represents an LFS action (upload/download URL).

    Attributes:
        href: The presigned URL for the action.
        header: Optional headers to include with the request.
        expires_in: Seconds until the URL expires.
    """

    href: str
    header: dict[str, str] = field(default_factory=dict)
    expires_in: int = PRESIGNED_URL_EXPIRY

    def to_dict(self) -> dict:
        """Serialize to LFS API response format.

        Returns:
            Dictionary matching LFS Batch API spec.
        """
        result: dict = {"href": self.href, "expires_in": self.expires_in}
        if self.header:
            result["header"] = self.header
        return result


@dataclass
class LfsObjectResponse:
    """Response for a single LFS object in a batch response.

    Attributes:
        oid: The object identifier.
        size: The object size.
        authenticated: Whether the user is pre-authenticated.
        actions: Dictionary of action name to LfsAction.
        error: Optional error for this object.
    """

    oid: str
    size: int
    authenticated: bool = True
    actions: dict[str, LfsAction] = field(default_factory=dict)
    error: dict | None = None

    def to_dict(self) -> dict:
        """Serialize to LFS API response format.

        Returns:
            Dictionary matching LFS Batch API spec.
        """
        result: dict = {
            "oid": self.oid,
            "size": self.size,
            "authenticated": self.authenticated,
        }
        if self.actions:
            result["actions"] = {
                name: action.to_dict()
                for name, action in self.actions.items()
            }
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class LfsLock:
    """Represents an LFS file lock.

    Attributes:
        lock_id: Unique lock identifier.
        path: The locked file path.
        owner: Username of the lock owner.
        locked_at: ISO timestamp when the lock was created.
    """

    lock_id: str
    path: str
    owner: str
    locked_at: str

    def to_dict(self) -> dict:
        """Serialize to LFS lock API response format.

        Returns:
            Dictionary matching LFS Lock API spec.
        """
        return {
            "id": self.lock_id,
            "path": self.path,
            "owner": {"name": self.owner},
            "locked_at": self.locked_at,
        }


def _lfs_s3_key(group: str, repo: str, oid: str) -> str:
    """Build the S3 key for an LFS object.

    Uses content-addressable storage with 2-level fan-out.

    Args:
        group: The group name.
        repo: The repository name.
        oid: The object identifier (SHA-256).

    Returns:
        S3 key string.
    """
    return f"lfs/{group}/{repo}/{oid[0:2]}/{oid[2:4]}/{oid}"


def _get_s3_client(s3_client: object = None) -> object:
    """Get an S3 client configured for SigV4 presigned URLs.

    The LFS bucket uses KMS encryption, which requires AWS Signature
    Version 4 for presigned URLs. Force SigV4 and virtual-host
    addressing so presigned PUT/GET URLs are accepted by S3.

    Args:
        s3_client: Optional boto3 S3 client for testing.

    Returns:
        A boto3 S3 client.
    """
    if s3_client is None:
        from botocore.config import Config  # pylint: disable=import-outside-toplevel
        return boto3.client(
            "s3",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "virtual"},
            ),
        )
    return s3_client


def handle_batch_request(  # pylint: disable=too-many-positional-arguments
    group: str,
    repo: str,
    operation: str,
    objects: list[dict],
    bucket: str = DEFAULT_LFS_BUCKET,
    s3_client: object = None,
) -> dict:
    """Handle an LFS Batch API request.

    Args:
        group: The group name.
        repo: The repository name.
        operation: Either 'upload' or 'download'.
        objects: List of object dicts with 'oid' and 'size' keys.
        bucket: S3 bucket for LFS objects.
        s3_client: Optional boto3 S3 client for testing.

    Returns:
        LFS Batch API response dictionary.

    Raises:
        LfsError: If the operation is invalid.
    """
    if operation not in ("upload", "download"):
        raise LfsError(f"Invalid operation: {operation}")

    client = _get_s3_client(s3_client)
    response_objects = []

    for obj_data in objects:
        oid = obj_data.get("oid", "")
        size = obj_data.get("size", 0)

        if not oid:
            response_objects.append(
                LfsObjectResponse(
                    oid=oid,
                    size=size,
                    error={"code": 422, "message": "Missing oid"},
                ).to_dict(),
            )
            continue

        s3_key = _lfs_s3_key(group, repo, oid)

        if operation == "upload":
            obj_response = _build_upload_response(
                oid, size, s3_key, bucket, client,
            )
        else:
            obj_response = _build_download_response(
                oid, size, s3_key, bucket, client,
            )

        response_objects.append(obj_response.to_dict())

    return {
        "transfer": "basic",
        "objects": response_objects,
    }


def _build_upload_response(
    oid: str,
    size: int,
    s3_key: str,
    bucket: str,
    client: object,
) -> LfsObjectResponse:
    """Build upload response with presigned PUT URL.

    Args:
        oid: Object identifier.
        size: Object size.
        s3_key: S3 key for the object.
        bucket: S3 bucket name.
        client: boto3 S3 client.

    Returns:
        LfsObjectResponse with upload action.
    """
    presigned_url = client.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": s3_key},
        ExpiresIn=PRESIGNED_URL_EXPIRY,
    )

    return LfsObjectResponse(
        oid=oid,
        size=size,
        actions={
            "upload": LfsAction(href=presigned_url),
        },
    )


def _build_download_response(
    oid: str,
    size: int,
    s3_key: str,
    bucket: str,
    client: object,
) -> LfsObjectResponse:
    """Build download response with presigned GET URL.

    Checks if the object exists before generating the URL.

    Args:
        oid: Object identifier.
        size: Object size.
        s3_key: S3 key for the object.
        bucket: S3 bucket name.
        client: boto3 S3 client.

    Returns:
        LfsObjectResponse with download action or error.
    """
    # Check if object exists
    try:
        client.head_object(Bucket=bucket, Key=s3_key)
    except client.exceptions.ClientError:
        return LfsObjectResponse(
            oid=oid,
            size=size,
            error={"code": 404, "message": "Object not found"},
        )

    presigned_url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": s3_key},
        ExpiresIn=PRESIGNED_URL_EXPIRY,
    )

    return LfsObjectResponse(
        oid=oid,
        size=size,
        actions={
            "download": LfsAction(href=presigned_url),
        },
    )


# --- Lock API ---

# In-memory lock store (in production, this would be DynamoDB)
_LOCKS: dict[str, dict[str, LfsLock]] = {}


def create_lock(
    group: str,
    repo: str,
    path: str,
    owner: str,
) -> LfsLock:
    """Create a file lock.

    Args:
        group: The group name.
        repo: The repository name.
        path: The file path to lock.
        owner: Username of the lock creator.

    Returns:
        The created LfsLock.

    Raises:
        LfsError: If the file is already locked.
    """
    repo_key = f"{group}/{repo}"
    if repo_key not in _LOCKS:
        _LOCKS[repo_key] = {}

    # Check if already locked
    for lock in _LOCKS[repo_key].values():
        if lock.path == path:
            raise LfsError(
                f"File already locked by {lock.owner}",
                status_code=409,
            )

    lock = LfsLock(
        lock_id=str(uuid.uuid4()),
        path=path,
        owner=owner,
        locked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    _LOCKS[repo_key][lock.lock_id] = lock
    LOG.info("Lock created: %s on %s/%s:%s", lock.lock_id, group, repo, path)
    return lock


def list_locks(
    group: str,
    repo: str,
    path: str | None = None,
) -> list[LfsLock]:
    """List locks for a repository.

    Args:
        group: The group name.
        repo: The repository name.
        path: Optional file path filter.

    Returns:
        List of LfsLock objects.
    """
    repo_key = f"{group}/{repo}"
    locks = list(_LOCKS.get(repo_key, {}).values())

    if path:
        locks = [lock for lock in locks if lock.path == path]

    return locks


def verify_locks(
    group: str,
    repo: str,
    owner: str,
) -> tuple[list[LfsLock], list[LfsLock]]:
    """Verify which locks belong to the current user.

    Args:
        group: The group name.
        repo: The repository name.
        owner: Username to check ownership.

    Returns:
        Tuple of (ours, theirs) lock lists.
    """
    repo_key = f"{group}/{repo}"
    all_locks = list(_LOCKS.get(repo_key, {}).values())

    ours = [lock for lock in all_locks if lock.owner == owner]
    theirs = [lock for lock in all_locks if lock.owner != owner]

    return ours, theirs


def delete_lock(
    group: str,
    repo: str,
    lock_id: str,
    owner: str,
    force: bool = False,
) -> LfsLock:
    """Delete (unlock) a file lock.

    Args:
        group: The group name.
        repo: The repository name.
        lock_id: The lock ID to delete.
        owner: Username requesting the unlock.
        force: If True, allow unlocking another user's lock.

    Returns:
        The deleted LfsLock.

    Raises:
        LfsError: If lock not found or unauthorized.
    """
    repo_key = f"{group}/{repo}"
    locks = _LOCKS.get(repo_key, {})

    if lock_id not in locks:
        raise LfsError("Lock not found", status_code=404)

    lock = locks[lock_id]
    if lock.owner != owner and not force:
        raise LfsError(
            f"Lock owned by {lock.owner}, use force to override",
            status_code=403,
        )

    del _LOCKS[repo_key][lock_id]
    LOG.info("Lock deleted: %s on %s/%s", lock_id, group, repo)
    return lock


def clear_locks() -> None:
    """Clear all locks. Used in testing."""
    _LOCKS.clear()
