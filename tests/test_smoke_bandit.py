"""Smoke test: bandit passes on gitgateway package."""

from __future__ import annotations

import subprocess

import pytest


@pytest.mark.smoke
class TestBandit:
    """Run bandit on the gitgateway package."""

    def test_bandit_passes(self) -> None:
        """Bandit reports no security issues."""
        result = subprocess.run(
            ["bandit", "-r", "gitgateway", "-q"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"bandit failed:\n{result.stdout}\n{result.stderr}"
        )
