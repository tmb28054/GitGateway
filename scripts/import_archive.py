#!/usr/bin/env python3
"""Ad-hoc importer: restore bare git repos from a dated archive.

Archive layout: ``<date>/<group>/<repo>/`` where each ``<repo>`` is a
bare git repository. For each repo this script:

1. Ensures the group exists (POST /api/groups).
2. Creates the repository in the group (POST /api/repos/<group>).
3. Pushes all branches and tags with ``git push --mirror``.

Auth: reads the API token from the ``GITGATEWAY_TOKEN`` environment
variable. Git operations use ``__token__:<token>`` basic auth.

Usage:
    python scripts/import_archive.py 2026-08-21.tar.gz \\
        --base-url https://git.botthouse.net
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess  # nosec B404 — git CLI is required for push
import sys
import tarfile
import tempfile
from pathlib import Path

import requests

LOG = logging.getLogger("import_archive")

DEFAULT_BASE_URL = "https://git.botthouse.net"


def _extract_archive(archive: Path, dest: Path) -> Path:
    """Extract the tar.gz archive and return the top-level date dir.

    Args:
        archive: Path to the .tar.gz archive.
        dest: Directory to extract into.

    Returns:
        Path to the extracted top-level directory.
    """
    LOG.info("Extracting %s ...", archive)
    with tarfile.open(archive) as tar:
        tar.extractall(dest, filter="data")  # nosec B202 — trusted archive
    # Top-level dir is the date (e.g. 2026-08-21)
    entries = [p for p in dest.iterdir() if p.is_dir()]
    if len(entries) == 1:
        return entries[0]
    return dest


def _discover_repos(root: Path) -> list[tuple[str, str, Path]]:
    """Find all bare repos under root/<group>/<repo>.

    A directory is treated as a bare repo if it contains a HEAD file.

    Args:
        root: The extracted date directory.

    Returns:
        List of (group, repo_name, repo_path) tuples.
    """
    repos = []
    for group_dir in sorted(root.iterdir()):
        if not group_dir.is_dir():
            continue
        group = group_dir.name
        for repo_dir in sorted(group_dir.iterdir()):
            if not repo_dir.is_dir():
                continue
            if not (repo_dir / "HEAD").exists():
                continue
            # Normalize repo name: strip a stray .git/.gitt suffix
            repo_name = repo_dir.name
            for suffix in (".gitt", ".git"):
                if repo_name.endswith(suffix):
                    repo_name = repo_name[: -len(suffix)]
                    break
            repos.append((group, repo_name, repo_dir))
    return repos


def _ensure_group(base_url: str, group: str) -> None:
    """Ensure a group exists (idempotent).

    Args:
        base_url: GitGateway base URL.
        group: Group name.
    """
    resp = requests.post(
        f"{base_url}/api/groups",
        json={"name": group},
        timeout=30,
    )
    if resp.status_code == 201:
        LOG.info("Created group %s", group)
    elif resp.status_code == 409:
        LOG.info("Group %s already exists", group)
    else:
        LOG.warning(
            "Group %s: unexpected status %s: %s",
            group, resp.status_code, resp.text[:200],
        )


def _ensure_repo(base_url: str, group: str, repo: str) -> None:
    """Ensure a repository exists in the group (idempotent).

    Args:
        base_url: GitGateway base URL.
        group: Group name.
        repo: Repository name.
    """
    resp = requests.post(
        f"{base_url}/api/repos/{group}",
        json={"name": repo},
        timeout=30,
    )
    if resp.status_code == 201:
        LOG.info("Created repo %s/%s", group, repo)
    elif resp.status_code == 409:
        LOG.info("Repo %s/%s already exists", group, repo)
    else:
        LOG.warning(
            "Repo %s/%s: unexpected status %s: %s",
            group, repo, resp.status_code, resp.text[:200],
        )


def _git(repo_path: Path, *args: str, timeout: int = 600):
    """Run a git command in a repo and return the CompletedProcess.

    Args:
        repo_path: Path to the git repo.
        *args: git arguments.
        timeout: Command timeout in seconds.

    Returns:
        The CompletedProcess result.
    """
    return subprocess.run(  # nosec B603 B607
        ["git", *args],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _branch_commits_oldest_first(repo_path: Path, ref: str) -> list[str]:
    """Return commit SHAs for a ref, oldest first.

    Args:
        repo_path: Path to the bare repo.
        ref: The ref name (e.g. refs/heads/main).

    Returns:
        List of commit SHAs oldest-to-newest.
    """
    result = _git(repo_path, "rev-list", "--reverse", ref)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.split("\n") if line.strip()]


def _push_incrementally(  # pylint: disable=too-many-locals,too-many-branches
    push_url: str, repo_path: Path, group: str, repo: str,
    batch: int = 200,
) -> bool:
    """Push each branch in commit batches to stay under the 10MB limit.

    API Gateway caps request bodies at ~10MB, so a single push of a
    large pack fails with HTTP 413. This walks each branch's history
    oldest-first and pushes incrementally: each push sends only the
    delta since the last, keeping request size small.

    Args:
        push_url: Authenticated push URL.
        repo_path: Path to the bare repo.
        group: Group name (for logging).
        repo: Repo name (for logging).
        batch: Number of commits per push.

    Returns:
        True if all branches and tags pushed successfully.
    """
    ok = True

    # Enumerate local branches
    heads = _git(repo_path, "for-each-ref", "--format=%(refname)", "refs/heads")
    branch_refs = [
        line for line in heads.stdout.split("\n") if line.strip()
    ]

    for ref in branch_refs:
        branch = ref.replace("refs/heads/", "")
        commits = _branch_commits_oldest_first(repo_path, ref)
        if not commits:
            continue

        # Push in incremental batches up the history
        pushed_ok = True
        for idx in range(batch - 1, len(commits), batch):
            sha = commits[idx]
            result = _git(
                repo_path, "push", push_url, "--force",
                f"{sha}:refs/heads/{branch}",
            )
            if result.returncode != 0:
                LOG.warning(
                    "Batch push %s/%s@%s failed: %s",
                    group, repo, branch, result.stderr.strip()[:200],
                )
                pushed_ok = False
                break

        # Final push to the branch tip
        if pushed_ok:
            tip = commits[-1]
            result = _git(
                repo_path, "push", push_url, "--force",
                f"{tip}:refs/heads/{branch}",
            )
            if result.returncode != 0:
                LOG.error(
                    "Tip push %s/%s@%s failed: %s",
                    group, repo, branch, result.stderr.strip()[:200],
                )
                ok = False

    # Push tags (usually small)
    tag_result = _git(
        repo_path, "push", push_url, "--tags", "--force",
    )
    if tag_result.returncode != 0 and "does not match any" not in tag_result.stderr:
        stderr = tag_result.stderr.strip()
        if stderr and "up-to-date" not in stderr.lower():
            LOG.warning(
                "Tag push %s/%s: %s", group, repo, stderr[:200],
            )

    return ok


def _push_mirror(
    base_url: str, token: str, group: str, repo: str, repo_path: Path,
) -> bool:
    """Push all branches and tags from a bare repo to GitGateway.

    Tries a single push first; if it fails (e.g. HTTP 413 from the
    API Gateway 10MB body limit), falls back to incremental batched
    pushes that keep each request small.

    Args:
        base_url: GitGateway base URL.
        token: API token for auth.
        group: Group name.
        repo: Repository name.
        repo_path: Path to the local bare repo.

    Returns:
        True if the push succeeded.
    """
    host = base_url.split("://", 1)[-1]
    scheme = base_url.split("://", 1)[0]
    push_url = f"{scheme}://__token__:{token}@{host}/{group}/{repo}.git"

    # Try a straight push of all branches first
    result = _git(
        repo_path, "push", push_url,
        "refs/heads/*:refs/heads/*", "--force",
    )
    if result.returncode == 0:
        # Branches ok — push tags too
        _git(repo_path, "push", push_url, "--tags", "--force")
        LOG.info("Pushed %s/%s (single)", group, repo)
        return True

    stderr = result.stderr.strip()
    if "does not match any" in stderr:
        # No branches to push; still try tags
        _git(repo_path, "push", push_url, "--tags", "--force")
        LOG.info("Pushed %s/%s (tags only / empty)", group, repo)
        return True

    # Fall back to incremental push (handles 413 / large packs)
    LOG.info(
        "Single push failed for %s/%s, pushing incrementally...",
        group, repo,
    )
    if _push_incrementally(push_url, repo_path, group, repo):
        LOG.info("Pushed %s/%s (incremental)", group, repo)
        return True
    return False


def main() -> int:
    """Run the archive import.

    Returns:
        Process exit code (0 on success).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", help="Path to the .tar.gz archive")
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL,
        help="GitGateway base URL",
    )
    parser.add_argument(
        "--only-group", default="",
        help="Only import repos from this group",
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

    with tempfile.TemporaryDirectory(prefix="gg-import-") as tmp:
        root = _extract_archive(archive, Path(tmp))
        repos = _discover_repos(root)
        if args.only_group:
            repos = [r for r in repos if r[0] == args.only_group]

        LOG.info("Discovered %d repositories", len(repos))

        ensured_groups: set[str] = set()
        failures = []
        for group, repo, repo_path in repos:
            if group not in ensured_groups:
                _ensure_group(args.base_url, group)
                ensured_groups.add(group)
            _ensure_repo(args.base_url, group, repo)
            if not _push_mirror(
                args.base_url, token, group, repo, repo_path,
            ):
                failures.append(f"{group}/{repo}")

        LOG.info(
            "Done. %d repos, %d groups, %d failures",
            len(repos), len(ensured_groups), len(failures),
        )
        if failures:
            LOG.warning("Failed repos: %s", ", ".join(failures))
            return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
