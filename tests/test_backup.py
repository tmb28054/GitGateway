"""Tests for gitgateway.backup — archive, list, and restore.

Uses moto-mocked S3 (repos, lfs, backups buckets) + DynamoDB, plus real
git repositories (the `git` binary is required for bundling) so the
archive/restore round-trips are exercised end to end.
"""

from __future__ import annotations

import subprocess  # nosec B404 - building fixture git repos
from datetime import date
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from gitgateway import backup, db
from gitgateway.lfs import _lfs_s3_key
from gitgateway.models import Group, Repo

REPO_BUCKET = "gitgateway-repos"
LFS_BUCKET = "gitgateway-lfs"
BACKUP_BUCKET = "gitgateway-backups"


def _git(cwd: Path, *args: str) -> None:
    """Run a git command in cwd (fixture helper)."""
    env = {
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.com",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.com",
        "HOME": str(cwd),
    }
    subprocess.run(  # nosec B603 B607
        ["git", *args], cwd=str(cwd), check=True,
        capture_output=True, env=env,
    )


def _make_bare_repo_in_s3(
    s3, tmp_path: Path, group: str, repo: str, filename: str = "a.txt",
) -> None:
    """Create a real bare repo with one commit and sync it to S3."""
    from gitgateway import storage

    work = tmp_path / f"work-{group}-{repo}"
    work.mkdir(parents=True)
    _git(work, "init", "-q", "-b", "main")
    (work / filename).write_text("hello\n")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "tag", "v1")

    bare = tmp_path / f"{group}-{repo}.git"
    subprocess.run(  # nosec B603 B607
        ["git", "clone", "-q", "--bare", str(work), str(bare)],
        check=True, capture_output=True,
    )
    storage.sync_repo_to_s3(
        bare, group, repo, bucket=REPO_BUCKET, s3_client=s3,
    )


def _make_empty_bare_repo_in_s3(s3, tmp_path: Path, group: str,
                                repo: str) -> None:
    """Create a bare repo with no commits/refs and sync it to S3."""
    from gitgateway import storage

    bare = tmp_path / f"{group}-{repo}-empty.git"
    subprocess.run(  # nosec B603 B607
        ["git", "init", "-q", "--bare", "-b", "main", str(bare)],
        check=True, capture_output=True,
    )
    storage.sync_repo_to_s3(
        bare, group, repo, bucket=REPO_BUCKET, s3_client=s3,
    )


@pytest.fixture
def aws():
    """Provision the three S3 buckets + the repos DynamoDB table."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        for bucket in (REPO_BUCKET, LFS_BUCKET, BACKUP_BUCKET):
            s3.create_bucket(Bucket=bucket)
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="gitgateway-repos",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield {"s3": s3, "ddb": ddb}


class TestTiersForDate:
    """Tests for tiers_for_date."""

    def test_weekday_is_daily_only(self) -> None:
        """A mid-week date writes only the daily tier."""
        # 2026-09-09 is a Wednesday.
        assert backup.tiers_for_date(date(2026, 9, 9)) == ["daily"]

    def test_sunday_adds_weekly(self) -> None:
        """A Sunday adds the weekly tier."""
        # 2026-09-06 is a Sunday.
        tiers = backup.tiers_for_date(date(2026, 9, 6))
        assert tiers == ["daily", "weekly"]

    def test_first_of_month_adds_monthly(self) -> None:
        """The 1st of the month adds the monthly tier."""
        # 2026-09-01 is a Tuesday, 1st of month.
        tiers = backup.tiers_for_date(date(2026, 9, 1))
        assert tiers == ["daily", "monthly"]

    def test_sunday_first_adds_both(self) -> None:
        """A date that is both Sunday and the 1st adds weekly + monthly."""
        # 2026-11-01 is a Sunday, 1st of month.
        tiers = backup.tiers_for_date(date(2026, 11, 1))
        assert tiers == ["daily", "weekly", "monthly"]


class TestCreateBundle:
    """Tests for the _create_bundle helper's edge cases."""

    def test_no_refs_raises(self, tmp_path) -> None:
        """A repo with no refs raises EmptyRepoError (a BackupError)."""
        from dulwich.repo import Repo as DulwichRepo

        bare = tmp_path / "empty.git"
        DulwichRepo.init_bare(str(bare), mkdir=True)
        with pytest.raises(backup.EmptyRepoError, match="no refs"):
            backup._create_bundle(bare, tmp_path / "out.bundle")

    def test_git_bundle_failure_raises(self, tmp_path) -> None:
        """A git bundle subprocess failure raises BackupError."""
        from unittest.mock import patch

        from dulwich.objects import Blob, Commit, Tree
        from dulwich.repo import Repo as DulwichRepo

        bare = tmp_path / "r.git"
        repo = DulwichRepo.init_bare(str(bare), mkdir=True)
        blob = Blob.from_string(b"x\n")
        tree = Tree()
        tree.add(b"f", 0o100644, blob.id)
        commit = Commit()
        commit.tree = tree.id
        commit.author = commit.committer = b"T <t@e.com>"
        commit.commit_time = commit.author_time = 1_700_000_000
        commit.commit_timezone = commit.author_timezone = 0
        commit.encoding = b"UTF-8"
        commit.message = b"m"
        for obj in (blob, tree, commit):
            repo.object_store.add_object(obj)
        repo.refs[b"refs/heads/main"] = commit.id

        with patch(
            "gitgateway.backup.subprocess.run",
            side_effect=FileNotFoundError("git missing"),
        ):
            with pytest.raises(backup.BackupError, match="git bundle failed"):
                backup._create_bundle(bare, tmp_path / "out.bundle")


class TestArchiveRepo:
    """Tests for archive_repo."""

    def test_archives_bundle_and_refs(self, aws, tmp_path) -> None:
        """Archives a repo's bundle and records its refs."""
        _make_bare_repo_in_s3(aws["s3"], tmp_path, "team", "app")
        result = backup.archive_repo(
            "team", "app", ["daily"], "2026-09-09",
            repo_bucket=REPO_BUCKET, lfs_bucket=LFS_BUCKET,
            backup_bucket=BACKUP_BUCKET,
            local_base=tmp_path / "arch", s3_client=aws["s3"],
        )
        assert result.status == "ok"
        assert result.bundle_bytes > 0
        assert "refs/heads/main" in result.refs
        assert "refs/tags/v1" in result.refs
        # Bundle object exists in the backups bucket.
        aws["s3"].head_object(
            Bucket=BACKUP_BUCKET,
            Key="daily/2026-09-09/team/app.bundle",
        )

    def test_archives_lfs_objects(self, aws, tmp_path) -> None:
        """Copies a repo's LFS objects into the snapshot."""
        _make_bare_repo_in_s3(aws["s3"], tmp_path, "team", "app")
        oid = "a" * 64
        aws["s3"].put_object(
            Bucket=LFS_BUCKET,
            Key=_lfs_s3_key("team", "app", oid),
            Body=b"lfs-content",
        )
        result = backup.archive_repo(
            "team", "app", ["daily"], "2026-09-09",
            repo_bucket=REPO_BUCKET, lfs_bucket=LFS_BUCKET,
            backup_bucket=BACKUP_BUCKET,
            local_base=tmp_path / "arch", s3_client=aws["s3"],
        )
        assert result.lfs_object_count == 1
        assert result.lfs_bytes == len(b"lfs-content")
        aws["s3"].head_object(
            Bucket=BACKUP_BUCKET,
            Key=f"daily/2026-09-09/team/app.lfs/{oid}",
        )

    def test_multiple_tiers(self, aws, tmp_path) -> None:
        """Writes the bundle into every requested tier."""
        _make_bare_repo_in_s3(aws["s3"], tmp_path, "team", "app")
        backup.archive_repo(
            "team", "app", ["daily", "weekly"], "2026-09-06",
            repo_bucket=REPO_BUCKET, lfs_bucket=LFS_BUCKET,
            backup_bucket=BACKUP_BUCKET,
            local_base=tmp_path / "arch", s3_client=aws["s3"],
        )
        for tier in ("daily", "weekly"):
            aws["s3"].head_object(
                Bucket=BACKUP_BUCKET,
                Key=f"{tier}/2026-09-06/team/app.bundle",
            )

    def test_missing_repo_returns_error(self, aws, tmp_path) -> None:
        """A repo missing from S3 yields an error result, not a raise."""
        result = backup.archive_repo(
            "team", "ghost", ["daily"], "2026-09-09",
            repo_bucket=REPO_BUCKET, lfs_bucket=LFS_BUCKET,
            backup_bucket=BACKUP_BUCKET,
            local_base=tmp_path / "arch", s3_client=aws["s3"],
        )
        assert result.status == "error"
        assert result.error

    def test_empty_repo_returns_empty_status(self, aws, tmp_path) -> None:
        """A repo with no refs is skipped with status 'empty', not error."""
        _make_empty_bare_repo_in_s3(aws["s3"], tmp_path, "team", "fresh")
        result = backup.archive_repo(
            "team", "fresh", ["daily"], "2026-09-09",
            repo_bucket=REPO_BUCKET, lfs_bucket=LFS_BUCKET,
            backup_bucket=BACKUP_BUCKET,
            local_base=tmp_path / "arch", s3_client=aws["s3"],
        )
        assert result.status == "empty"
        assert "no refs" in result.error
        # No bundle was written for an empty repo.
        with pytest.raises(Exception):
            aws["s3"].head_object(
                Bucket=BACKUP_BUCKET,
                Key="daily/2026-09-09/team/fresh.bundle",
            )


class TestArchiveAll:
    """Tests for archive_all."""

    def test_archives_all_repos_with_manifest(self, aws, tmp_path) -> None:
        """Archives every registered repo and writes a manifest."""
        db.create_group(Group(name="team"), aws["ddb"])
        db.create_repo(Repo(group="team", name="app"), aws["ddb"])
        db.create_repo(Repo(group="team", name="lib"), aws["ddb"])
        _make_bare_repo_in_s3(aws["s3"], tmp_path, "team", "app")
        _make_bare_repo_in_s3(aws["s3"], tmp_path, "team", "lib")

        manifest = backup.archive_all(
            today=date(2026, 9, 9),
            repo_bucket=REPO_BUCKET, lfs_bucket=LFS_BUCKET,
            backup_bucket=BACKUP_BUCKET, local_base=tmp_path / "arch",
            s3_client=aws["s3"], dynamodb_resource=aws["ddb"],
        )
        assert manifest["repo_count"] == 2
        assert manifest["ok_count"] == 2
        assert manifest["failed_count"] == 0
        # Manifest object is written.
        obj = aws["s3"].get_object(
            Bucket=BACKUP_BUCKET, Key="daily/2026-09-09/manifest.json",
        )
        assert b"team" in obj["Body"].read()

    def test_manifest_records_failures(self, aws, tmp_path) -> None:
        """A repo registered but missing from S3 is recorded as failed."""
        db.create_group(Group(name="team"), aws["ddb"])
        db.create_repo(Repo(group="team", name="ghost"), aws["ddb"])

        manifest = backup.archive_all(
            today=date(2026, 9, 9),
            repo_bucket=REPO_BUCKET, lfs_bucket=LFS_BUCKET,
            backup_bucket=BACKUP_BUCKET, local_base=tmp_path / "arch",
            s3_client=aws["s3"], dynamodb_resource=aws["ddb"],
        )
        assert manifest["failed_count"] == 1
        assert manifest["repos"][0]["status"] == "error"

    def test_manifest_records_empty(self, aws, tmp_path) -> None:
        """An empty repo is counted as 'empty', not 'failed'."""
        db.create_group(Group(name="team"), aws["ddb"])
        db.create_repo(Repo(group="team", name="fresh"), aws["ddb"])
        _make_empty_bare_repo_in_s3(aws["s3"], tmp_path, "team", "fresh")

        manifest = backup.archive_all(
            today=date(2026, 9, 9),
            repo_bucket=REPO_BUCKET, lfs_bucket=LFS_BUCKET,
            backup_bucket=BACKUP_BUCKET, local_base=tmp_path / "arch",
            s3_client=aws["s3"], dynamodb_resource=aws["ddb"],
        )
        assert manifest["failed_count"] == 0
        assert manifest["empty_count"] == 1
        assert manifest["repos"][0]["status"] == "empty"


class TestListAndReadManifest:
    """Tests for list_snapshots and read_manifest."""

    def test_list_snapshots(self, aws, tmp_path) -> None:
        """Lists snapshots across tiers, newest first."""
        db.create_group(Group(name="team"), aws["ddb"])
        db.create_repo(Repo(group="team", name="app"), aws["ddb"])
        _make_bare_repo_in_s3(aws["s3"], tmp_path, "team", "app")
        backup.archive_all(
            today=date(2026, 9, 6),  # Sunday -> daily + weekly
            backup_bucket=BACKUP_BUCKET, local_base=tmp_path / "a",
            s3_client=aws["s3"], dynamodb_resource=aws["ddb"],
        )
        snaps = backup.list_snapshots(
            backup_bucket=BACKUP_BUCKET, s3_client=aws["s3"],
        )
        tiers = {s["tier"] for s in snaps}
        assert tiers == {"daily", "weekly"}

    def test_read_manifest(self, aws, tmp_path) -> None:
        """Reads back a written manifest."""
        db.create_group(Group(name="team"), aws["ddb"])
        db.create_repo(Repo(group="team", name="app"), aws["ddb"])
        _make_bare_repo_in_s3(aws["s3"], tmp_path, "team", "app")
        backup.archive_all(
            today=date(2026, 9, 9),
            backup_bucket=BACKUP_BUCKET, local_base=tmp_path / "a",
            s3_client=aws["s3"], dynamodb_resource=aws["ddb"],
        )
        manifest = backup.read_manifest(
            "daily", "2026-09-09",
            backup_bucket=BACKUP_BUCKET, s3_client=aws["s3"],
        )
        assert manifest["snapshot"] == "2026-09-09"

    def test_read_missing_manifest_raises(self, aws) -> None:
        """A missing manifest raises BackupError."""
        with pytest.raises(backup.BackupError):
            backup.read_manifest(
                "daily", "1999-01-01",
                backup_bucket=BACKUP_BUCKET, s3_client=aws["s3"],
            )


class TestRestore:
    """Tests for restore_repo / restore_repos."""

    def _seed_snapshot(self, aws, tmp_path, with_lfs=False):
        """Create a repo, archive it, return the snapshot date."""
        db.create_group(Group(name="team"), aws["ddb"])
        db.create_repo(Repo(group="team", name="app"), aws["ddb"])
        _make_bare_repo_in_s3(aws["s3"], tmp_path, "team", "app")
        if with_lfs:
            aws["s3"].put_object(
                Bucket=LFS_BUCKET,
                Key=_lfs_s3_key("team", "app", "b" * 64),
                Body=b"lfsdata",
            )
        backup.archive_all(
            today=date(2026, 9, 9),
            repo_bucket=REPO_BUCKET, lfs_bucket=LFS_BUCKET,
            backup_bucket=BACKUP_BUCKET, local_base=tmp_path / "arch",
            s3_client=aws["s3"], dynamodb_resource=aws["ddb"],
        )
        return "2026-09-09"

    def test_restore_to_new_name(self, aws, tmp_path) -> None:
        """Restores into a new repo without touching the source."""
        snap = self._seed_snapshot(aws, tmp_path)
        new_name = backup.restore_repo(
            "daily", snap, "team", "app",
            target_name="app-restored",
            repo_bucket=REPO_BUCKET, lfs_bucket=LFS_BUCKET,
            backup_bucket=BACKUP_BUCKET, local_base=tmp_path / "r",
            s3_client=aws["s3"], dynamodb_resource=aws["ddb"],
        )
        assert new_name == "app-restored"
        # New repo registered in DynamoDB and present in S3.
        restored = db.get_repo("team", "app-restored", aws["ddb"])
        assert restored.name == "app-restored"
        assert __import__("gitgateway.storage", fromlist=["repo_exists"]) \
            .repo_exists("team", "app-restored", bucket=REPO_BUCKET,
                         s3_client=aws["s3"])
        # Source repo untouched.
        assert db.get_repo("team", "app", aws["ddb"]).name == "app"

    def test_restore_default_name(self, aws, tmp_path) -> None:
        """Without an override, the restore name is auto-generated."""
        snap = self._seed_snapshot(aws, tmp_path)
        new_name = backup.restore_repo(
            "daily", snap, "team", "app",
            repo_bucket=REPO_BUCKET, lfs_bucket=LFS_BUCKET,
            backup_bucket=BACKUP_BUCKET, local_base=tmp_path / "r",
            s3_client=aws["s3"], dynamodb_resource=aws["ddb"],
        )
        assert new_name.startswith("app-restore-")

    def test_restore_with_lfs(self, aws, tmp_path) -> None:
        """LFS objects are restored under the new repo's LFS prefix."""
        snap = self._seed_snapshot(aws, tmp_path, with_lfs=True)
        new_name = backup.restore_repo(
            "daily", snap, "team", "app", target_name="app-r",
            repo_bucket=REPO_BUCKET, lfs_bucket=LFS_BUCKET,
            backup_bucket=BACKUP_BUCKET, local_base=tmp_path / "r",
            s3_client=aws["s3"], dynamodb_resource=aws["ddb"],
        )
        aws["s3"].head_object(
            Bucket=LFS_BUCKET,
            Key=_lfs_s3_key("team", new_name, "b" * 64),
        )

    def test_restore_missing_bundle_raises(self, aws, tmp_path) -> None:
        """A missing bundle raises BackupError."""
        with pytest.raises(backup.BackupError):
            backup.restore_repo(
                "daily", "1999-01-01", "team", "nope",
                backup_bucket=BACKUP_BUCKET, local_base=tmp_path / "r",
                s3_client=aws["s3"], dynamodb_resource=aws["ddb"],
            )

    def test_restore_unbundle_failure_raises(self, aws, tmp_path) -> None:
        """A git unbundle subprocess failure raises BackupError."""
        from unittest.mock import patch
        snap = self._seed_snapshot(aws, tmp_path)
        boom = subprocess.CalledProcessError(1, ["git", "bundle", "unbundle"])
        with patch("gitgateway.backup.subprocess.run", side_effect=boom):
            with pytest.raises(backup.BackupError, match="unbundle failed"):
                backup.restore_repo(
                    "daily", snap, "team", "app", target_name="app-fail",
                    repo_bucket=REPO_BUCKET, lfs_bucket=LFS_BUCKET,
                    backup_bucket=BACKUP_BUCKET, local_base=tmp_path / "r",
                    s3_client=aws["s3"], dynamodb_resource=aws["ddb"],
                )

    def test_restore_creates_missing_group(self, aws, tmp_path) -> None:
        """Restore recreates the group if it no longer exists."""
        snap = self._seed_snapshot(aws, tmp_path)
        # Delete the group so restore must recreate it.
        db.delete_repo("team", "app", aws["ddb"])
        db.delete_group("team", aws["ddb"])
        new_name = backup.restore_repo(
            "daily", snap, "team", "app", target_name="app-r2",
            repo_bucket=REPO_BUCKET, lfs_bucket=LFS_BUCKET,
            backup_bucket=BACKUP_BUCKET, local_base=tmp_path / "r",
            s3_client=aws["s3"], dynamodb_resource=aws["ddb"],
        )
        assert db.get_group("team", aws["ddb"]).name == "team"
        assert new_name == "app-r2"

    def test_restore_repos_list(self, aws, tmp_path) -> None:
        """restore_repos handles an explicit list."""
        snap = self._seed_snapshot(aws, tmp_path)
        results = backup.restore_repos(
            "daily", snap,
            [{"group": "team", "repo": "app", "target_name": "app-x"}],
            repo_bucket=REPO_BUCKET, lfs_bucket=LFS_BUCKET,
            backup_bucket=BACKUP_BUCKET, local_base=tmp_path / "r",
            s3_client=aws["s3"], dynamodb_resource=aws["ddb"],
        )
        assert results[0]["status"] == "ok"
        assert results[0]["new_name"] == "app-x"

    def test_restore_repos_all(self, aws, tmp_path) -> None:
        """restore_repos "all" restores every ok repo in the manifest."""
        snap = self._seed_snapshot(aws, tmp_path)
        results = backup.restore_repos(
            "daily", snap, "all",
            repo_bucket=REPO_BUCKET, lfs_bucket=LFS_BUCKET,
            backup_bucket=BACKUP_BUCKET, local_base=tmp_path / "r",
            s3_client=aws["s3"], dynamodb_resource=aws["ddb"],
        )
        assert len(results) == 1
        assert results[0]["status"] == "ok"

    def test_restore_repos_records_error(self, aws, tmp_path) -> None:
        """A failing restore is captured as an error entry."""
        self._seed_snapshot(aws, tmp_path)
        results = backup.restore_repos(
            "daily", "2026-09-09",
            [{"group": "team", "repo": "missing"}],
            backup_bucket=BACKUP_BUCKET, local_base=tmp_path / "r",
            s3_client=aws["s3"], dynamodb_resource=aws["ddb"],
        )
        assert results[0]["status"] == "error"
