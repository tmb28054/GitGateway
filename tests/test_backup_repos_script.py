"""Tests for the CodeBuild backup entry script (scripts/backup_repos.py).

These exercise the mode dispatch, env-var parsing, alerting, and failure
handling of the script without touching real AWS (all backup.* and boto3
calls are patched).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# The script lives under scripts/ (not an importable package), so load it
# by path into a module object we can patch against.
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backup_repos.py"
_spec = importlib.util.spec_from_file_location("backup_repos", _SCRIPT)
backup_repos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup_repos)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Clear backup-related env vars before each test."""
    for key in (
        "BACKUP_MODE", "REPO_BUCKET", "LFS_BUCKET", "BACKUP_BUCKET",
        "ALERTS_TOPIC_ARN", "RESTORE_TIER", "RESTORE_SNAPSHOT", "RESTORE_REPOS",
    ):
        monkeypatch.delenv(key, raising=False)


class TestParseRestoreRepos:
    """Tests for _parse_restore_repos."""

    def test_parses_all(self) -> None:
        """The literal string "all" round-trips through JSON."""
        assert backup_repos._parse_restore_repos('"all"') == "all"

    def test_parses_list(self) -> None:
        """A JSON list of repo specs is parsed."""
        raw = '[{"group": "team", "repo": "app"}]'
        assert backup_repos._parse_restore_repos(raw) == [
            {"group": "team", "repo": "app"},
        ]

    def test_empty_raises(self) -> None:
        """An empty value raises BackupError."""
        with pytest.raises(backup_repos.backup.BackupError):
            backup_repos._parse_restore_repos("")

    def test_invalid_json_raises(self) -> None:
        """Malformed JSON raises BackupError."""
        with pytest.raises(backup_repos.backup.BackupError):
            backup_repos._parse_restore_repos("{not json")


class TestPublishAlert:
    """Tests for _publish_alert."""

    def test_no_topic_skips(self) -> None:
        """With no topic ARN, no SNS client is created."""
        with patch.object(backup_repos, "boto3") as mock_boto3:
            backup_repos._publish_alert("", "subj", "msg")
        mock_boto3.client.assert_not_called()

    def test_publishes(self) -> None:
        """With a topic ARN, publish is called."""
        client = MagicMock()
        with patch.object(backup_repos, "boto3") as mock_boto3:
            mock_boto3.client.return_value = client
            backup_repos._publish_alert("arn:topic", "subj", "msg")
        client.publish.assert_called_once()

    def test_publish_error_is_swallowed(self) -> None:
        """An SNS failure must not raise (it would mask the real error)."""
        client = MagicMock()
        client.publish.side_effect = RuntimeError("boom")
        with patch.object(backup_repos, "boto3") as mock_boto3:
            mock_boto3.client.return_value = client
            backup_repos._publish_alert("arn:topic", "subj", "msg")


class TestMain:
    """Tests for main() mode dispatch and failure handling."""

    def test_archive_success(self, monkeypatch) -> None:
        """Archive mode returns 0 when all repos succeed."""
        monkeypatch.setenv("BACKUP_MODE", "archive")
        manifest = {"repos": [{"group": "t", "repo": "a", "status": "ok"}],
                    "tiers": ["daily"]}
        with patch.object(backup_repos.backup, "archive_all",
                          return_value=manifest) as m:
            assert backup_repos.main() == 0
        m.assert_called_once()

    def test_archive_default_mode(self) -> None:
        """No BACKUP_MODE defaults to archive."""
        with patch.object(backup_repos.backup, "archive_all",
                          return_value={"repos": [], "tiers": []}):
            assert backup_repos.main() == 0

    def test_archive_failure_alerts(self, monkeypatch) -> None:
        """A failed repo makes archive raise, return 1, and alert."""
        monkeypatch.setenv("BACKUP_MODE", "archive")
        monkeypatch.setenv("ALERTS_TOPIC_ARN", "arn:topic")
        manifest = {"repos": [{"group": "t", "repo": "a", "status": "error"}],
                    "tiers": ["daily"]}
        with patch.object(backup_repos.backup, "archive_all",
                          return_value=manifest), \
                patch.object(backup_repos, "_publish_alert") as alert:
            assert backup_repos.main() == 1
        alert.assert_called_once()

    def test_restore_success(self, monkeypatch) -> None:
        """Restore mode returns 0 when all restores succeed."""
        monkeypatch.setenv("BACKUP_MODE", "restore")
        monkeypatch.setenv("RESTORE_TIER", "daily")
        monkeypatch.setenv("RESTORE_SNAPSHOT", "2026-09-09")
        monkeypatch.setenv("RESTORE_REPOS", '[{"group": "t", "repo": "a"}]')
        results = [{"group": "t", "repo": "a", "status": "ok",
                    "new_name": "a-r"}]
        with patch.object(backup_repos.backup, "restore_repos",
                          return_value=results) as m:
            assert backup_repos.main() == 0
        m.assert_called_once()

    def test_restore_missing_params_fails(self, monkeypatch) -> None:
        """Restore without tier/snapshot returns 1."""
        monkeypatch.setenv("BACKUP_MODE", "restore")
        with patch.object(backup_repos, "_publish_alert"):
            assert backup_repos.main() == 1

    def test_restore_failure_returns_1(self, monkeypatch) -> None:
        """A failed restore entry makes main return 1."""
        monkeypatch.setenv("BACKUP_MODE", "restore")
        monkeypatch.setenv("RESTORE_TIER", "daily")
        monkeypatch.setenv("RESTORE_SNAPSHOT", "2026-09-09")
        monkeypatch.setenv("RESTORE_REPOS", '"all"')
        results = [{"group": "t", "repo": "a", "status": "error",
                    "error": "nope"}]
        with patch.object(backup_repos.backup, "restore_repos",
                          return_value=results), \
                patch.object(backup_repos, "_publish_alert"):
            assert backup_repos.main() == 1

    def test_unknown_mode_fails(self, monkeypatch) -> None:
        """An unrecognised BACKUP_MODE returns 1."""
        monkeypatch.setenv("BACKUP_MODE", "bogus")
        with patch.object(backup_repos, "_publish_alert"):
            assert backup_repos.main() == 1

    @pytest.mark.smoke
    def test_smoke_archive_happy_path(self) -> None:
        """Smoke: default archive mode with an empty repo set succeeds."""
        with patch.object(backup_repos.backup, "archive_all",
                          return_value={"repos": [], "tiers": ["daily"]}):
            assert backup_repos.main() == 0
