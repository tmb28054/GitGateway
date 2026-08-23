"""Tests for gitgateway.__version__ module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


class TestReadVersion:
    """Test version reading from CHANGELOG.md."""

    def test_reads_version_from_changelog(self) -> None:
        """Reads the first version entry from CHANGELOG.md."""
        from gitgateway.__version__ import _read_version  # pylint: disable=import-outside-toplevel

        version = _read_version()
        assert version == "0.1.0"

    def test_returns_fallback_when_no_changelog(self, tmp_path: Path) -> None:
        """Returns 0.0.0 when CHANGELOG.md does not exist."""
        from gitgateway.__version__ import _read_version  # pylint: disable=import-outside-toplevel

        fake_file = tmp_path / "gitgateway" / "__version__.py"
        fake_file.parent.mkdir(parents=True)
        fake_file.touch()

        with patch(
            "gitgateway.__version__.__file__",
            str(fake_file),
        ):
            result = _read_version()
        assert result == "0.0.0"

    def test_returns_fallback_when_no_version_pattern(
        self,
        tmp_path: Path,
    ) -> None:
        """Returns 0.0.0 when CHANGELOG.md has no version entry."""
        from gitgateway.__version__ import _read_version  # pylint: disable=import-outside-toplevel

        # Create a CHANGELOG.md with no version pattern
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text("# Changelog\n\nNo versions yet.\n")

        # Point __file__ to a location where parent is tmp_path
        fake_file = tmp_path / "gitgateway" / "__version__.py"
        fake_file.parent.mkdir(parents=True, exist_ok=True)
        fake_file.touch()

        # Move changelog to be at parent of fake_file's parent
        # i.e. tmp_path / "CHANGELOG.md" exists, and
        # fake_file.parent.parent == tmp_path
        with patch(
            "gitgateway.__version__.__file__",
            str(fake_file),
        ):
            result = _read_version()
        assert result == "0.0.0"
