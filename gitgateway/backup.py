"""Repository backup and restore for GitGateway.

Creates portable, self-contained snapshots of every repository — a
``git bundle`` capturing all refs plus the repository's Git LFS objects —
in a dedicated backups bucket, organized into daily/weekly/monthly tiers.
Supports restoring a snapshot into a brand-new repository (never
overwriting a live one).

Designed to run inside a CodeBuild job (no Lambda timeout limit), but every
function accepts injected boto3 clients/resources so the logic is unit
testable with moto + a real Dulwich repo.
"""

from __future__ import annotations

import json
import logging
import subprocess  # nosec B404 - used only for `git bundle` on trusted paths
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import boto3

from gitgateway import db, storage
from gitgateway.lfs import DEFAULT_LFS_BUCKET, _lfs_s3_key
from gitgateway.models import Group, Repo
from gitgateway.storage import DEFAULT_BUCKET as DEFAULT_REPO_BUCKET

LOG = logging.getLogger(__name__)

DEFAULT_BACKUP_BUCKET = "gitgateway-backups"

TIER_DAILY = "daily"
TIER_WEEKLY = "weekly"
TIER_MONTHLY = "monthly"


class BackupError(Exception):
    """Raised when a backup or restore operation fails."""


@dataclass
class RepoBackupResult:
    """Outcome of archiving a single repository.

    Attributes:
        group: Group name.
        repo: Repository name.
        status: 'ok' or 'error'.
        bundle_bytes: Size of the git bundle in bytes.
        lfs_object_count: Number of LFS objects copied.
        lfs_bytes: Total bytes of copied LFS objects.
        refs: List of ref names captured in the bundle.
        error: Error message when status is 'error'.
    """

    group: str = ""
    repo: str = ""
    status: str = "ok"
    bundle_bytes: int = 0
    lfs_object_count: int = 0
    lfs_bytes: int = 0
    refs: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        """Serialize to a manifest-friendly dict."""
        return {
            "group": self.group,
            "repo": self.repo,
            "status": self.status,
            "bundle_bytes": self.bundle_bytes,
            "lfs_object_count": self.lfs_object_count,
            "lfs_bytes": self.lfs_bytes,
            "refs": self.refs,
            "error": self.error,
        }


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_s3_client(s3_client: object = None) -> object:
    """Return the given S3 client or a default one."""
    if s3_client is None:
        return boto3.client("s3")  # pragma: no cover - real AWS only
    return s3_client


def tiers_for_date(today: date | None = None) -> list[str]:
    """Determine which retention tiers a run on ``today`` writes to.

    Always writes the daily tier; additionally the weekly tier on Sundays
    and the monthly tier on the first of the month.

    Args:
        today: The date of the run (defaults to today, UTC).

    Returns:
        Ordered list of tier names to write.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()  # pragma: no cover
    tiers = [TIER_DAILY]
    if today.weekday() == 6:  # Sunday
        tiers.append(TIER_WEEKLY)
    if today.day == 1:
        tiers.append(TIER_MONTHLY)
    return tiers


def _snapshot_date(today: date | None = None) -> str:
    """Return the snapshot date string (YYYY-MM-DD)."""
    if today is None:
        today = datetime.now(timezone.utc).date()  # pragma: no cover
    return today.isoformat()


def _snapshot_prefix(tier: str, snapshot: str) -> str:
    """Build the S3 key prefix for a tier's snapshot."""
    return f"{tier}/{snapshot}/"


def _list_repo_lfs_objects(
    group: str,
    repo: str,
    lfs_bucket: str,
    s3_client: object,
) -> list[tuple[str, str]]:
    """List a repository's LFS objects.

    Args:
        group: Group name.
        repo: Repository name.
        lfs_bucket: The LFS bucket name.
        s3_client: boto3 S3 client.

    Returns:
        List of (oid, s3_key) tuples for the repo's LFS objects.
    """
    prefix = f"lfs/{group}/{repo}/"
    paginator = s3_client.get_paginator("list_objects_v2")
    results: list[tuple[str, str]] = []
    for page in paginator.paginate(Bucket=lfs_bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            oid = key.rsplit("/", 1)[-1]
            results.append((oid, key))
    return results


def _create_bundle(local_path: Path, bundle_path: Path) -> list[str]:
    """Create a ``git bundle`` of all refs from a bare repo.

    Args:
        local_path: Path to the local bare repository.
        bundle_path: Destination path for the bundle file.

    Returns:
        The list of ref names included in the bundle.

    Raises:
        BackupError: If the repo has no refs or bundling fails.
    """
    from dulwich.repo import Repo as DulwichRepo  # pylint: disable=import-outside-toplevel

    # A repo synced from S3 can be missing the standard empty git
    # directories (S3 does not store empty dirs), which makes git/dulwich
    # refuse to open it. Recreate the skeleton before reading.
    for sub in ("refs", "refs/heads", "refs/tags", "objects/pack",
                "objects/info"):
        (local_path / sub).mkdir(parents=True, exist_ok=True)

    repo = DulwichRepo(str(local_path))
    refs = sorted(
        r.decode() for r in repo.get_refs()
        if not r.startswith(b"refs/remotes/")
    )
    if not refs:
        raise BackupError("Repository has no refs to bundle")

    try:
        subprocess.run(  # nosec B603 B607 - fixed args, trusted local path
            ["git", "bundle", "create", str(bundle_path), "--all"],
            cwd=str(local_path),
            check=True,
            capture_output=True,
            timeout=600,
        )
    except (subprocess.CalledProcessError, FileNotFoundError,
            subprocess.TimeoutExpired) as exc:
        raise BackupError(f"git bundle failed: {exc}") from exc

    return refs


def archive_repo(  # pylint: disable=too-many-locals,too-many-arguments,too-many-positional-arguments
    group: str,
    repo: str,
    tiers: list[str],
    snapshot: str,
    repo_bucket: str = DEFAULT_REPO_BUCKET,
    lfs_bucket: str = DEFAULT_LFS_BUCKET,
    backup_bucket: str = DEFAULT_BACKUP_BUCKET,
    local_base: Path | None = None,
    s3_client: object = None,
) -> RepoBackupResult:
    """Archive a single repository into the given snapshot tiers.

    Syncs the bare repo locally, bundles all refs, copies its LFS objects,
    and uploads the bundle + LFS objects into each tier's snapshot prefix.

    Args:
        group: Group name.
        repo: Repository name.
        tiers: Tier names to write into (e.g. ['daily']).
        snapshot: Snapshot date string (YYYY-MM-DD).
        repo_bucket: Source repos bucket.
        lfs_bucket: Source LFS bucket.
        backup_bucket: Destination backups bucket.
        local_base: Base path for local working copies.
        s3_client: Optional boto3 S3 client.

    Returns:
        A RepoBackupResult (status 'ok' or 'error').
    """
    client = _get_s3_client(s3_client)
    base = Path(local_base) if local_base else Path(tempfile.mkdtemp())
    base.mkdir(parents=True, exist_ok=True)
    result = RepoBackupResult(group=group, repo=repo)

    try:
        local_path = storage.sync_repo_to_local(
            group, repo, bucket=repo_bucket, local_base=base,
            s3_client=client,
        )
        bundle_path = base / f"{repo}.bundle"
        result.refs = _create_bundle(local_path, bundle_path)
        result.bundle_bytes = bundle_path.stat().st_size

        lfs_objects = _list_repo_lfs_objects(
            group, repo, lfs_bucket, client,
        )

        for tier in tiers:
            prefix = _snapshot_prefix(tier, snapshot)
            bundle_key = f"{prefix}{group}/{repo}.bundle"
            client.upload_file(str(bundle_path), backup_bucket, bundle_key)
            for oid, src_key in lfs_objects:
                dst_key = f"{prefix}{group}/{repo}.lfs/{oid}"
                client.copy_object(
                    Bucket=backup_bucket,
                    CopySource={"Bucket": lfs_bucket, "Key": src_key},
                    Key=dst_key,
                )

        # Compute LFS totals once (sizes are identical across tiers).
        lfs_bytes = 0
        for _oid, src_key in lfs_objects:
            head = client.head_object(Bucket=lfs_bucket, Key=src_key)
            lfs_bytes += int(head.get("ContentLength", 0))
        result.lfs_object_count = len(lfs_objects)
        result.lfs_bytes = lfs_bytes
    except Exception as exc:  # pylint: disable=broad-except
        result.status = "error"
        result.error = str(exc)
        LOG.error("Backup failed for %s/%s: %s", group, repo, exc)

    return result


def archive_all(  # pylint: disable=too-many-locals,too-many-arguments,too-many-positional-arguments
    today: date | None = None,
    repo_bucket: str = DEFAULT_REPO_BUCKET,
    lfs_bucket: str = DEFAULT_LFS_BUCKET,
    backup_bucket: str = DEFAULT_BACKUP_BUCKET,
    local_base: Path | None = None,
    s3_client: object = None,
    dynamodb_resource: object = None,
) -> dict:
    """Archive every registered repository and write a manifest.

    Args:
        today: Run date (defaults to today, UTC).
        repo_bucket: Source repos bucket.
        lfs_bucket: Source LFS bucket.
        backup_bucket: Destination backups bucket.
        local_base: Base path for local working copies.
        s3_client: Optional boto3 S3 client.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The manifest dict that was written to each tier.
    """
    client = _get_s3_client(s3_client)
    run_date = today or datetime.now(timezone.utc).date()  # pragma: no cover
    tiers = tiers_for_date(run_date)
    snapshot = _snapshot_date(run_date)
    started = _now_iso()

    results: list[RepoBackupResult] = []
    for group in db.list_groups(dynamodb_resource):
        for repo in db.list_repos(group.name, dynamodb_resource):
            results.append(archive_repo(
                repo.group, repo.name, tiers, snapshot,
                repo_bucket=repo_bucket, lfs_bucket=lfs_bucket,
                backup_bucket=backup_bucket, local_base=local_base,
                s3_client=client,
            ))

    ok = [r for r in results if r.status == "ok"]
    failed = [r for r in results if r.status == "error"]

    manifest = {
        "snapshot": snapshot,
        "tiers": tiers,
        "started_at": started,
        "finished_at": _now_iso(),
        "repo_count": len(results),
        "ok_count": len(ok),
        "failed_count": len(failed),
        "repos": [r.to_dict() for r in results],
    }

    body = json.dumps(manifest, indent=2).encode()
    for tier in tiers:
        client.put_object(
            Bucket=backup_bucket,
            Key=f"{_snapshot_prefix(tier, snapshot)}manifest.json",
            Body=body,
            ContentType="application/json",
        )

    return manifest


def list_snapshots(
    backup_bucket: str = DEFAULT_BACKUP_BUCKET,
    s3_client: object = None,
) -> list[dict]:
    """List available snapshots across all tiers.

    Args:
        backup_bucket: The backups bucket.
        s3_client: Optional boto3 S3 client.

    Returns:
        List of {tier, snapshot} dicts, newest snapshot first.
    """
    client = _get_s3_client(s3_client)
    snapshots: list[dict] = []
    for tier in (TIER_DAILY, TIER_WEEKLY, TIER_MONTHLY):
        resp = client.list_objects_v2(
            Bucket=backup_bucket, Prefix=f"{tier}/", Delimiter="/",
        )
        for cp in resp.get("CommonPrefixes", []):
            # cp["Prefix"] looks like "daily/2026-09-07/"
            snap = cp["Prefix"].split("/")[1]
            snapshots.append({"tier": tier, "snapshot": snap})
    snapshots.sort(key=lambda s: s["snapshot"], reverse=True)
    return snapshots


def read_manifest(
    tier: str,
    snapshot: str,
    backup_bucket: str = DEFAULT_BACKUP_BUCKET,
    s3_client: object = None,
) -> dict:
    """Read the manifest for a snapshot.

    Args:
        tier: Tier name.
        snapshot: Snapshot date string.
        backup_bucket: The backups bucket.
        s3_client: Optional boto3 S3 client.

    Returns:
        The parsed manifest dict.

    Raises:
        BackupError: If the manifest cannot be read.
    """
    client = _get_s3_client(s3_client)
    key = f"{_snapshot_prefix(tier, snapshot)}manifest.json"
    try:
        resp = client.get_object(Bucket=backup_bucket, Key=key)
        return json.loads(resp["Body"].read())
    except Exception as exc:  # pylint: disable=broad-except
        raise BackupError(
            f"Manifest not found for {tier}/{snapshot}: {exc}",
        ) from exc


def _default_restore_name(repo: str) -> str:
    """Build the default restore target name for a repo."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return f"{repo}-restore-{stamp}"


def restore_repo(  # pylint: disable=too-many-locals,too-many-arguments,too-many-positional-arguments
    tier: str,
    snapshot: str,
    group: str,
    repo: str,
    target_name: str = "",
    repo_bucket: str = DEFAULT_REPO_BUCKET,
    lfs_bucket: str = DEFAULT_LFS_BUCKET,
    backup_bucket: str = DEFAULT_BACKUP_BUCKET,
    local_base: Path | None = None,
    s3_client: object = None,
    dynamodb_resource: object = None,
) -> str:
    """Restore one repository from a snapshot into a NEW repository.

    Never overwrites the source repo: the restored repo is created under a
    new name (default ``<repo>-restore-<YYYYMMDD-HHMM>``) in the same group.

    Args:
        tier: Snapshot tier.
        snapshot: Snapshot date string.
        group: Source group name.
        repo: Source repository name.
        target_name: Optional explicit new repo name.
        repo_bucket: Destination repos bucket.
        lfs_bucket: Destination LFS bucket.
        backup_bucket: Source backups bucket.
        local_base: Base path for local working copies.
        s3_client: Optional boto3 S3 client.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        The new repository name that was created.

    Raises:
        BackupError: If the bundle is missing or restore fails.
    """
    from dulwich.repo import Repo as DulwichRepo  # pylint: disable=import-outside-toplevel

    client = _get_s3_client(s3_client)
    new_name = target_name or _default_restore_name(repo)
    base = Path(local_base) if local_base else Path(tempfile.mkdtemp())
    base.mkdir(parents=True, exist_ok=True)
    prefix = _snapshot_prefix(tier, snapshot)
    bundle_key = f"{prefix}{group}/{repo}.bundle"

    bundle_path = base / f"{repo}.bundle"
    try:
        client.download_file(backup_bucket, bundle_key, str(bundle_path))
    except Exception as exc:  # pylint: disable=broad-except
        raise BackupError(
            f"Backup bundle not found: {bundle_key}: {exc}",
        ) from exc

    # Clone the bundle into a fresh bare repo.
    dest_path = base / f"{new_name}.git"
    try:
        DulwichRepo.init_bare(str(dest_path), mkdir=True)
        subprocess.run(  # nosec B603 B607 - fixed args, trusted local paths
            ["git", "bundle", "unbundle", str(bundle_path)],
            cwd=str(dest_path), check=True, capture_output=True, timeout=600,
        )
        src_repo = DulwichRepo(str(dest_path))
        # Copy refs from the bundle into the new repo's ref store.
        subprocess.run(  # nosec B603 B607 - fixed args, trusted local paths
            ["git", "fetch", str(bundle_path), "+refs/*:refs/*"],
            cwd=str(dest_path), check=True, capture_output=True, timeout=600,
        )
        del src_repo
    except (subprocess.CalledProcessError, FileNotFoundError,
            subprocess.TimeoutExpired) as exc:
        raise BackupError(f"Restore unbundle failed: {exc}") from exc

    # Ensure the group exists, then register + upload the new repo.
    _ensure_group(group, dynamodb_resource)
    db.create_repo(
        Repo(
            group=group, name=new_name,
            description=f"Restored from {tier}/{snapshot} of {group}/{repo}",
            created_by="gitgateway-backup",
        ),
        dynamodb_resource,
    )
    storage.sync_repo_to_s3(
        dest_path, group, new_name, bucket=repo_bucket, s3_client=client,
    )

    # Restore LFS objects, if any, under the new repo's LFS prefix.
    _restore_lfs_objects(
        prefix, group, repo, new_name, backup_bucket, lfs_bucket, client,
    )

    LOG.info(
        "Restored %s/%s from %s/%s into %s/%s",
        group, repo, tier, snapshot, group, new_name,
    )
    return new_name


def _ensure_group(group: str, dynamodb_resource: object = None) -> None:
    """Create the group if it does not already exist."""
    try:
        db.get_group(group, dynamodb_resource)
    except db.NotFoundError:
        db.create_group(
            Group(name=group, description="", created_by="gitgateway-backup"),
            dynamodb_resource,
        )


def _restore_lfs_objects(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    prefix: str,
    group: str,
    repo: str,
    new_name: str,
    backup_bucket: str,
    lfs_bucket: str,
    s3_client: object,
) -> int:
    """Copy a snapshot's LFS objects to the restored repo's LFS prefix.

    Args:
        prefix: The snapshot prefix (e.g. 'daily/2026-09-07/').
        group: Group name.
        repo: Source repository name.
        new_name: Restored repository name.
        backup_bucket: Source backups bucket.
        lfs_bucket: Destination LFS bucket.
        s3_client: boto3 S3 client.

    Returns:
        Number of LFS objects restored.
    """
    lfs_prefix = f"{prefix}{group}/{repo}.lfs/"
    paginator = s3_client.get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=backup_bucket, Prefix=lfs_prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            oid = key.rsplit("/", 1)[-1]
            s3_client.copy_object(
                Bucket=lfs_bucket,
                CopySource={"Bucket": backup_bucket, "Key": key},
                Key=_lfs_s3_key(group, new_name, oid),
            )
            count += 1
    return count


def restore_repos(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    tier: str,
    snapshot: str,
    repos: list[dict] | str,
    repo_bucket: str = DEFAULT_REPO_BUCKET,
    lfs_bucket: str = DEFAULT_LFS_BUCKET,
    backup_bucket: str = DEFAULT_BACKUP_BUCKET,
    local_base: Path | None = None,
    s3_client: object = None,
    dynamodb_resource: object = None,
) -> list[dict]:
    """Restore one, several, or all repos from a snapshot.

    Args:
        tier: Snapshot tier.
        snapshot: Snapshot date string.
        repos: Either the string ``"all"`` to restore every repo in the
            snapshot, or a list of {group, repo, target_name?} dicts.
        repo_bucket: Destination repos bucket.
        lfs_bucket: Destination LFS bucket.
        backup_bucket: Source backups bucket.
        local_base: Base path for local working copies.
        s3_client: Optional boto3 S3 client.
        dynamodb_resource: Optional boto3 DynamoDB resource.

    Returns:
        List of per-repo result dicts (group, repo, status, new_name/error).
    """
    client = _get_s3_client(s3_client)

    if repos == "all":
        manifest = read_manifest(
            tier, snapshot, backup_bucket=backup_bucket, s3_client=client,
        )
        targets = [
            {"group": r["group"], "repo": r["repo"]}
            for r in manifest.get("repos", [])
            if r.get("status") == "ok"
        ]
    else:
        targets = list(repos)

    results: list[dict] = []
    for target in targets:
        group = target["group"]
        repo = target["repo"]
        entry: dict = {"group": group, "repo": repo}
        try:
            new_name = restore_repo(
                tier, snapshot, group, repo,
                target_name=target.get("target_name", ""),
                repo_bucket=repo_bucket, lfs_bucket=lfs_bucket,
                backup_bucket=backup_bucket, local_base=local_base,
                s3_client=client, dynamodb_resource=dynamodb_resource,
            )
            entry["status"] = "ok"
            entry["new_name"] = new_name
        except Exception as exc:  # pylint: disable=broad-except
            entry["status"] = "error"
            entry["error"] = str(exc)
            LOG.error("Restore failed for %s/%s: %s", group, repo, exc)
        results.append(entry)

    return results
