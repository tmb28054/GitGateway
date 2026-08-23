"""Smoke test: pylint passes on gitgateway package."""

from __future__ import annotations

import subprocess

import pytest


@pytest.mark.smoke
class TestPylint:
    """Run pylint on the gitgateway package."""

    def test_pylint_passes(self) -> None:
        """Pylint reports no errors or warnings."""
        result = subprocess.run(
            ["pylint", "gitgateway", "--fail-under=10"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"pylint failed:\n{result.stdout}\n{result.stderr}"
        )
