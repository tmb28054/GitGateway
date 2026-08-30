#!/usr/bin/env python3
"""Import oversized repos by moving large blobs into Git LFS.

Some archived repos have large blobs baked into their git history,
making the pack exceed the API Gateway 10MB request limit (HTTP 413
on push). This script rewrites each repo's history with
``git lfs migrate import`` so large objects move into LFS (uploaded
directly to S3 via presigned URLs), shrinking the pack enough to push.

For each repo it:

1. Extracts the bare repo from the archive.
2. Clones it to a working (non-bare) repo so LFS migrate can run.
3. Runs ``git lfs migrate import`` for blobs above a size threshold.
4. Ensures the group and repository exist on the server.
5. Pushes LFS objects and all branches/tags.

History rewriting changes commit SHAs — acceptable for archive
restore. Reads the API token from ``GITGATEWAY_TOKEN``.

Usage:
    python scripts/import_large_repos.py 2026-08-21.tar.gz \\
        --repo botthouse/letsencrypt --repo deprecated/echobridge
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess  # nosec B404 — git CLI is required
import sys
import tarfile
import tempfile
from pathlib import Path

import requests

LOG = logging.getLogger("import_large")

DEFAULT_BASE_URL = "https://git.botthouse.net"
# Blobs at or above this size are moved into LFS.
DEFAULT_THRESHOLD = "1MB"

# The repos known to exceed the push limit (group/name in the archive).
DEFAULT_LARGE_REPOS = [
    "botthouse/letsencrypt",
    "botthouse/www.botthouse.net",
    "deprecated/ansible-enforcement",
    "deprecated/echobridge",
    "deprecated/openhab-configs",
    "deprecated/rpi-gitlab-runner-armv8",
    "deprecated/wordpress-site",
    "freddiemac/lambda-behind-apigw",
    "javier/online-store",
]


def _git(cwd: Path, *args: str, timeout: int = 1800):
    """Run a git command and return the CompletedProcess.

    Args:
        cwd: Working directory.
        *args: git arguments.
        timeout: Timeout in seconds.

    Returns:
        The CompletedProcess.
    """
    return subprocess.run(  # nosec B603 B607
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _pack_size(repo_dir: Path) -> int:
    """Return total size in bytes of the repo's pack files.

    Args:
        repo_dir: Path to the git repo.

    Returns:
        Total pack size in bytes (0 if no packs).
    """
    pack_dir = repo_dir / "objects" / "pack"
    if not pack_dir.exists():
        return 0
    return sum(
        p.stat().st_size for p in pack_dir.glob("*.pack")
    )


def _extract_repo(archive: Path, date_dir: str, group: str,
                  repo: str, dest: Path) -> Path | None:
    """Extract a single repo directory from the archive.

    Handles the stray .gitt/.git suffix in archive paths.

    Args:
        archive: Path to the tar.gz.
        date_dir: Top-level date directory name in the archive.
        group: Group name.
        repo: Repository name (normalized, no suffix).
        dest: Directory to extract into.

    Returns:
        Path to the extracted bare repo, or None if not found.
    """
    with tarfile.open(archive) as tar:
        prefix_candidates = [
            f"{date_dir}/{group}/{repo}/",
            f"{date_dir}/{group}/{repo}.git/",
            f"{date_dir}/{group}/{repo}.gitt/",
        ]
        members = []
        matched_prefix = None
        for member in tar.getmembers():
            for prefix in prefix_candidates:
                if member.name.startswith(prefix):
                    members.append(member)
                    matched_prefix = prefix
                    break
        if not members:
            return None
        tar.extractall(dest, members=members, filter="data")  # nosec B202
        return dest / matched_prefix.rstrip("/")


def _detect_date_dir(archive: Path) -> str:
    """Return the top-level date directory name in the archive.

    Args:
        archive: Path to the tar.gz.

    Returns:
        The top-level directory name.
    """
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            top = member.name.split("/", 1)[0]
            if top:
                return top
    return ""


def _ensure_group(base_url: str, group: str) -> None:
    """Ensure a group exists (idempotent)."""
    resp = requests.post(
        f"{base_url}/api/groups", json={"name": group}, timeout=30,
    )
    if resp.status_code not in (201, 409):
        LOG.warning(
            "Group %s: status %s %s",
            group, resp.status_code, resp.text[:150],
        )


def _ensure_repo(base_url: str, group: str, repo: str) -> None:
    """Ensure a repo exists (idempotent)."""
    resp = requests.post(
        f"{base_url}/api/repos/{group}", json={"name": repo}, timeout=30,
    )
    if resp.status_code not in (201, 409):
        LOG.warning(
            "Repo %s/%s: status %s %s",
            group, repo, resp.status_code, resp.text[:150],
        )


def _push_commits_in_batches(  # pylint: disable=too-many-locals
    clone_dir: Path, push_url: str, group: str, repo: str,
    batch: int = 5,
) -> bool:
    """Push each branch's history in small commit batches.

    For repos whose bloat is many small files (LFS can't help), push
    incrementally so each receive-pack request stays under the 10MB
    API Gateway limit.

    Args:
        clone_dir: Path to the mirror clone.
        push_url: Authenticated push URL.
        group: Group name (for logging).
        repo: Repo name (for logging).
        batch: Commits per push.

    Returns:
        True if all branches and tags pushed.
    """
    ok = True
    heads = _git(
        clone_dir, "for-each-ref", "--format=%(refname)", "refs/heads",
    )
    branch_refs = [ln for ln in heads.stdout.split("\n") if ln.strip()]

    for ref in branch_refs:
        branch = ref.replace("refs/heads/", "")
        rev = _git(clone_dir, "rev-list", "--reverse", ref)
        commits = [c for c in rev.stdout.split("\n") if c.strip()]
        if not commits:
            continue

        pushed_ok = True
        for idx in range(batch - 1, len(commits), batch):
            sha = commits[idx]
            res = _git(
                clone_dir, "push", push_url, "--force",
                f"{sha}:refs/heads/{branch}",
            )
            if res.returncode != 0:
                LOG.warning(
                    "Batch push %s/%s@%s failed: %s",
                    group, repo, branch, res.stderr.strip()[:200],
                )
                pushed_ok = False
                break

        if pushed_ok:
            tip = commits[-1]
            res = _git(
                clone_dir, "push", push_url, "--force",
                f"{tip}:refs/heads/{branch}",
            )
            if res.returncode != 0:
                LOG.error(
                    "Tip push %s/%s@%s failed: %s",
                    group, repo, branch, res.stderr.strip()[:200],
                )
                ok = False
        else:
            ok = False

    tag_res = _git(clone_dir, "push", push_url, "--tags", "--force")
    if (tag_res.returncode != 0
            and "does not match any" not in tag_res.stderr
            and "up-to-date" not in tag_res.stderr.lower()):
        LOG.warning(
            "Tag push %s/%s: %s", group, repo, tag_res.stderr.strip()[:200],
        )

    if ok:
        LOG.info("Pushed %s/%s (commit batches)", group, repo)
    return ok


def _migrate_and_push(  # pylint: disable=too-many-locals,too-many-return-statements,too-many-positional-arguments
    bare_path: Path, work_dir: Path, base_url: str, token: str,
    group: str, repo: str, threshold: str,
) -> bool:
    """Clone, LFS-migrate history, and push a single repo.

    Args:
        bare_path: Path to the extracted bare repo.
        work_dir: Scratch directory for the working clone.
        base_url: GitGateway base URL.
        token: API token.
        group: Group name.
        repo: Repository name.
        threshold: LFS size threshold (e.g. "1MB").

    Returns:
        True on success.
    """
    clone_dir = work_dir / "work"
    # Clone all refs into a working repo (mirror keeps every branch/tag)
    res = _git(work_dir, "clone", "--mirror", str(bare_path), str(clone_dir))
    if res.returncode != 0:
        LOG.error("Clone failed %s/%s: %s", group, repo, res.stderr[:200])
        return False

    # Disable LFS lock verification for all pushes to this remote so
    # git-lfs doesn't block the push demanding lock support.
    host = base_url.split("://", 1)[-1]
    scheme = base_url.split("://", 1)[0]
    push_url = f"{scheme}://__token__:{token}@{host}/{group}/{repo}.git"
    lfs_url = f"{push_url}/info/lfs"
    _git(clone_dir, "config", "lfs.url", lfs_url)
    _git(clone_dir, "config", f"lfs.{lfs_url}.locksverify", "false")
    _git(clone_dir, "config", "lfs.locksverify", "false")

    # Migrate large blobs into LFS, lowering the threshold until the
    # repacked pack is comfortably under the API Gateway 10MB limit.
    # Size thresholds can leave many-small-files bloat behind, so we
    # retry with progressively smaller thresholds.
    thresholds = [threshold, "512KB", "256KB", "100KB", "50KB", "10KB"]
    seen = set()
    ordered = [t for t in thresholds if not (t in seen or seen.add(t))]
    migrated = False
    for thr in ordered:
        mig = _git(
            clone_dir, "lfs", "migrate", "import",
            "--everything", f"--above={thr}",
        )
        if mig.returncode != 0:
            LOG.error(
                "LFS migrate failed %s/%s: %s",
                group, repo, mig.stderr[:300],
            )
            return False
        _git(clone_dir, "reflog", "expire", "--expire=now", "--all")
        _git(clone_dir, "gc", "--prune=now", "--aggressive")
        pack_bytes = _pack_size(clone_dir)
        LOG.info(
            "Migrated %s/%s above %s -> pack %.1fMB",
            group, repo, thr, pack_bytes / 1048576,
        )
        # Target well under 10MB to leave room for git protocol
        # overhead in the single receive-pack request.
        if pack_bytes < 3 * 1024 * 1024:
            migrated = True
            break

    if not migrated:
        # LFS migration couldn't shrink the pack — the bloat is many
        # small files, not large blobs. Fall back to pushing commits
        # in small batches so each receive-pack request stays small.
        LOG.info(
            "%s/%s not LFS-shrinkable; pushing commits in batches",
            group, repo,
        )
        return _push_commits_in_batches(
            clone_dir, push_url, group, repo, batch=1,
        )

    # Push LFS objects to S3 first (endpoint/locks configured above).
    lfs_push = _git(clone_dir, "lfs", "push", "--all", push_url)
    if lfs_push.returncode != 0:
        LOG.warning(
            "LFS object push %s/%s: %s",
            group, repo, lfs_push.stderr[:200],
        )

    # Push all branches and tags (pack is now small — pointers only).
    for refspec in ("refs/heads/*:refs/heads/*", "refs/tags/*:refs/tags/*"):
        push = _git(clone_dir, "push", push_url, refspec, "--force")
        if push.returncode != 0 and "does not match any" not in push.stderr:
            LOG.error(
                "Push failed %s/%s (%s): %s",
                group, repo, refspec, push.stderr[:250],
            )
            return False

    LOG.info("Pushed %s/%s", group, repo)
    return True


def main() -> int:
    """Run the large-repo LFS-migrate import.

    Returns:
        Process exit code.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", help="Path to the .tar.gz archive")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--threshold", default=DEFAULT_THRESHOLD)
    parser.add_argument(
        "--repo", action="append", default=[],
        help="group/name to import (repeatable); defaults to known large repos",
    )
    args = parser.parse_args()

    token = os.environ.get("GITGATEWAY_TOKEN", "")
    if not token:
        LOG.error("GITGATEWAY_TOKEN environment variable is not set")
        return 1

    archive = Path(args.archive)
    if not archive.exists():
        LOG.error("Archive not found: %s", archive)
        return 1

    targets = args.repo or DEFAULT_LARGE_REPOS
    date_dir = _detect_date_dir(archive)
    LOG.info("Archive date dir: %s; %d repos to import", date_dir, len(targets))

    failures = []
    for target in targets:
        group, _, repo = target.partition("/")
        # Normalize any .gitt/.git suffix off the repo name
        for suffix in (".gitt", ".git"):
            if repo.endswith(suffix):
                repo = repo[: -len(suffix)]
                break

        with tempfile.TemporaryDirectory(prefix="gg-large-") as tmp:
            tmp_path = Path(tmp)
            bare = _extract_repo(archive, date_dir, group, repo, tmp_path)
            if bare is None:
                LOG.error("Could not find %s in archive", target)
                failures.append(target)
                continue

            _ensure_group(args.base_url, group)
            _ensure_repo(args.base_url, group, repo)

            if not _migrate_and_push(
                bare, tmp_path, args.base_url, token,
                group, repo, args.threshold,
            ):
                failures.append(f"{group}/{repo}")

    LOG.info("Done. %d repos, %d failures", len(targets), len(failures))
    if failures:
        LOG.warning("Failed: %s", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
