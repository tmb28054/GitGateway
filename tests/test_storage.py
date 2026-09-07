"""Tests for gitgateway.storage module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from gitgateway.storage import (
    ConflictError,
    StorageError,
    _list_s3_objects,
    _remove_tree,
    _repo_s3_prefix,
    _sync_local_to_s3,
    delete_repo_storage,
    fork_repo,
    init_bare_repo,
    repo_exists,
    sync_repo_to_local,
    sync_repo_to_s3,
    _get_s3_client,
)


BUCKET = "test-bucket"


@pytest.fixture
def s3():
    """Create a mocked S3 client with a test bucket."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


class TestRepoS3Prefix:
    """Tests for _repo_s3_prefix helper."""

    def test_basic_prefix(self) -> None:
        """Builds correct S3 prefix."""
        assert _repo_s3_prefix("team", "app") == "repos/team/app.git/"

    def test_prefix_with_special_chars(self) -> None:
        """Handles group names that might have special chars."""
        assert _repo_s3_prefix("my-team", "my-repo") == (
            "repos/my-team/my-repo.git/"
        )


class TestGetS3Client:
    """Tests for _get_s3_client helper."""

    @mock_aws
    def test_default_client(self) -> None:
        """Creates default boto3 client when none provided."""
        client = _get_s3_client()
        assert client is not None

    def test_returns_provided_client(self, s3) -> None:
        """Returns the provided client."""
        result = _get_s3_client(s3)
        assert result is s3


class TestInitBareRepo:
    """Tests for init_bare_repo function."""

    def test_creates_bare_repo(self, s3, tmp_path: Path) -> None:
        """Initializes a bare repo and uploads to S3."""
        local_path = init_bare_repo(
            "team", "app", bucket=BUCKET,
            local_base=tmp_path, s3_client=s3,
        )

        assert local_path.exists()
        assert (local_path / "HEAD").exists()

        # Verify S3 has objects
        objects = _list_s3_objects(BUCKET, "repos/team/app.git/", s3)
        assert len(objects) > 0

    def test_overwrites_existing_local(self, s3, tmp_path: Path) -> None:
        """Cleans up existing local directory before init."""
        existing = tmp_path / "team" / "app.git"
        existing.mkdir(parents=True)
        (existing / "stale_file").write_text("old")

        local_path = init_bare_repo(
            "team", "app", bucket=BUCKET,
            local_base=tmp_path, s3_client=s3,
        )

        assert not (local_path / "stale_file").exists()
        assert (local_path / "HEAD").exists()

    def test_raises_on_git_failure(self, s3, tmp_path: Path) -> None:
        """Raises StorageError when git init fails."""
        with patch(
            "dulwich.repo.Repo.init_bare",
            side_effect=OSError("git not found"),
        ):
            with pytest.raises(StorageError, match="Failed to initialize"):
                init_bare_repo(
                    "team", "app", bucket=BUCKET,
                    local_base=tmp_path, s3_client=s3,
                )


class TestSyncRepoToLocal:
    """Tests for sync_repo_to_local function."""

    def test_downloads_repo(self, s3, tmp_path: Path) -> None:
        """Downloads all repo files from S3."""
        # First create a repo
        init_bare_repo(
            "team", "app", bucket=BUCKET,
            local_base=tmp_path, s3_client=s3,
        )

        # Now sync to a different location
        sync_base = tmp_path / "sync"
        local_path = sync_repo_to_local(
            "team", "app", bucket=BUCKET,
            local_base=sync_base, s3_client=s3,
        )

        assert local_path.exists()
        assert (local_path / "HEAD").exists()

    def test_raises_on_missing_repo(self, s3, tmp_path: Path) -> None:
        """Raises StorageError when repo doesn't exist in S3."""
        with pytest.raises(StorageError, match="not found in S3"):
            sync_repo_to_local(
                "ghost", "repo", bucket=BUCKET,
                local_base=tmp_path, s3_client=s3,
            )

    def test_cleans_existing_local(self, s3, tmp_path: Path) -> None:
        """Removes existing local copy before sync."""
        # Create repo in S3
        init_base = tmp_path / "init"
        init_bare_repo(
            "team", "app", bucket=BUCKET,
            local_base=init_base, s3_client=s3,
        )

        # Create stale local dir
        sync_base = tmp_path / "sync"
        stale_dir = sync_base / "team" / "app.git"
        stale_dir.mkdir(parents=True)
        (stale_dir / "stale").write_text("old")

        local_path = sync_repo_to_local(
            "team", "app", bucket=BUCKET,
            local_base=sync_base, s3_client=s3,
        )

        assert not (local_path / "stale").exists()
        assert (local_path / "HEAD").exists()


class TestSyncRepoToS3:
    """Tests for sync_repo_to_s3 function."""

    def test_uploads_local_repo(self, s3, tmp_path: Path) -> None:
        """Uploads local files to S3."""
        # Create a fake repo structure
        repo_path = tmp_path / "team" / "app.git"
        repo_path.mkdir(parents=True)
        (repo_path / "HEAD").write_text("ref: refs/heads/main\n")
        refs_dir = repo_path / "refs" / "heads"
        refs_dir.mkdir(parents=True)
        (refs_dir / "main").write_text("abc123\n")

        sync_repo_to_s3(
            repo_path, "team", "app",
            bucket=BUCKET, s3_client=s3,
        )

        objects = _list_s3_objects(BUCKET, "repos/team/app.git/", s3)
        keys = {k.split("repos/team/app.git/")[1] for k in objects}
        assert "HEAD" in keys
        assert "refs/heads/main" in keys

    def test_raises_on_missing_local(self, s3, tmp_path: Path) -> None:
        """Raises StorageError when local path doesn't exist."""
        with pytest.raises(StorageError, match="does not exist"):
            sync_repo_to_s3(
                tmp_path / "nonexistent", "team", "app",
                bucket=BUCKET, s3_client=s3,
            )

    def test_deletes_stale_s3_keys(self, s3, tmp_path: Path) -> None:
        """Objects removed locally are deleted from S3 on re-sync."""
        repo_path = tmp_path / "team" / "app.git"
        refs_dir = repo_path / "refs" / "heads"
        refs_dir.mkdir(parents=True)
        (repo_path / "HEAD").write_text("ref: refs/heads/main\n")
        (refs_dir / "main").write_text("abc123\n")
        (refs_dir / "feature").write_text("def456\n")

        # First sync uploads both refs.
        sync_repo_to_s3(
            repo_path, "team", "app", bucket=BUCKET, s3_client=s3,
        )

        # Remove one ref locally, then re-sync.
        (refs_dir / "feature").unlink()
        sync_repo_to_s3(
            repo_path, "team", "app", bucket=BUCKET, s3_client=s3,
        )

        objects = _list_s3_objects(BUCKET, "repos/team/app.git/", s3)
        keys = {k.split("repos/team/app.git/")[1] for k in objects}
        assert "refs/heads/main" in keys
        assert "refs/heads/feature" not in keys


class TestDeleteRepoStorage:
    """Tests for delete_repo_storage function."""

    def test_deletes_all_objects(self, s3, tmp_path: Path) -> None:
        """Removes all S3 objects for a repo."""
        init_bare_repo(
            "team", "app", bucket=BUCKET,
            local_base=tmp_path, s3_client=s3,
        )

        # Verify objects exist
        assert repo_exists("team", "app", bucket=BUCKET, s3_client=s3)

        delete_repo_storage("team", "app", bucket=BUCKET, s3_client=s3)

        # Verify objects gone
        assert not repo_exists("team", "app", bucket=BUCKET, s3_client=s3)

    def test_no_error_on_missing_repo(self, s3) -> None:
        """Doesn't raise when repo doesn't exist."""
        delete_repo_storage(
            "ghost", "repo", bucket=BUCKET, s3_client=s3,
        )


class TestRepoExists:
    """Tests for repo_exists function."""

    def test_returns_true_for_existing(self, s3, tmp_path: Path) -> None:
        """Returns True when repo has objects in S3."""
        init_bare_repo(
            "team", "app", bucket=BUCKET,
            local_base=tmp_path, s3_client=s3,
        )
        assert repo_exists("team", "app", bucket=BUCKET, s3_client=s3)

    def test_returns_false_for_missing(self, s3) -> None:
        """Returns False when repo has no objects."""
        assert not repo_exists("ghost", "repo", bucket=BUCKET, s3_client=s3)


class TestForkRepo:
    """Tests for fork_repo function."""

    def test_copies_all_objects(self, s3, tmp_path: Path) -> None:
        """Copies all S3 objects from source to target."""
        init_bare_repo(
            "team", "original", bucket=BUCKET,
            local_base=tmp_path, s3_client=s3,
        )

        fork_repo(
            "team", "original", "user", "forked",
            bucket=BUCKET, s3_client=s3,
        )

        # Verify fork exists
        assert repo_exists("user", "forked", bucket=BUCKET, s3_client=s3)

        # Verify fork has same number of objects
        source_objs = _list_s3_objects(
            BUCKET, "repos/team/original.git/", s3,
        )
        target_objs = _list_s3_objects(
            BUCKET, "repos/user/forked.git/", s3,
        )
        assert len(target_objs) == len(source_objs)

    def test_raises_on_missing_source(self, s3) -> None:
        """Raises StorageError when source doesn't exist."""
        with pytest.raises(StorageError, match="not found"):
            fork_repo(
                "ghost", "repo", "user", "fork",
                bucket=BUCKET, s3_client=s3,
            )


class TestRemoveTree:
    """Tests for _remove_tree helper."""

    def test_removes_directory(self, tmp_path: Path) -> None:
        """Removes a directory tree."""
        target = tmp_path / "to_remove"
        target.mkdir()
        (target / "file.txt").write_text("content")
        sub = target / "sub"
        sub.mkdir()
        (sub / "nested.txt").write_text("deep")

        _remove_tree(target)
        assert not target.exists()

    def test_no_error_on_nonexistent(self, tmp_path: Path) -> None:
        """Doesn't raise when path doesn't exist."""
        _remove_tree(tmp_path / "ghost")


class TestListS3Objects:
    """Tests for _list_s3_objects helper."""

    def test_lists_objects(self, s3) -> None:
        """Lists objects under a prefix."""
        s3.put_object(Bucket=BUCKET, Key="test/a.txt", Body=b"a")
        s3.put_object(Bucket=BUCKET, Key="test/b.txt", Body=b"b")
        s3.put_object(Bucket=BUCKET, Key="other/c.txt", Body=b"c")

        result = _list_s3_objects(BUCKET, "test/", s3)
        assert len(result) == 2
        assert "test/a.txt" in result
        assert "test/b.txt" in result

    def test_empty_result(self, s3) -> None:
        """Returns empty list when no objects match."""
        result = _list_s3_objects(BUCKET, "nonexistent/", s3)
        assert result == []


class TestSyncLocalToS3:
    """Tests for _sync_local_to_s3 helper."""

    def test_uploads_files(self, s3, tmp_path: Path) -> None:
        """Uploads all files maintaining structure."""
        local = tmp_path / "repo"
        local.mkdir()
        (local / "HEAD").write_text("ref: refs/heads/main\n")
        sub = local / "objects" / "pack"
        sub.mkdir(parents=True)
        (sub / "pack-abc.pack").write_bytes(b"\x00" * 10)

        _sync_local_to_s3(local, "grp", "repo", BUCKET, s3)

        objects = _list_s3_objects(BUCKET, "repos/grp/repo.git/", s3)
        keys = {k.split("repos/grp/repo.git/")[1] for k in objects}
        assert "HEAD" in keys
        assert "objects/pack/pack-abc.pack" in keys


class TestEdgeCases:
    """Tests for edge cases in sync operations."""

    def test_sync_to_local_skips_prefix_only_keys(
        self, s3, tmp_path: Path,
    ) -> None:
        """Skips S3 keys that match the prefix exactly (folder markers)."""
        # Create a folder marker (key = prefix itself)
        prefix = "repos/team/app.git/"
        s3.put_object(Bucket=BUCKET, Key=prefix, Body=b"")
        # Also add a real file
        s3.put_object(
            Bucket=BUCKET,
            Key=f"{prefix}HEAD",
            Body=b"ref: refs/heads/main\n",
        )

        local_path = sync_repo_to_local(
            "team", "app", bucket=BUCKET,
            local_base=tmp_path, s3_client=s3,
        )

        assert (local_path / "HEAD").exists()
        # Should only have files from real keys, not the prefix marker
        files = list(local_path.rglob("*"))
        file_names = [f.name for f in files if f.is_file()]
        assert "HEAD" in file_names

    def test_fork_skips_prefix_only_keys(self, s3) -> None:
        """Fork skips S3 keys that match prefix exactly."""
        source_prefix = "repos/team/src.git/"
        s3.put_object(Bucket=BUCKET, Key=source_prefix, Body=b"")
        s3.put_object(
            Bucket=BUCKET,
            Key=f"{source_prefix}HEAD",
            Body=b"ref: refs/heads/main\n",
        )

        fork_repo(
            "team", "src", "user", "fork",
            bucket=BUCKET, s3_client=s3,
        )

        target_objs = _list_s3_objects(
            BUCKET, "repos/user/fork.git/", s3,
        )
        # Should only have HEAD, not the prefix-only marker
        keys = [k.split("repos/user/fork.git/")[1] for k in target_objs]
        assert "HEAD" in keys
        assert "" not in keys
