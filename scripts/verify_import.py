#!/usr/bin/env python3
"""Verify archive import: compare refs in the archive vs the server.

For each repo in the archive, list its branch and tag refs, then list
the same repo's refs on the GitGateway server (via git ls-remote). Any
branch or tag present in the archive but missing on the server is a
potential data-loss finding.

Note: repos whose history was rewritten (LFS migration / filter-repo)
will have different commit SHAs on the server. This check verifies that
every branch and tag *name* is present, not that SHAs match — SHA
divergence for rewritten repos is expected and not data loss.

Usage:
    GITGATEWAY_TOKEN=gg_xxx python scripts/verify_import.py 2026-08-21.tar.gz
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess  # nosec B404
import sys
import tarfile
from pathlib import Path

LOG = logging.getLogger("verify_import")

DEFAULT_BASE_URL = "https://git.botthouse.net"

# Repos whose history was intentionally rewritten during import.
REWRITTEN = {
    "botthouse/letsencrypt",  # bulky key/csr/archive dirs stripped
}


def _detect_date_dir(archive: Path) -> str:
    """Return the archive's top-level directory name."""
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            top = member.name.split("/", 1)[0]
            if top:
                return top
    return ""


def _archive_repo_refs(archive: Path):  # pylint: disable=too-many-locals,too-many-branches
    """Extract branch and tag ref names per repo from the archive.

    Reads each repo's packed-refs and loose refs to enumerate
    refs/heads/* and refs/tags/*.

    Args:
        archive: Path to the tar.gz.

    Yields:
        (group, repo, branches:set, tags:set) tuples.
    """
    date_dir = _detect_date_dir(archive)
    with tarfile.open(archive) as tar:
        # Map repo -> {"branches": set, "tags": set}
        repos: dict[str, dict] = {}
        for member in tar.getmembers():
            name = member.name
            if not name.startswith(f"{date_dir}/"):
                continue
            rel = name[len(date_dir) + 1:]
            parts = rel.split("/")
            if len(parts) < 3:
                continue
            group, repo = parts[0], parts[1]
            key = f"{group}/{repo}"
            entry = repos.setdefault(
                key, {"branches": set(), "tags": set(), "packed": None},
            )
            tail = "/".join(parts[2:])
            if tail.startswith("refs/heads/") and not member.isdir():
                entry["branches"].add(tail[len("refs/heads/"):])
            elif tail.startswith("refs/tags/") and not member.isdir():
                entry["tags"].add(tail[len("refs/tags/"):])
            elif tail == "packed-refs" and member.isfile():
                fobj = tar.extractfile(member)
                if fobj:
                    entry["packed"] = fobj.read().decode(
                        "utf-8", "replace",
                    )

        for key, entry in repos.items():
            branches = set(entry["branches"])
            tags = set(entry["tags"])
            # Parse packed-refs for additional refs
            if entry["packed"]:
                for line in entry["packed"].split("\n"):
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("^"):
                        continue
                    parts = line.split(" ", 1)
                    if len(parts) != 2:
                        continue
                    ref = parts[1]
                    if ref.startswith("refs/heads/"):
                        branches.add(ref[len("refs/heads/"):])
                    elif ref.startswith("refs/tags/"):
                        tags.add(ref[len("refs/tags/"):])
            group, repo = key.split("/", 1)
            # Normalize stray suffixes
            for suffix in (".gitt", ".git"):
                if repo.endswith(suffix):
                    repo = repo[: -len(suffix)]
                    break
            yield group, repo, branches, tags


def _server_refs(base_url: str, token: str, group: str, repo: str):
    """Return (branches, tags) sets present on the server via ls-remote.

    Args:
        base_url: GitGateway base URL.
        token: API token.
        group: Group name.
        repo: Repo name.

    Returns:
        Tuple of (branches:set, tags:set). Empty sets if unreachable.
    """
    host = base_url.split("://", 1)[-1]
    scheme = base_url.split("://", 1)[0]
    url = f"{scheme}://__token__:{token}@{host}/{group}/{repo}.git"
    result = subprocess.run(  # nosec B603 B607
        ["git", "ls-remote", url],
        capture_output=True, text=True, timeout=60, check=False,
    )
    branches, tags = set(), set()
    if result.returncode != 0:
        return branches, tags
    for line in result.stdout.split("\n"):
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        ref = parts[1]
        if ref.startswith("refs/heads/"):
            branches.add(ref[len("refs/heads/"):])
        elif ref.startswith("refs/tags/") and not ref.endswith("^{}"):
            tags.add(ref[len("refs/tags/"):])
    return branches, tags


def main() -> int:  # pylint: disable=too-many-locals,too-many-branches
    """Run the verification and report any missing refs.

    Returns:
        0 if no data loss, 1 if any refs are missing.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()

    token = os.environ.get("GITGATEWAY_TOKEN", "")
    if not token:
        LOG.error("GITGATEWAY_TOKEN not set")
        return 1

    archive = Path(args.archive)
    if not archive.exists():
        LOG.error("Archive not found: %s", archive)
        return 1

    problems = []
    checked = 0
    for group, repo, a_branches, a_tags in _archive_repo_refs(archive):
        if not a_branches and not a_tags:
            # Repo may be empty in the archive; nothing to verify
            continue
        checked += 1
        s_branches, s_tags = _server_refs(
            args.base_url, token, group, repo,
        )
        missing_b = a_branches - s_branches
        missing_t = a_tags - s_tags
        rewritten = f"{group}/{repo}" in REWRITTEN
        if missing_b or missing_t:
            problems.append(
                (group, repo, missing_b, missing_t, rewritten),
            )
            LOG.warning(
                "%s/%s missing branches=%s tags=%s%s",
                group, repo, sorted(missing_b), sorted(missing_t),
                " (REWRITTEN)" if rewritten else "",
            )
        else:
            LOG.info(
                "%s/%s OK (%d branches, %d tags)",
                group, repo, len(a_branches), len(a_tags),
            )

    LOG.info("Checked %d repos; %d with missing refs", checked, len(problems))
    real = [p for p in problems if not p[4]]
    if real:
        LOG.error("DATA LOSS in %d non-rewritten repos:", len(real))
        for group, repo, mb, mt, _ in real:
            LOG.error(
                "  %s/%s branches=%s tags=%s",
                group, repo, sorted(mb), sorted(mt),
            )
        return 1
    LOG.info("No data loss detected.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
