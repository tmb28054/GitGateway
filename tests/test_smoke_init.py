"""Smoke tests for gitgateway package initialization."""

from __future__ import annotations

import re

import pytest


@pytest.mark.smoke
class TestPackageInit:
    """Verify the package can be imported and has valid metadata."""

    def test_import_gitgateway(self) -> None:
        """Package can be imported without errors."""
        import gitgateway  # noqa: F401  pylint: disable=import-outside-toplevel

    def test_version_is_valid_semver(self) -> None:
        """Version string is a valid semantic version."""
        import gitgateway  # pylint: disable=import-outside-toplevel

        pattern = re.compile(r"^\d+\.\d+\.\d+$")
        assert pattern.match(gitgateway.__version__), (
            f"Version '{gitgateway.__version__}' is not valid semver"
        )

    def test_version_not_zero(self) -> None:
        """Version is not the fallback 0.0.0."""
        import gitgateway  # pylint: disable=import-outside-toplevel

        assert gitgateway.__version__ != "0.0.0"
