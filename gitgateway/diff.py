"""Diff generation and file comparison for GitGateway.

Generates structured diffs between branches/commits by invoking
git diff as a subprocess on a bare repository.
"""

from __future__ import annotations

import logging
import subprocess  # nosec B404 - Required for git commands
from dataclasses import dataclass, field
from pathlib import Path

LOG = logging.getLogger(__name__)


class DiffError(Exception):
    """Raised when a diff operation fails."""


@dataclass
class DiffLine:
    """A single line in a diff hunk.

    Attributes:
        content: The line content (without +/- prefix).
        line_type: One of 'add', 'delete', or 'context'.
        old_line_no: Line number in the old file (None for adds).
        new_line_no: Line number in the new file (None for deletes).
    """

    content: str
    line_type: str  # 'add', 'delete', 'context'
    old_line_no: int | None = None
    new_line_no: int | None = None


@dataclass
class Hunk:
    """A diff hunk representing a changed section.

    Attributes:
        old_start: Starting line number in old file.
        old_count: Number of lines in old file.
        new_start: Starting line number in new file.
        new_count: Number of lines in new file.
        lines: List of DiffLine objects.
    """

    old_start: int = 0
    old_count: int = 0
    new_start: int = 0
    new_count: int = 0
    lines: list[DiffLine] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "old_start": self.old_start,
            "old_count": self.old_count,
            "new_start": self.new_start,
            "new_count": self.new_count,
            "lines": [
                {
                    "content": line.content,
                    "type": line.line_type,
                    "old_line_no": line.old_line_no,
                    "new_line_no": line.new_line_no,
                }
                for line in self.lines
            ],
        }


@dataclass
class FileDiff:
    """Diff for a single file.

    Attributes:
        filename: The current filename.
        old_filename: Previous filename (for renames).
        status: One of 'added', 'modified', 'deleted', 'renamed'.
        is_binary: Whether the file is binary.
        hunks: List of Hunk objects.
    """

    filename: str
    old_filename: str = ""
    status: str = "modified"
    is_binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "filename": self.filename,
            "old_filename": self.old_filename,
            "status": self.status,
            "is_binary": self.is_binary,
            "hunks": [h.to_dict() for h in self.hunks],
        }


@dataclass
class Commit:
    """A git commit.

    Attributes:
        sha: The commit SHA.
        message: The commit message.
        author: The commit author.
        date: The commit date.
    """

    sha: str
    message: str
    author: str
    date: str

    def to_dict(self) -> dict:
        """Serialize to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "sha": self.sha,
            "message": self.message,
            "author": self.author,
            "date": self.date,
        }


def generate_diff(
    repo_path: Path,
    base: str,
    head: str,
) -> list[FileDiff]:
    """Generate a structured diff between two refs.

    Args:
        repo_path: Path to the bare git repository.
        base: Base ref (branch, tag, or SHA).
        head: Head ref (branch, tag, or SHA).

    Returns:
        List of FileDiff objects.

    Raises:
        DiffError: If the git diff command fails.
    """
    raw_diff = _run_git(
        repo_path,
        ["diff", "--no-color", "-M", base, head],
    )

    if not raw_diff.strip():
        return []

    return _parse_unified_diff(raw_diff)


def get_file_at_ref(
    repo_path: Path,
    ref: str,
    file_path: str,
) -> str:
    """Get the content of a file at a specific ref.

    Args:
        repo_path: Path to the bare git repository.
        ref: Git ref (branch, tag, or SHA).
        file_path: Path to the file within the repo.

    Returns:
        The file content as a string.

    Raises:
        DiffError: If the file or ref does not exist.
    """
    return _run_git(repo_path, ["show", f"{ref}:{file_path}"])


def get_commit_log(
    repo_path: Path,
    base: str,
    head: str,
) -> list[Commit]:
    """Get commit log between two refs.

    Args:
        repo_path: Path to the bare git repository.
        base: Base ref.
        head: Head ref.

    Returns:
        List of Commit objects (newest first).

    Raises:
        DiffError: If the git log command fails.
    """
    # Use a separator that won't appear in commit messages
    sep = "---GG_COMMIT_SEP---"
    fmt = f"%H{sep}%s{sep}%an{sep}%aI"

    raw = _run_git(
        repo_path,
        ["log", f"--format={fmt}", f"{base}..{head}"],
    )

    if not raw.strip():
        return []

    commits = []
    for line in raw.strip().split("\n"):
        parts = line.split(sep)
        if len(parts) == 4:
            commits.append(Commit(
                sha=parts[0],
                message=parts[1],
                author=parts[2],
                date=parts[3],
            ))

    return commits


def _run_git(repo_path: Path, args: list[str]) -> str:
    """Run a git command on a bare repo.

    Args:
        repo_path: Path to the bare repository.
        args: Git command arguments.

    Returns:
        stdout as a string.

    Raises:
        DiffError: If the command fails.
    """
    cmd = ["git", "--git-dir", str(repo_path)] + args

    try:
        result = subprocess.run(  # nosec
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError as exc:
        raise DiffError("git binary not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise DiffError("git command timed out") from exc

    if result.returncode != 0:
        raise DiffError(
            f"git command failed: {result.stderr.strip()}",
        )

    return result.stdout


def _parse_unified_diff(raw: str) -> list[FileDiff]:  # pylint: disable=too-many-branches,too-many-statements
    """Parse unified diff output into structured FileDiff objects.

    Args:
        raw: Raw unified diff string from git diff.

    Returns:
        List of FileDiff objects.
    """
    files: list[FileDiff] = []
    current_file: FileDiff | None = None
    current_hunk: Hunk | None = None
    old_line = 0
    new_line = 0

    for line in raw.split("\n"):
        # New file header
        if line.startswith("diff --git"):
            if current_file:
                if current_hunk:
                    current_file.hunks.append(current_hunk)
                files.append(current_file)
            current_file = _parse_diff_header(line)
            current_hunk = None
            continue

        if current_file is None:
            continue

        # File metadata
        if line.startswith("--- "):
            old_name = line[4:]
            if old_name == "/dev/null":
                current_file.status = "added"
            continue

        if line.startswith("+++ "):
            new_name = line[4:]
            if new_name == "/dev/null":
                current_file.status = "deleted"
            continue

        if line.startswith("rename from "):
            current_file.old_filename = line[12:]
            current_file.status = "renamed"
            continue

        if line.startswith("rename to "):
            current_file.filename = line[10:]
            continue

        # Binary file
        if line.startswith("Binary files"):
            current_file.is_binary = True
            continue

        # Hunk header
        if line.startswith("@@"):
            if current_hunk:
                current_file.hunks.append(current_hunk)
            current_hunk = _parse_hunk_header(line)
            old_line = current_hunk.old_start
            new_line = current_hunk.new_start
            continue

        # Diff content lines
        if current_hunk is None:
            continue

        if line.startswith("+"):
            current_hunk.lines.append(DiffLine(
                content=line[1:],
                line_type="add",
                new_line_no=new_line,
            ))
            new_line += 1
        elif line.startswith("-"):
            current_hunk.lines.append(DiffLine(
                content=line[1:],
                line_type="delete",
                old_line_no=old_line,
            ))
            old_line += 1
        elif line.startswith(" "):
            current_hunk.lines.append(DiffLine(
                content=line[1:],
                line_type="context",
                old_line_no=old_line,
                new_line_no=new_line,
            ))
            old_line += 1
            new_line += 1

    # Append last file/hunk
    if current_file:
        if current_hunk:
            current_file.hunks.append(current_hunk)
        files.append(current_file)

    return files


def _parse_diff_header(line: str) -> FileDiff:
    """Parse a 'diff --git' header line.

    Args:
        line: The diff header line.

    Returns:
        A FileDiff with filename set.
    """
    # diff --git a/path/to/file b/path/to/file
    parts = line.split(" b/", 1)
    if len(parts) == 2:
        filename = parts[1]
    else:
        filename = line.split()[-1]

    return FileDiff(filename=filename)


def _parse_hunk_header(line: str) -> Hunk:
    """Parse a hunk header line.

    Format: @@ -old_start,old_count +new_start,new_count @@

    Args:
        line: The hunk header line.

    Returns:
        A Hunk with start/count values set.
    """
    # Strip @@ markers and any trailing context
    parts = line.split("@@")
    if len(parts) < 2:
        return Hunk()

    range_str = parts[1].strip()

    old_range = ""
    new_range = ""
    tokens = range_str.split()
    idx = 0
    while idx < len(tokens):
        if tokens[idx].startswith("-"):
            old_range = tokens[idx][1:]
        elif tokens[idx].startswith("+"):
            new_range = tokens[idx][1:]
        idx += 1

    old_start, old_count = _parse_range(old_range)
    new_start, new_count = _parse_range(new_range)

    return Hunk(
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
    )


def _parse_range(range_str: str) -> tuple[int, int]:
    """Parse a range string like '10,5' into (start, count).

    Args:
        range_str: Range string (e.g., '10,5' or '10').

    Returns:
        Tuple of (start, count).
    """
    if "," in range_str:
        parts = range_str.split(",")
        return int(parts[0]), int(parts[1])
    if range_str:
        return int(range_str), 1
    return 0, 0
