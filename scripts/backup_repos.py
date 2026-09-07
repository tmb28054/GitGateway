#!/usr/bin/env python3
"""CodeBuild entry point for GitGateway Tier-2 backups and restore.

This script runs inside a CodeBuild project (see ``buildspec_backup.yaml``)
and drives the :mod:`gitgateway.backup` module. It supports two modes,
selected by the ``BACKUP_MODE`` environment variable:

``archive``
    Nightly job. Bundles every registered repository (``git bundle --all``),
    copies its Git LFS objects, and writes a per-snapshot manifest into the
    tiered layout (``daily/``, ``weekly/``, ``monthly/``) of the backup
    bucket. Tier selection is driven by the run date.

``restore``
    On-demand job triggered from the admin console. Restores one repo, a
    list of repos, or ``all`` repos from a chosen snapshot into brand-new
    repository names (never overwriting the source).

On any failure the script publishes an alert to the SNS topic named in
``ALERTS_TOPIC_ARN`` (when set) and exits non-zero so CodeBuild marks the
build FAILED.

Environment variables:
    BACKUP_MODE:        ``archive`` (default) or ``restore``.
    REPO_BUCKET:        Source repositories bucket.
    LFS_BUCKET:         Source Git LFS bucket.
    BACKUP_BUCKET:      Destination backups bucket.
    ALERTS_TOPIC_ARN:   Optional SNS topic ARN for failure alerts.
    AWS_REGION:         Standard AWS region (provided by CodeBuild).

    Restore-only:
    RESTORE_TIER:       Tier of the snapshot to restore from.
    RESTORE_SNAPSHOT:   Snapshot id (e.g. ``2026-09-09``).
    RESTORE_REPOS:      JSON: ``"all"`` or a list of
                        ``{"group": ..., "repo": ..., "target_name": ...}``.

Usage (normally invoked by CodeBuild, but runnable locally):
    BACKUP_MODE=archive python scripts/backup_repos.py
"""

from __future__ import annotations

import json
import logging
import os
import sys

import boto3

from gitgateway import backup

LOG = logging.getLogger("backup_repos")


def _publish_alert(topic_arn: str, subject: str, message: str) -> None:
    """Publish a failure alert to SNS (best effort).

    Args:
        topic_arn: The SNS topic ARN. If empty, nothing is published.
        subject: The alert subject line.
        message: The alert body.
    """
    if not topic_arn:
        LOG.warning("No ALERTS_TOPIC_ARN set; skipping SNS alert")
        return
    try:
        boto3.client("sns").publish(
            TopicArn=topic_arn,
            Subject=subject[:100],
            Message=message,
        )
        LOG.info("Published alert to %s", topic_arn)
    except Exception as exc:  # pylint: disable=broad-except
        # An alert failure must not mask the original error.
        LOG.error("Failed to publish SNS alert: %s", exc)


def _run_archive() -> dict:
    """Run the nightly archive of every registered repository.

    Returns:
        The manifest dict written for the run.
    """
    manifest = backup.archive_all(
        repo_bucket=os.environ.get("REPO_BUCKET", backup.DEFAULT_REPO_BUCKET),
        lfs_bucket=os.environ.get("LFS_BUCKET", backup.DEFAULT_LFS_BUCKET),
        backup_bucket=os.environ.get(
            "BACKUP_BUCKET", backup.DEFAULT_BACKUP_BUCKET,
        ),
    )
    repos = manifest.get("repos", [])
    failed = [r for r in repos if r.get("status") == "error"]
    empty = [r for r in repos if r.get("status") == "empty"]
    LOG.info(
        "Archive complete: %d repos, %d empty (skipped), %d failed, tiers=%s",
        len(repos), len(empty), len(failed), manifest.get("tiers", []),
    )
    if failed:
        names = ", ".join(f"{r['group']}/{r['repo']}" for r in failed)
        raise backup.BackupError(f"Archive failed for: {names}")
    return manifest


def _parse_restore_repos(raw: str) -> object:
    """Parse the RESTORE_REPOS env value into the restore_repos argument.

    Args:
        raw: The raw JSON string (``"all"`` or a list of dicts).

    Returns:
        Either the string ``"all"`` or a list of repo spec dicts.

    Raises:
        backup.BackupError: If the value is missing or not valid JSON.
    """
    if not raw:
        raise backup.BackupError("RESTORE_REPOS is required for restore mode")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise backup.BackupError(f"Invalid RESTORE_REPOS JSON: {exc}") from exc


def _run_restore() -> list[dict]:
    """Run an on-demand restore from a chosen snapshot.

    Returns:
        A list of per-repo restore result dicts.

    Raises:
        backup.BackupError: On invalid inputs or if any restore fails.
    """
    tier = os.environ.get("RESTORE_TIER", "")
    snapshot = os.environ.get("RESTORE_SNAPSHOT", "")
    if not tier or not snapshot:
        raise backup.BackupError(
            "RESTORE_TIER and RESTORE_SNAPSHOT are required for restore mode",
        )
    repos = _parse_restore_repos(os.environ.get("RESTORE_REPOS", ""))

    results = backup.restore_repos(
        tier, snapshot, repos,
        repo_bucket=os.environ.get("REPO_BUCKET", backup.DEFAULT_REPO_BUCKET),
        lfs_bucket=os.environ.get("LFS_BUCKET", backup.DEFAULT_LFS_BUCKET),
        backup_bucket=os.environ.get(
            "BACKUP_BUCKET", backup.DEFAULT_BACKUP_BUCKET,
        ),
    )
    failed = [r for r in results if r.get("status") != "ok"]
    LOG.info(
        "Restore complete: %d repos, %d failed", len(results), len(failed),
    )
    if failed:
        names = ", ".join(f"{r['group']}/{r['repo']}" for r in failed)
        raise backup.BackupError(f"Restore failed for: {names}")
    return results


def main() -> int:
    """Run the backup job selected by ``BACKUP_MODE``.

    Returns:
        Process exit code (0 on success, 1 on failure).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    mode = os.environ.get("BACKUP_MODE", "archive").strip().lower()
    topic_arn = os.environ.get("ALERTS_TOPIC_ARN", "")

    try:
        if mode == "archive":
            _run_archive()
        elif mode == "restore":
            _run_restore()
        else:
            raise backup.BackupError(f"Unknown BACKUP_MODE: {mode!r}")
    except Exception as exc:  # pylint: disable=broad-except
        LOG.error("Backup job failed (mode=%s): %s", mode, exc)
        _publish_alert(
            topic_arn,
            subject=f"GitGateway backup {mode} FAILED",
            message=f"The GitGateway {mode} job failed:\n\n{exc}",
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
