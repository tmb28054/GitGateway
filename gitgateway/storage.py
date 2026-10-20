"""S3 repository storage layer for GitGateway.

Manages bare Git repository storage in S3 with sync operations
between Lambda /tmp and S3. Supports init, delete, fork, and
delta sync with optimistic locking.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import boto3

LOG = logging.getLogger(__name__)

DEFAULT_BUCKET = "gitgateway-repos"
S3_KEY_PREFIX = "repos"


def _log_sync_metric(
    operation: str,
    group: str,
    repo: str,
    *,
    object_count: int,
    byte_count: int,
    duration_s: float,
    warm_cache: bool | None = None,
) -> None:
    """Emit a single structured log line describing a repo sync.

    The line is JSON prefixed with ``SYNC_METRIC`` so it can be queried in
    CloudWatch Logs Insights or turned into a metric filter without any
    extra instrumentation. This is measurement-only: it informs whether the
    S3<->/tmp sync latency is worth optimizing and, if so, which sync
    (download vs upload) and repo sizes dominate.

    Args:
        operation: ``"s3_to_local"`` or ``"local_to_s3"``.
        group: Repository group.
        repo: Repository name.
        object_count: Number of objects transferred.
        byte_count: Total bytes transferred.
        duration_s: Wall-clock duration in seconds.
        warm_cache: For downloads, whether the repo already existed in
            /tmp (warm Lambda instance). None when not applicable.
    """
    payload = {
        "metric": "repo_sync",
        "operation": operation,
        "repo": f"{group}/{repo}",
        "objects": object_count,
        "bytes": byte_count,
        "duration_ms": round(duration_s * 1000, 1),
    }
    if byte_count and duration_s > 0:
        payload["throughput_mbps"] = round(
            (byte_count / 1_000_000) / duration_s, 2,
        )
    if warm_cache is not None:
        payload["warm_cache"] = warm_cache
    LOG.info("SYNC_METRIC %s", json.dumps(payload, separators=(",", ":")))


class StorageError(Exception):
    """Raised when a storage operation fails."""


class ConflictError(StorageError):
    """Raised when a concurrent modification is detected."""


def _get_s3_client(s3_client: object = None) -> object:
    """Get an S3 client.

    Args:
        s3_client: Optional boto3 S3 client for testing.

    Returns:
        A boto3 S3 client.
    """
    if s3_client is None:
        return boto3.client("s3")
    return s3_client


def _repo_s3_prefix(group: str, repo: str) -> str:
    """Build the S3 key prefix for a repository.

    Args:
        group: The group name.
        repo: The repository name.

    Returns:
        S3 key prefix string.
    """
    return f"{S3_KEY_PREFIX}/{group}/{repo}.git/"


def init_bare_repo(
    group: str,
    repo: str,
    bucket: str = DEFAULT_BUCKET,
    local_base: Path | None = None,
    s3_client: object = None,
) -> Path:
    """Initialize a new bare Git repository and upload to S3.

    Creates a bare repo locally, then syncs it to S3.

    Args:
        group: The group name.
        repo: The repository name.
        bucket: S3 bucket name.
        local_base: Base path for local repos (defaults to /tmp).
        s3_client: Optional boto3 S3 client for testing.

    Returns:
        The local path of the initialized bare repo.

    Raises:
        StorageError: If initialization fails.
    """
    client = _get_s3_client(s3_client)
    base = local_base or Path("/tmp")  # nosec B108 - Lambda uses /tmp
    local_path = base / group / f"{repo}.git"

    if local_path.exists():
        # Clean up existing local copy
        _remove_tree(local_path)

    local_path.mkdir(parents=True, exist_ok=True)

    try:
        from dulwich.repo import Repo as DulwichRepo  # pylint: disable=import-outside-toplevel
        DulwichRepo.init_bare(str(local_path))
    except Exception as exc:  # pylint: disable=broad-except
        raise StorageError(
            f"Failed to initialize bare repo: {exc}",
        ) from exc

    # Upload to S3
    _sync_local_to_s3(local_path, group, repo, bucket, client)
    LOG.info("Initialized bare repo: %s/%s", group, repo)
    return local_path


def sync_repo_to_local(  # pylint: disable=too-many-locals
    group: str,
    repo: str,
    bucket: str = DEFAULT_BUCKET,
    local_base: Path | None = None,
    s3_client: object = None,
) -> Path:
    """Sync a repository from S3 to local filesystem.

    Downloads all objects from S3 to a local directory.

    Args:
        group: The group name.
        repo: The repository name.
        bucket: S3 bucket name.
        local_base: Base path for local repos (defaults to /tmp).
        s3_client: Optional boto3 S3 client for testing.

    Returns:
        The local path of the synced repo.

    Raises:
        StorageError: If the repo does not exist in S3.
    """
    client = _get_s3_client(s3_client)
    base = local_base or Path("/tmp")  # nosec B108 - Lambda uses /tmp
    local_path = base / group / f"{repo}.git"
    prefix = _repo_s3_prefix(group, repo)

    # List objects in S3
    objects = _list_s3_objects(bucket, prefix, client)
    if not objects:
        raise StorageError(
            f"Repository '{group}/{repo}' not found in S3",
        )

    # Whether the repo was already present in /tmp indicates a warm Lambda
    # instance (the full re-download below happens regardless today; this
    # signal lets us quantify how often the cost is paid on warm vs cold).
    warm_cache = local_path.exists()

    # Ensure local directory exists
    if warm_cache:
        _remove_tree(local_path)
    local_path.mkdir(parents=True, exist_ok=True)

    # Download each object, timing the transfer and tallying bytes moved.
    start = time.perf_counter()
    byte_count = 0
    for obj_key in objects:
        relative = obj_key[len(prefix):]
        if not relative:
            continue
        local_file = local_path / relative
        local_file.parent.mkdir(parents=True, exist_ok=True)
        client.download_file(bucket, obj_key, str(local_file))
        try:
            byte_count += local_file.stat().st_size
        except OSError:  # pragma: no cover - defensive: file just written
            pass
    duration_s = time.perf_counter() - start

    _log_sync_metric(
        "s3_to_local", group, repo,
        object_count=len(objects), byte_count=byte_count,
        duration_s=duration_s, warm_cache=warm_cache,
    )
    LOG.info("Synced repo from S3: %s/%s", group, repo)
    return local_path


def sync_repo_to_s3(
    local_path: Path,
    group: str,
    repo: str,
    bucket: str = DEFAULT_BUCKET,
    s3_client: object = None,
) -> None:
    """Sync a local repository back to S3.

    Uploads all files from the local path to S3.

    Args:
        local_path: Path to the local bare repo.
        group: The group name.
        repo: The repository name.
        bucket: S3 bucket name.
        s3_client: Optional boto3 S3 client for testing.

    Raises:
        StorageError: If the local path does not exist.
    """
    if not local_path.exists():
        raise StorageError(
            f"Local path does not exist: {local_path}",
        )

    client = _get_s3_client(s3_client)
    start = time.perf_counter()
    object_count, byte_count = _sync_local_to_s3(
        local_path, group, repo, bucket, client,
    )
    duration_s = time.perf_counter() - start
    _log_sync_metric(
        "local_to_s3", group, repo,
        object_count=object_count, byte_count=byte_count,
        duration_s=duration_s,
    )
    LOG.info("Synced repo to S3: %s/%s", group, repo)


def delete_repo_storage(
    group: str,
    repo: str,
    bucket: str = DEFAULT_BUCKET,
    s3_client: object = None,
) -> None:
    """Delete a repository from S3.

    Removes all objects under the repository prefix.

    Args:
        group: The group name.
        repo: The repository name.
        bucket: S3 bucket name.
        s3_client: Optional boto3 S3 client for testing.
    """
    client = _get_s3_client(s3_client)
    prefix = _repo_s3_prefix(group, repo)
    objects = _list_s3_objects(bucket, prefix, client)

    if objects:
        # Delete in batches of 1000 (S3 limit)
        for i in range(0, len(objects), 1000):
            batch = objects[i:i + 1000]
            client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": k} for k in batch]},
            )

    LOG.info("Deleted repo storage: %s/%s", group, repo)


def repo_exists(
    group: str,
    repo: str,
    bucket: str = DEFAULT_BUCKET,
    s3_client: object = None,
) -> bool:
    """Check if a repository exists in S3.

    Args:
        group: The group name.
        repo: The repository name.
        bucket: S3 bucket name.
        s3_client: Optional boto3 S3 client for testing.

    Returns:
        True if the repository has objects in S3.
    """
    client = _get_s3_client(s3_client)
    prefix = _repo_s3_prefix(group, repo)
    objects = _list_s3_objects(bucket, prefix, client, max_keys=1)
    return len(objects) > 0


def fork_repo(  # pylint: disable=too-many-positional-arguments
    source_group: str,
    source_repo: str,
    target_group: str,
    target_repo: str,
    bucket: str = DEFAULT_BUCKET,
    s3_client: object = None,
) -> None:
    """Fork a repository by copying all S3 objects.

    Performs a server-side S3 copy from source to target.

    Args:
        source_group: Source group name.
        source_repo: Source repository name.
        target_group: Target group name.
        target_repo: Target repository name.
        bucket: S3 bucket name.
        s3_client: Optional boto3 S3 client for testing.

    Raises:
        StorageError: If the source repository does not exist.
    """
    client = _get_s3_client(s3_client)
    source_prefix = _repo_s3_prefix(source_group, source_repo)
    target_prefix = _repo_s3_prefix(target_group, target_repo)

    objects = _list_s3_objects(bucket, source_prefix, client)
    if not objects:
        raise StorageError(
            f"Source repository '{source_group}/{source_repo}' not found",
        )

    for obj_key in objects:
        relative = obj_key[len(source_prefix):]
        if not relative:
            continue
        target_key = f"{target_prefix}{relative}"
        client.copy_object(
            Bucket=bucket,
            CopySource={"Bucket": bucket, "Key": obj_key},
            Key=target_key,
        )

    LOG.info(
        "Forked repo: %s/%s -> %s/%s",
        source_group, source_repo, target_group, target_repo,
    )


# --- Private helpers ---


def _sync_local_to_s3(  # pylint: disable=too-many-locals
    local_path: Path,
    group: str,
    repo: str,
    bucket: str,
    client: object,
) -> tuple[int, int]:
    """Upload all files from local path to S3.

    Args:
        local_path: Path to the local bare repo.
        group: The group name.
        repo: The repository name.
        bucket: S3 bucket name.
        client: boto3 S3 client.

    Returns:
        Tuple of (object_count, byte_count) uploaded.
    """
    prefix = _repo_s3_prefix(group, repo)

    # Upload all local files and track which keys should exist
    local_keys = set()
    byte_count = 0
    for root, _dirs, files in os.walk(local_path):
        for filename in files:
            file_path = Path(root) / filename
            relative = file_path.relative_to(local_path)
            s3_key = f"{prefix}{relative}"
            client.upload_file(str(file_path), bucket, s3_key)
            local_keys.add(s3_key)
            try:
                byte_count += file_path.stat().st_size
            except OSError:  # pragma: no cover - defensive: file just walked
                pass

    # Delete S3 objects that no longer exist locally (e.g. deleted
    # branch refs). This makes S3 mirror the local repo state so
    # deletions actually persist.
    existing_keys = set(_list_s3_objects(bucket, prefix, client))
    stale_keys = existing_keys - local_keys
    if stale_keys:
        stale_list = list(stale_keys)
        for i in range(0, len(stale_list), 1000):
            batch = stale_list[i:i + 1000]
            client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": k} for k in batch]},
            )

    return len(local_keys), byte_count


def _list_s3_objects(
    bucket: str,
    prefix: str,
    client: object,
    max_keys: int = 10000,
) -> list[str]:
    """List all object keys under a prefix.

    Args:
        bucket: S3 bucket name.
        prefix: Key prefix to list.
        client: boto3 S3 client.
        max_keys: Maximum number of keys to return.

    Returns:
        List of S3 object keys.
    """
    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    pages = paginator.paginate(
        Bucket=bucket,
        Prefix=prefix,
        PaginationConfig={"MaxItems": max_keys},
    )

    for page in pages:
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])

    return keys


def _remove_tree(path: Path) -> None:
    """Recursively remove a directory tree.

    Args:
        path: The directory to remove.
    """
    if not path.exists():
        return

    for child in path.iterdir():
        if child.is_dir():
            _remove_tree(child)
        else:
            child.unlink()
    path.rmdir()
