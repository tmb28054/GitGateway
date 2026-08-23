"""Tests for gitgateway.diff module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from gitgateway.diff import (
    Commit,
    DiffError,
    DiffLine,
    FileDiff,
    Hunk,
    _parse_diff_header,
    _parse_hunk_header,
    _parse_range,
    _parse_unified_diff,
    _run_git,
    generate_diff,
    get_commit_log,
    get_file_at_ref,
)


SAMPLE_DIFF = """diff --git a/hello.py b/hello.py
--- a/hello.py
+++ b/hello.py
@@ -1,3 +1,4 @@
 import sys
+import os

 def main():
-    print("hello")
+    print("world")
"""

SAMPLE_NEW_FILE_DIFF = """diff --git a/new.txt b/new.txt
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+line one
+line two
"""

SAMPLE_DELETE_DIFF = """diff --git a/old.txt b/old.txt
--- a/old.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-was here
-now gone
"""

SAMPLE_RENAME_DIFF = """diff --git a/old_name.py b/new_name.py
rename from old_name.py
rename to new_name.py
--- a/old_name.py
+++ b/new_name.py
@@ -1 +1 @@
-old
+new
"""

SAMPLE_BINARY_DIFF = """diff --git a/image.png b/image.png
Binary files a/image.png and b/image.png differ
"""


class TestParseUnifiedDiff:
    """Tests for _parse_unified_diff function."""

    def test_basic_modification(self) -> None:
        """Parses a basic file modification."""
        files = _parse_unified_diff(SAMPLE_DIFF)
        assert len(files) == 1
        assert files[0].filename == "hello.py"
        assert files[0].status == "modified"
        assert len(files[0].hunks) == 1

        hunk = files[0].hunks[0]
        assert hunk.old_start == 1
        assert hunk.old_count == 3
        assert hunk.new_start == 1
        assert hunk.new_count == 4

        # Check line types
        types = [line.line_type for line in hunk.lines]
        assert "context" in types
        assert "add" in types
        assert "delete" in types

    def test_new_file(self) -> None:
        """Parses a newly added file."""
        files = _parse_unified_diff(SAMPLE_NEW_FILE_DIFF)
        assert len(files) == 1
        assert files[0].status == "added"
        assert files[0].filename == "new.txt"

    def test_deleted_file(self) -> None:
        """Parses a deleted file."""
        files = _parse_unified_diff(SAMPLE_DELETE_DIFF)
        assert len(files) == 1
        assert files[0].status == "deleted"

    def test_renamed_file(self) -> None:
        """Parses a renamed file."""
        files = _parse_unified_diff(SAMPLE_RENAME_DIFF)
        assert len(files) == 1
        assert files[0].status == "renamed"
        assert files[0].old_filename == "old_name.py"
        assert files[0].filename == "new_name.py"

    def test_binary_file(self) -> None:
        """Parses a binary file diff."""
        files = _parse_unified_diff(SAMPLE_BINARY_DIFF)
        assert len(files) == 1
        assert files[0].is_binary is True
        assert files[0].hunks == []

    def test_empty_diff(self) -> None:
        """Returns empty list for empty diff."""
        files = _parse_unified_diff("")
        assert files == []

    def test_multiple_files(self) -> None:
        """Parses diff with multiple files."""
        multi_diff = SAMPLE_DIFF + SAMPLE_NEW_FILE_DIFF
        files = _parse_unified_diff(multi_diff)
        assert len(files) == 2

    def test_line_numbers(self) -> None:
        """Verifies line numbers are tracked correctly."""
        files = _parse_unified_diff(SAMPLE_DIFF)
        hunk = files[0].hunks[0]

        # First line is context " import sys" -> old=1, new=1
        first_context = [
            l for l in hunk.lines if l.line_type == "context"
        ][0]
        assert first_context.old_line_no == 1
        assert first_context.new_line_no == 1

        # Added line "import os" should have new_line_no=2
        add_line = [l for l in hunk.lines if l.line_type == "add"][0]
        assert add_line.new_line_no == 2
        assert add_line.old_line_no is None


class TestParseDiffHeader:
    """Tests for _parse_diff_header function."""

    def test_basic_header(self) -> None:
        """Parses basic diff header."""
        result = _parse_diff_header("diff --git a/file.py b/file.py")
        assert result.filename == "file.py"

    def test_path_with_spaces(self) -> None:
        """Handles file paths - extracts after b/."""
        result = _parse_diff_header("diff --git a/path/to/file.py b/path/to/file.py")
        assert result.filename == "path/to/file.py"

    def test_no_b_prefix(self) -> None:
        """Falls back to last token if no b/ found."""
        result = _parse_diff_header("diff --git something")
        assert result.filename == "something"


class TestParseHunkHeader:
    """Tests for _parse_hunk_header function."""

    def test_basic_hunk(self) -> None:
        """Parses standard hunk header."""
        hunk = _parse_hunk_header("@@ -1,3 +1,4 @@")
        assert hunk.old_start == 1
        assert hunk.old_count == 3
        assert hunk.new_start == 1
        assert hunk.new_count == 4

    def test_single_line_range(self) -> None:
        """Parses hunk with single-line range (no comma)."""
        hunk = _parse_hunk_header("@@ -5 +5 @@")
        assert hunk.old_start == 5
        assert hunk.old_count == 1
        assert hunk.new_start == 5
        assert hunk.new_count == 1

    def test_with_context_text(self) -> None:
        """Parses hunk header with trailing context."""
        hunk = _parse_hunk_header("@@ -10,5 +12,7 @@ def main():")
        assert hunk.old_start == 10
        assert hunk.new_start == 12

    def test_malformed_header(self) -> None:
        """Handles malformed hunk header."""
        hunk = _parse_hunk_header("not a hunk")
        assert hunk.old_start == 0


class TestParseRange:
    """Tests for _parse_range function."""

    def test_with_comma(self) -> None:
        """Parses range with comma."""
        assert _parse_range("10,5") == (10, 5)

    def test_without_comma(self) -> None:
        """Parses range without comma (single line)."""
        assert _parse_range("7") == (7, 1)

    def test_empty_string(self) -> None:
        """Handles empty string."""
        assert _parse_range("") == (0, 0)


class TestRunGit:
    """Tests for _run_git function."""

    def test_successful_command(self, tmp_path: Path) -> None:
        """Runs git command successfully."""
        # Init a real bare repo for this test
        subprocess.run(
            ["git", "init", "--bare", str(tmp_path / "test.git")],
            check=True, capture_output=True,
        )
        result = _run_git(tmp_path / "test.git", ["branch"])
        # Empty repo has no branches, but command succeeds
        assert isinstance(result, str)

    def test_git_not_found(self, tmp_path: Path) -> None:
        """Raises DiffError when git binary not found."""
        with patch(
            "gitgateway.diff.subprocess.run",
            side_effect=FileNotFoundError("git not found"),
        ):
            with pytest.raises(DiffError, match="git binary not found"):
                _run_git(tmp_path, ["status"])

    def test_git_timeout(self, tmp_path: Path) -> None:
        """Raises DiffError on timeout."""
        with patch(
            "gitgateway.diff.subprocess.run",
            side_effect=subprocess.TimeoutExpired("cmd", 60),
        ):
            with pytest.raises(DiffError, match="timed out"):
                _run_git(tmp_path, ["log"])

    def test_git_command_failure(self, tmp_path: Path) -> None:
        """Raises DiffError on non-zero exit code."""
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stderr = "fatal: bad revision"

        with patch("gitgateway.diff.subprocess.run", return_value=mock_result):
            with pytest.raises(DiffError, match="bad revision"):
                _run_git(tmp_path, ["diff", "bad..ref"])


class TestGenerateDiff:
    """Tests for generate_diff function."""

    def test_generates_diff(self, tmp_path: Path) -> None:
        """Generates diff between two refs."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = SAMPLE_DIFF

        with patch("gitgateway.diff.subprocess.run", return_value=mock_result):
            files = generate_diff(tmp_path, "main", "feature")

        assert len(files) == 1
        assert files[0].filename == "hello.py"

    def test_empty_diff(self, tmp_path: Path) -> None:
        """Returns empty list when no differences."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch("gitgateway.diff.subprocess.run", return_value=mock_result):
            files = generate_diff(tmp_path, "main", "main")

        assert files == []


class TestGetFileAtRef:
    """Tests for get_file_at_ref function."""

    def test_gets_file_content(self, tmp_path: Path) -> None:
        """Gets file content at a specific ref."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "print('hello')\n"

        with patch("gitgateway.diff.subprocess.run", return_value=mock_result):
            content = get_file_at_ref(tmp_path, "main", "hello.py")

        assert content == "print('hello')\n"


class TestGetCommitLog:
    """Tests for get_commit_log function."""

    def test_parses_log(self, tmp_path: Path) -> None:
        """Parses git log output."""
        sep = "---GG_COMMIT_SEP---"
        log_output = (
            f"abc123{sep}Fix bug{sep}Alice{sep}2025-01-01T00:00:00+00:00\n"
            f"def456{sep}Add feature{sep}Bob{sep}2025-01-02T00:00:00+00:00\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = log_output

        with patch("gitgateway.diff.subprocess.run", return_value=mock_result):
            commits = get_commit_log(tmp_path, "main", "feature")

        assert len(commits) == 2
        assert commits[0].sha == "abc123"
        assert commits[0].message == "Fix bug"
        assert commits[0].author == "Alice"
        assert commits[1].sha == "def456"

    def test_empty_log(self, tmp_path: Path) -> None:
        """Returns empty list when no commits between refs."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch("gitgateway.diff.subprocess.run", return_value=mock_result):
            commits = get_commit_log(tmp_path, "main", "main")

        assert commits == []

    def test_skips_malformed_lines(self, tmp_path: Path) -> None:
        """Skips log lines that don't have expected format."""
        sep = "---GG_COMMIT_SEP---"
        log_output = (
            f"abc123{sep}Fix{sep}Alice{sep}2025-01-01T00:00:00+00:00\n"
            "malformed line\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = log_output

        with patch("gitgateway.diff.subprocess.run", return_value=mock_result):
            commits = get_commit_log(tmp_path, "main", "feature")

        assert len(commits) == 1


class TestDataclassesToDict:
    """Tests for dataclass serialization."""

    def test_hunk_to_dict(self) -> None:
        """Hunk serializes correctly."""
        hunk = Hunk(
            old_start=1, old_count=3, new_start=1, new_count=4,
            lines=[DiffLine(content="hello", line_type="add", new_line_no=1)],
        )
        result = hunk.to_dict()
        assert result["old_start"] == 1
        assert result["lines"][0]["content"] == "hello"
        assert result["lines"][0]["type"] == "add"

    def test_file_diff_to_dict(self) -> None:
        """FileDiff serializes correctly."""
        fd = FileDiff(
            filename="test.py",
            old_filename="old.py",
            status="renamed",
            hunks=[Hunk(old_start=1, old_count=1, new_start=1, new_count=1)],
        )
        result = fd.to_dict()
        assert result["filename"] == "test.py"
        assert result["old_filename"] == "old.py"
        assert result["status"] == "renamed"
        assert len(result["hunks"]) == 1

    def test_commit_to_dict(self) -> None:
        """Commit serializes correctly."""
        c = Commit(sha="abc", message="fix", author="me", date="2025-01-01")
        result = c.to_dict()
        assert result["sha"] == "abc"
        assert result["message"] == "fix"


class TestEdgeCases:
    """Edge case tests for complete coverage."""

    def test_rename_with_old_filename_set(self) -> None:
        """Covers branch where old_filename is already set from rename."""
        # Rename diff where --- line comes after rename headers
        diff_text = """diff --git a/old.py b/new.py
rename from old.py
rename to new.py
--- a/old.py
+++ b/new.py
@@ -1 +1 @@
-old
+new
"""
        files = _parse_unified_diff(diff_text)
        assert len(files) == 1
        assert files[0].old_filename == "old.py"
        assert files[0].status == "renamed"

    def test_multi_file_first_has_no_hunk(self) -> None:
        """First file has no hunk (e.g., binary) then second file starts."""
        diff_text = """diff --git a/image.png b/image.png
Binary files a/image.png and b/image.png differ
diff --git a/code.py b/code.py
--- a/code.py
+++ b/code.py
@@ -1 +1 @@
-old
+new
"""
        files = _parse_unified_diff(diff_text)
        assert len(files) == 2
        assert files[0].is_binary is True
        assert files[1].filename == "code.py"
        assert len(files[1].hunks) == 1

    def test_hunk_header_with_only_old_range(self) -> None:
        """Hunk header parsing when only old range part is found."""
        hunk = _parse_hunk_header("@@ -5,3 @@ context")
        assert hunk.old_start == 5
        assert hunk.old_count == 3
        assert hunk.new_start == 0
        assert hunk.new_count == 0

    def test_hunk_header_with_extra_tokens(self) -> None:
        """Hunk header with tokens that don't start with - or +."""
        # Simulate a malformed range string with unknown token
        hunk = _parse_hunk_header("@@ -1,2 extra +3,4 @@")
        assert hunk.old_start == 1
        assert hunk.new_start == 3


    def test_multiple_hunks_in_one_file(self) -> None:
        """File with multiple hunks appends each correctly."""
        diff_text = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 line1
+added
 line2
 line3
@@ -10,3 +11,3 @@
 line10
-old11
+new11
 line12
"""
        files = _parse_unified_diff(diff_text)
        assert len(files) == 1
        assert len(files[0].hunks) == 2
        assert files[0].hunks[0].old_start == 1
        assert files[0].hunks[1].old_start == 10
