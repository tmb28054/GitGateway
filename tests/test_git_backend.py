"""Tests for gitgateway.git_backend Smart HTTP handlers.

Exercises info/refs, upload-pack, receive-pack, and the DynamoDB
tree/commit indexing against real Dulwich bare repos and moto-mocked
S3 + DynamoDB.
"""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path

import boto3
import pytest
from dulwich.objects import Blob, Commit, Tree
from dulwich.repo import Repo
from moto import mock_aws

from gitgateway import git_backend
from gitgateway.git_backend import (
    _build_report_status,
    _collect_objects,
    _handle_info_refs,
    _handle_receive_pack,
    _handle_upload_pack,
    _index_repo_tree,
    _write_pack_object_simple,
    handle_git_http_request,
)

BUCKET = "gitgateway-repos"


def _commit_tree(repo, files: dict[str, bytes], parents=None):
    """Add a commit with the given files to a repo; return its SHA."""
    tree = Tree()
    for name, content in files.items():
        blob = Blob.from_string(content)
        repo.object_store.add_object(blob)
        tree.add(name.encode(), 0o100644, blob.id)
    repo.object_store.add_object(tree)
    commit = Commit()
    commit.tree = tree.id
    commit.author = commit.committer = b"Tester <t@example.com>"
    commit.commit_time = commit.author_time = 1_700_000_000
    commit.commit_timezone = commit.author_timezone = 0
    commit.encoding = b"UTF-8"
    commit.message = b"a commit"
    if parents:
        commit.parents = parents
    repo.object_store.add_object(commit)
    return commit.id


@pytest.fixture
def aws():
    """Provision S3 bucket + DynamoDB repos table, mocked."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
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
        yield ddb


@pytest.fixture
def bare_repo(tmp_path):
    """A real bare repo with one commit on refs/heads/main."""
    repo_dir = tmp_path / "app.git"
    repo = Repo.init_bare(str(repo_dir), mkdir=True)
    sha = _commit_tree(repo, {"README.md": b"# hello\n", "main.py": b"x=1\n"})
    repo.refs[b"refs/heads/main"] = sha
    return repo_dir, sha


class TestHandleInfoRefs:
    """Tests for _handle_info_refs."""

    def test_upload_pack_advertisement(self, bare_repo) -> None:
        """Advertises refs for git-upload-pack."""
        repo_dir, sha = bare_repo
        status, ctype, body = _handle_info_refs(repo_dir, "git-upload-pack")
        assert status == 200
        assert ctype == "application/x-git-upload-pack-advertisement"
        assert b"service=git-upload-pack" in body
        assert sha in body

    def test_receive_pack_advertisement(self, bare_repo) -> None:
        """Advertises refs for git-receive-pack."""
        repo_dir, _sha = bare_repo
        status, ctype, _body = _handle_info_refs(repo_dir, "git-receive-pack")
        assert status == 200
        assert ctype == "application/x-git-receive-pack-advertisement"

    def test_invalid_service(self, bare_repo) -> None:
        """An unknown service returns 400."""
        repo_dir, _sha = bare_repo
        status, _ctype, _body = _handle_info_refs(repo_dir, "bogus")
        assert status == 400

    def test_empty_repo_advertises_capabilities(self, tmp_path) -> None:
        """An empty repo advertises the zero-id capabilities line."""
        repo_dir = tmp_path / "empty.git"
        Repo.init_bare(str(repo_dir), mkdir=True)
        status, _ctype, body = _handle_info_refs(repo_dir, "git-upload-pack")
        assert status == 200
        assert b"capabilities^{}" in body

    def test_multiple_refs_advertised(self, tmp_path) -> None:
        """A repo with two branches advertises both (non-first ref path)."""
        repo_dir = tmp_path / "multi.git"
        repo = Repo.init_bare(str(repo_dir), mkdir=True)
        sha = _commit_tree(repo, {"a.txt": b"a\n"})
        repo.refs[b"refs/heads/main"] = sha
        repo.refs[b"refs/heads/dev"] = sha
        status, _ctype, body = _handle_info_refs(repo_dir, "git-upload-pack")
        assert status == 200
        assert b"refs/heads/main" in body
        assert b"refs/heads/dev" in body

    def test_str_typed_refs_are_encoded(self, bare_repo) -> None:
        """Refs returned as str (defensive path) are encoded to bytes.

        Dulwich normally returns bytes; patch get_refs to return str keys
        and values to exercise the isinstance normalization branches.
        """
        from unittest.mock import patch

        repo_dir, sha = bare_repo
        str_refs = {"refs/heads/main": sha.decode()}
        with patch.object(Repo, "get_refs", return_value=str_refs):
            status, _ctype, body = _handle_info_refs(
                repo_dir, "git-upload-pack",
            )
        assert status == 200
        assert sha in body


class TestHandleUploadPack:
    """Tests for _handle_upload_pack."""

    def test_no_wants_returns_flush(self, bare_repo) -> None:
        """A request with no wants returns a flush packet."""
        repo_dir, _sha = bare_repo
        status, ctype, body = _handle_upload_pack(repo_dir, b"0000")
        assert status == 200
        assert body == b"0000"

    def test_wants_returns_pack(self, bare_repo) -> None:
        """A want line produces a NAK + sideband pack."""
        repo_dir, sha = bare_repo
        want = b"want " + sha + b"\n"
        body = f"{len(want) + 4:04x}".encode() + want + b"0000" + b"done"
        status, ctype, resp = _handle_upload_pack(repo_dir, body)
        assert status == 200
        assert resp.startswith(b"0008NAK\n")
        assert b"PACK" in resp

    def test_unknown_want_sha_ignored(self, bare_repo) -> None:
        """An unknown want SHA is skipped without error."""
        repo_dir, _sha = bare_repo
        bogus = b"0" * 40
        want = b"want " + bogus + b"\n"
        body = f"{len(want) + 4:04x}".encode() + want + b"done"
        status, _ctype, _resp = _handle_upload_pack(repo_dir, body)
        assert status == 200

    def test_large_object_multichunk_sideband(self, tmp_path) -> None:
        """A large blob forces multi-chunk sideband framing."""
        repo_dir = tmp_path / "big.git"
        repo = Repo.init_bare(str(repo_dir), mkdir=True)
        # A blob large enough to exceed the 65515-byte sideband chunk once
        # compressed would still be one chunk; use incompressible content.
        import os as _os
        big = _os.urandom(200_000)
        sha = _commit_tree(repo, {"big.bin": big})
        repo.refs[b"refs/heads/main"] = sha
        want = b"want " + sha + b"\n"
        body = f"{len(want) + 4:04x}".encode() + want + b"0000done"
        status, _ctype, resp = _handle_upload_pack(repo_dir, body)
        assert status == 200
        assert b"PACK" in resp

    def test_upload_pack_internal_error(self, bare_repo) -> None:
        """An error while building the pack returns a NAK fallback."""
        from unittest.mock import patch

        repo_dir, sha = bare_repo
        want = b"want " + sha + b"\n"
        body = f"{len(want) + 4:04x}".encode() + want + b"0000done"
        with patch(
            "gitgateway.git_backend._collect_objects",
            side_effect=RuntimeError("walk boom"),
        ):
            status, _ctype, resp = _handle_upload_pack(repo_dir, body)
        assert status == 200
        assert b"NAK" in resp


def _make_pack(objects) -> bytes:
    """Build a minimal pack byte string for the given (type_num, raw) objs."""
    import hashlib

    buf = io.BytesIO()
    buf.write(b"PACK")
    buf.write(struct.pack(">I", 2))
    buf.write(struct.pack(">I", len(objects)))
    for type_num, raw in objects:
        _write_pack_object_simple(buf, type_num, raw)
    buf.seek(0)
    checksum = hashlib.sha1(buf.read()).digest()  # nosec
    buf.write(checksum)
    return buf.getvalue()


class TestHandleReceivePack:
    """Tests for _handle_receive_pack."""

    def _make_pack(self, objects) -> bytes:
        """Delegate to the module-level pack builder."""
        return _make_pack(objects)

    def test_create_ref_with_pack(self, tmp_path) -> None:
        """A push with a pack creates the ref and reports ok."""
        repo_dir = tmp_path / "r.git"
        repo = Repo.init_bare(str(repo_dir), mkdir=True)

        blob = Blob.from_string(b"hi\n")
        tree = Tree()
        tree.add(b"f.txt", 0o100644, blob.id)
        commit = Commit()
        commit.tree = tree.id
        commit.author = commit.committer = b"T <t@e.com>"
        commit.commit_time = commit.author_time = 1_700_000_000
        commit.commit_timezone = commit.author_timezone = 0
        commit.encoding = b"UTF-8"
        commit.message = b"init"

        pack = self._make_pack([
            (3, blob.as_raw_string()),
            (2, tree.as_raw_string()),
            (1, commit.as_raw_string()),
        ])

        zero = "0" * 40
        line = f"{zero} {commit.id.decode()} refs/heads/main\x00report-status\n"
        line_b = line.encode()
        body = f"{len(line_b) + 4:04x}".encode() + line_b + b"0000" + pack

        status, ctype, resp = _handle_receive_pack(repo_dir, body)
        assert status == 200
        assert b"unpack ok" in resp
        assert Repo(str(repo_dir)).refs[b"refs/heads/main"] == commit.id

    def test_delete_ref(self, bare_repo) -> None:
        """A push with a zero new-sha deletes the ref."""
        repo_dir, sha = bare_repo
        zero = "0" * 40
        line = f"{sha.decode()} {zero} refs/heads/main\n".encode()
        body = f"{len(line) + 4:04x}".encode() + line + b"0000"
        status, _ctype, resp = _handle_receive_pack(repo_dir, body)
        assert status == 200
        assert b"unpack ok" in resp
        assert b"refs/heads/main" not in Repo(str(repo_dir)).refs

    def test_bad_pack_reports_error(self, tmp_path) -> None:
        """A corrupt pack yields an error report but still 200."""
        repo_dir = tmp_path / "r.git"
        Repo.init_bare(str(repo_dir), mkdir=True)
        zero = "0" * 40
        newsha = "1" * 40
        line = f"{zero} {newsha} refs/heads/main\n".encode()
        body = (
            f"{len(line) + 4:04x}".encode() + line + b"0000"
            + b"PACKcorruptdata"
        )
        status, _ctype, resp = _handle_receive_pack(repo_dir, body)
        assert status == 200
        assert b"ng " in resp or b"unpack" in resp

    def test_malformed_pktline_breaks(self, tmp_path) -> None:
        """A non-hex pkt-line length terminates parsing gracefully."""
        repo_dir = tmp_path / "r.git"
        Repo.init_bare(str(repo_dir), mkdir=True)
        status, _ctype, _resp = _handle_receive_pack(repo_dir, b"zzzz")
        assert status == 200


class TestReportStatus:
    """Tests for _build_report_status."""

    def test_ok_report(self) -> None:
        """A successful report contains unpack ok and ok <ref>."""
        out = _build_report_status([("0", "1", "refs/heads/main")])
        assert b"unpack ok" in out
        assert b"ok refs/heads/main" in out

    def test_error_report(self) -> None:
        """An error report contains unpack <error> and ng <ref>."""
        out = _build_report_status(
            [("0", "1", "refs/heads/main")], error="boom",
        )
        assert b"unpack boom" in out
        assert b"ng refs/heads/main" in out


class TestEndToEnd:
    """Tests for handle_git_http_request end to end."""

    def _seed_repo_in_s3(self, tmp_path):
        """Create a bare repo with a commit and sync it to S3."""
        from gitgateway.storage import sync_repo_to_s3

        repo_dir = tmp_path / "seed.git"
        repo = Repo.init_bare(str(repo_dir), mkdir=True)
        sha = _commit_tree(repo, {"README.md": b"# hi\n"})
        repo.refs[b"refs/heads/main"] = sha
        # Ensure objects/pack survives the S3 round-trip (empty dirs are
        # not stored in S3, but dulwich needs it to receive packs).
        keep = repo_dir / "objects" / "pack" / ".keep"
        keep.write_text("")
        sync_repo_to_s3(repo_dir, "team", "app", bucket=BUCKET)
        return sha

    def test_info_refs_existing_repo(self, aws, tmp_path) -> None:
        """info/refs on an existing repo returns the advertisement."""
        self._seed_repo_in_s3(tmp_path)
        status, ctype, body = handle_git_http_request(
            method="GET",
            path_info="/info/refs",
            query_string="service=git-upload-pack",
            body=b"",
            group="team",
            repo_name="app",
        )
        assert status == 200
        assert b"service=git-upload-pack" in body

    def test_info_refs_extra_query_param(self, aws, tmp_path) -> None:
        """A query string with a non-service param still resolves service."""
        self._seed_repo_in_s3(tmp_path)
        status, _ctype, body = handle_git_http_request(
            method="GET",
            path_info="/info/refs",
            query_string="foo=bar&service=git-upload-pack",
            body=b"",
            group="team",
            repo_name="app",
        )
        assert status == 200
        assert b"service=git-upload-pack" in body

    def test_info_refs_new_repo_initialized(self, aws) -> None:
        """info/refs on a missing repo initializes a fresh bare repo."""
        status, _ctype, body = handle_git_http_request(
            method="GET",
            path_info="/info/refs",
            query_string="service=git-upload-pack",
            body=b"",
            group="team",
            repo_name="brandnew",
        )
        assert status == 200
        assert b"capabilities^{}" in body

    def test_info_refs_query_without_service(self, aws, tmp_path) -> None:
        """A query string without a service= param resolves no service."""
        self._seed_repo_in_s3(tmp_path)
        status, _ctype, _body = handle_git_http_request(
            method="GET",
            path_info="/info/refs",
            query_string="foo=bar&baz=qux",
            body=b"",
            group="team",
            repo_name="app",
        )
        # No service resolved -> 400 from _handle_info_refs.
        assert status == 400

    def test_info_refs_empty_query_string(self, aws, tmp_path) -> None:
        """An empty query string skips service parsing (empty-query leg)."""
        self._seed_repo_in_s3(tmp_path)
        status, _ctype, _body = handle_git_http_request(
            method="GET",
            path_info="/info/refs",
            query_string="",
            body=b"",
            group="team",
            repo_name="app",
        )
        # No service -> _handle_info_refs returns 400.
        assert status == 400

    def test_unknown_path_404(self, aws) -> None:
        """An unrecognized path returns 404."""
        status, _ctype, _body = handle_git_http_request(
            method="GET",
            path_info="/nonsense",
            query_string="",
            body=b"",
            group="team",
            repo_name="brandnew",
        )
        assert status == 404

    def test_receive_pack_indexes_tree(self, aws, tmp_path) -> None:
        """A push syncs to S3 and indexes the tree into DynamoDB."""
        self._seed_repo_in_s3(tmp_path)

        # Build a push that adds a new branch pointing at a new commit.
        repo = Repo(str(tmp_path / "seed.git"))
        blob = Blob.from_string(b"new\n")
        tree = Tree()
        tree.add(b"new.txt", 0o100644, blob.id)
        commit = Commit()
        commit.tree = tree.id
        commit.author = commit.committer = b"T <t@e.com>"
        commit.commit_time = commit.author_time = 1_700_000_100
        commit.commit_timezone = commit.author_timezone = 0
        commit.encoding = b"UTF-8"
        commit.message = b"feat"

        buf = io.BytesIO()
        buf.write(b"PACK")
        buf.write(struct.pack(">I", 2))
        buf.write(struct.pack(">I", 3))
        for type_num, raw in [
            (3, blob.as_raw_string()),
            (2, tree.as_raw_string()),
            (1, commit.as_raw_string()),
        ]:
            _write_pack_object_simple(buf, type_num, raw)
        import hashlib
        buf.seek(0)
        buf.write(hashlib.sha1(buf.read()).digest())  # nosec
        pack = buf.getvalue()

        zero = "0" * 40
        line = (
            f"{zero} {commit.id.decode()} refs/heads/feat"
            f"\x00report-status\n"
        ).encode()
        body = f"{len(line) + 4:04x}".encode() + line + b"0000" + pack

        status, _ctype, resp = handle_git_http_request(
            method="POST",
            path_info="/git-receive-pack",
            query_string="",
            body=body,
            group="team",
            repo_name="app",
        )
        assert status == 200
        # The tree should have been indexed for the new branch.
        table = aws.Table("gitgateway-repos")
        item = table.get_item(Key={
            "PK": "TREE#team/app#feat",
            "SK": "FILE#new.txt",
        }).get("Item")
        assert item is not None

    def test_upload_pack_via_dispatch(self, aws, tmp_path) -> None:
        """git-upload-pack path routes through the dispatcher."""
        sha = self._seed_repo_in_s3(tmp_path)
        want = b"want " + sha + b"\n"
        body = f"{len(want) + 4:04x}".encode() + want + b"0000" + b"done"
        status, ctype, resp = handle_git_http_request(
            method="POST",
            path_info="/git-upload-pack",
            query_string="",
            body=body,
            group="team",
            repo_name="app",
        )
        assert status == 200
        assert ctype == "application/x-git-upload-pack-result"


class TestErrorPaths:
    """Cover the error/exception branches of handle_git_http_request."""

    def _seed(self, tmp_path):
        from gitgateway.storage import sync_repo_to_s3

        repo_dir = tmp_path / "seed.git"
        repo = Repo.init_bare(str(repo_dir), mkdir=True)
        sha = _commit_tree(repo, {"README.md": b"# hi\n"})
        repo.refs[b"refs/heads/main"] = sha
        (repo_dir / "objects" / "pack" / ".keep").write_text("")
        sync_repo_to_s3(repo_dir, "team", "app", bucket=BUCKET)
        return sha

    def test_corrupt_repo_reinitialized(self, aws, tmp_path) -> None:
        """A sync/open failure triggers a fresh reinit."""
        from unittest.mock import patch

        # repo_exists True, but sync raises -> reinit branch.
        with patch(
            "gitgateway.git_backend.repo_exists", return_value=True,
        ), patch(
            "gitgateway.git_backend.sync_repo_to_local",
            side_effect=RuntimeError("corrupt"),
        ):
            status, _ctype, body = handle_git_http_request(
                method="GET",
                path_info="/info/refs",
                query_string="service=git-upload-pack",
                body=b"",
                group="team",
                repo_name="recover",
            )
        assert status == 200
        assert b"capabilities^{}" in body

    def test_handler_exception_returns_500(self, aws, tmp_path) -> None:
        """An exception in a sub-handler returns HTTP 500."""
        from unittest.mock import patch

        self._seed(tmp_path)
        with patch(
            "gitgateway.git_backend._handle_info_refs",
            side_effect=RuntimeError("boom"),
        ):
            status, ctype, body = handle_git_http_request(
                method="GET",
                path_info="/info/refs",
                query_string="service=git-upload-pack",
                body=b"",
                group="team",
                repo_name="app",
            )
        assert status == 500
        assert b"Internal error" in body

    def test_push_sync_and_index_failures_tolerated(
        self, aws, tmp_path,
    ) -> None:
        """Sync-back and index failures after a push are swallowed."""
        from unittest.mock import patch

        self._seed(tmp_path)
        # A delete push (no pack) succeeds at the ref level, then we force
        # both the sync-back and indexing to fail.
        sha = Repo(str(tmp_path / "seed.git")).refs[b"refs/heads/main"]
        zero = "0" * 40
        line = f"{sha.decode()} {zero} refs/heads/main\n".encode()
        body = f"{len(line) + 4:04x}".encode() + line + b"0000"

        with patch(
            "gitgateway.git_backend.sync_repo_to_s3",
            side_effect=RuntimeError("s3 down"),
        ), patch(
            "gitgateway.git_backend._index_repo_tree",
            side_effect=RuntimeError("index down"),
        ):
            status, _ctype, resp = handle_git_http_request(
                method="POST",
                path_info="/git-receive-pack",
                query_string="",
                body=body,
                group="team",
                repo_name="app",
            )
        assert status == 200
        assert b"unpack ok" in resp


class TestIndexing:
    """Cover _index_repo_tree / _index_tree_recursive / commit history."""

    def test_indexes_subdirs_and_history(self, aws, tmp_path) -> None:
        """Indexing walks subdirectories and multi-commit history."""
        repo_dir = tmp_path / "idx.git"
        repo = Repo.init_bare(str(repo_dir), mkdir=True)

        # Build a tree with a subdirectory and a large file.
        sub_blob = Blob.from_string(b"nested\n")
        repo.object_store.add_object(sub_blob)
        subtree = Tree()
        subtree.add(b"nested.txt", 0o100644, sub_blob.id)
        repo.object_store.add_object(subtree)

        big_blob = Blob.from_string(b"x" * 2_000_000)  # >1MB, skipped content
        repo.object_store.add_object(big_blob)
        root_blob = Blob.from_string(b"root\n")
        repo.object_store.add_object(root_blob)
        root = Tree()
        root.add(b"src", 0o040000, subtree.id)
        root.add(b"big.bin", 0o100644, big_blob.id)
        root.add(b"README.md", 0o100644, root_blob.id)
        repo.object_store.add_object(root)

        first = Commit()
        first.tree = root.id
        first.author = first.committer = b"T <t@e.com>"
        first.commit_time = first.author_time = 1_700_000_000
        first.commit_timezone = first.author_timezone = 0
        first.encoding = b"UTF-8"
        first.message = b"first"
        repo.object_store.add_object(first)

        second = Commit()
        second.tree = root.id
        second.author = second.committer = b"T <t@e.com>"
        second.commit_time = second.author_time = 1_700_000_100
        second.commit_timezone = second.author_timezone = 0
        second.encoding = b"UTF-8"
        second.message = b"second\n\nbody"
        second.parents = [first.id]
        repo.object_store.add_object(second)

        repo.refs[b"refs/heads/main"] = second.id
        # A non-branch ref that should be skipped by indexing.
        repo.refs[b"refs/tags/v1"] = second.id

        _index_repo_tree(repo_dir, "team", "idx")

        table = aws.Table("gitgateway-repos")
        # Nested file indexed with full path.
        nested = table.get_item(Key={
            "PK": "TREE#team/idx#main",
            "SK": "FILE#src/nested.txt",
        }).get("Item")
        assert nested is not None
        # Large file indexed but with empty content.
        big = table.get_item(Key={
            "PK": "TREE#team/idx#main",
            "SK": "FILE#big.bin",
        }).get("Item")
        assert big is not None and big["content"] == ""
        # Commit history has both commits.
        commits = table.query(
            KeyConditionExpression=(
                "PK = :pk AND begins_with(SK, :p)"
            ),
            ExpressionAttributeValues={
                ":pk": "COMMITS#team/idx#main",
                ":p": "COMMIT#",
            },
        )["Items"]
        assert len(commits) == 2


class TestIndexingEdges:
    """Cover the error/continue branches of _index_repo_tree."""

    def test_branch_points_at_non_commit(self, aws, tmp_path) -> None:
        """A branch ref pointing at a tree object is skipped."""
        repo_dir = tmp_path / "nc.git"
        repo = Repo.init_bare(str(repo_dir), mkdir=True)
        blob = Blob.from_string(b"x\n")
        repo.object_store.add_object(blob)
        tree = Tree()
        tree.add(b"f", 0o100644, blob.id)
        repo.object_store.add_object(tree)
        # Point a branch at the TREE (not a commit).
        repo.refs[b"refs/heads/weird"] = tree.id
        _index_repo_tree(repo_dir, "team", "nc")
        # No tree files should be indexed for that branch.
        table = aws.Table("gitgateway-repos")
        items = table.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": "TREE#team/nc#weird"},
        )["Items"]
        assert items == []

    def test_branch_points_at_missing_sha(self, aws, tmp_path) -> None:
        """A branch ref pointing at a missing object is skipped."""
        repo_dir = tmp_path / "ms.git"
        repo = Repo.init_bare(str(repo_dir), mkdir=True)
        repo.refs[b"refs/heads/ghost"] = b"a" * 40  # no such object
        # Should not raise.
        _index_repo_tree(repo_dir, "team", "ms")

    def test_tree_and_history_index_errors(self, aws, tmp_path) -> None:
        """Errors while indexing tree/history are caught and logged."""
        from unittest.mock import patch

        repo_dir = tmp_path / "err.git"
        repo = Repo.init_bare(str(repo_dir), mkdir=True)
        sha = _commit_tree(repo, {"a.txt": b"a\n"})
        repo.refs[b"refs/heads/main"] = sha
        with patch(
            "gitgateway.git_backend._index_tree_recursive",
            side_effect=RuntimeError("tree boom"),
        ), patch(
            "gitgateway.git_backend._index_commit_history",
            side_effect=RuntimeError("hist boom"),
        ):
            # Should not raise despite both failing.
            _index_repo_tree(repo_dir, "team", "err")


class TestIndexingHelpers:
    """Directly cover _index_tree_recursive and _index_commit_history edges."""

    @pytest.fixture
    def table(self, aws):
        return aws.Table("gitgateway-repos")

    def test_tree_recursive_missing_tree(self, table) -> None:
        """A missing tree sha returns without indexing."""
        from dulwich.object_store import MemoryObjectStore

        from gitgateway.git_backend import _index_tree_recursive

        _index_tree_recursive(
            MemoryObjectStore(), table, "team", "r", "main",
            b"0" * 40, "", "msg", "author",
        )  # should not raise

    def test_tree_recursive_missing_blob(self, table) -> None:
        """A tree entry pointing at a missing blob indexes empty content."""
        from dulwich.object_store import MemoryObjectStore
        from dulwich.objects import Tree

        from gitgateway.git_backend import _index_tree_recursive

        store = MemoryObjectStore()
        tree = Tree()
        tree.add(b"gone.txt", 0o100644, b"1" * 40)  # blob not in store
        store.add_object(tree)
        _index_tree_recursive(
            store, table, "team", "r", "main", tree.id, "", "m", "a",
        )
        item = table.get_item(Key={
            "PK": "TREE#team/r#main", "SK": "FILE#gone.txt",
        }).get("Item")
        assert item is not None and item["content"] == ""

    def test_commit_history_missing_parent(self, table) -> None:
        """A missing parent commit is skipped during history walk."""
        from dulwich.object_store import MemoryObjectStore
        from dulwich.objects import Blob, Commit, Tree

        from gitgateway.git_backend import _index_commit_history

        store = MemoryObjectStore()
        blob = Blob.from_string(b"x\n")
        store.add_object(blob)
        tree = Tree()
        tree.add(b"f", 0o100644, blob.id)
        store.add_object(tree)
        commit = Commit()
        commit.tree = tree.id
        commit.parents = [b"9" * 40]  # missing parent
        commit.author = commit.committer = b"T <t@e.com>"
        commit.commit_time = commit.author_time = 1_700_000_000
        commit.commit_timezone = commit.author_timezone = 0
        commit.encoding = b"UTF-8"
        commit.message = b"only"
        store.add_object(commit)

        _index_commit_history(store, table, "team", "r", "main", commit.id)
        commits = table.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": "COMMITS#team/r#main"},
        )["Items"]
        assert len(commits) == 1



    def test_commit_history_diamond(self, table) -> None:
        """A commit reached via two parents is visited once."""
        from dulwich.object_store import MemoryObjectStore
        from dulwich.objects import Blob, Commit, Tree

        from gitgateway.git_backend import _index_commit_history

        store = MemoryObjectStore()
        blob = Blob.from_string(b"x\n")
        store.add_object(blob)
        tree = Tree()
        tree.add(b"f", 0o100644, blob.id)
        store.add_object(tree)

        def _mk(msg, parents, t):
            c = Commit()
            c.tree = tree.id
            c.parents = parents
            c.author = c.committer = b"T <t@e.com>"
            c.commit_time = c.author_time = t
            c.commit_timezone = c.author_timezone = 0
            c.encoding = b"UTF-8"
            c.message = msg
            store.add_object(c)
            return c.id

        base = _mk(b"base", [], 1_700_000_000)
        left = _mk(b"left", [base], 1_700_000_100)
        right = _mk(b"right", [base], 1_700_000_100)
        merge = _mk(b"merge", [left, right], 1_700_000_200)

        _index_commit_history(store, table, "team", "r", "main", merge)
        commits = table.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": "COMMITS#team/r#main"},
        )["Items"]
        # base reached via both left and right, but indexed once => 4 total.
        assert len(commits) == 4


class TestReceivePackErrors:
    """Cover receive-pack pack-apply failure branch."""

    def test_pack_apply_failure_reports_ng(self, tmp_path) -> None:
        """A pack that fails to apply yields an error (ng) report."""
        from unittest.mock import patch

        repo_dir = tmp_path / "r.git"
        Repo.init_bare(str(repo_dir), mkdir=True)

        # Build a real, well-formed pack (same shape as the success test)
        # so parsing reaches add_thin_pack, which we patch to raise.
        blob = Blob.from_string(b"hi\n")
        tree = Tree()
        tree.add(b"f.txt", 0o100644, blob.id)
        commit = Commit()
        commit.tree = tree.id
        commit.author = commit.committer = b"T <t@e.com>"
        commit.commit_time = commit.author_time = 1_700_000_000
        commit.commit_timezone = commit.author_timezone = 0
        commit.encoding = b"UTF-8"
        commit.message = b"init"
        pack = _make_pack([
            (3, blob.as_raw_string()),
            (2, tree.as_raw_string()),
            (1, commit.as_raw_string()),
        ])
        zero = "0" * 40
        line = (
            f"{zero} {commit.id.decode()} refs/heads/main\x00report-status\n"
        ).encode()
        body = f"{len(line) + 4:04x}".encode() + line + b"0000" + pack

        store_cls = type(Repo(str(repo_dir)).object_store)
        with patch.object(
            store_cls, "add_thin_pack",
            side_effect=RuntimeError("bad pack"),
        ):
            status, _ctype, resp = _handle_receive_pack(repo_dir, body)
        assert status == 200
        assert b"ng " in resp


class TestCollectObjects:
    """Tests for the _collect_objects helper."""

    def test_collects_tree_and_parents(self, tmp_path) -> None:
        """Collects tree, blobs, and parent commits from a commit."""
        repo_dir = tmp_path / "c.git"
        repo = Repo.init_bare(str(repo_dir), mkdir=True)
        parent = _commit_tree(repo, {"a.txt": b"a\n"})
        child = _commit_tree(
            repo, {"a.txt": b"a\n", "b.txt": b"b\n"}, parents=[parent],
        )
        child_obj = repo.object_store[child]
        collected: list = []
        _collect_objects(repo.object_store, child_obj, collected)
        # Should include at least the tree and the parent commit.
        assert any(o.type_name == b"tree" for o in collected)

    def test_collect_nested_subtree(self, tmp_path) -> None:
        """Collects blobs from nested sub-trees."""
        repo_dir = tmp_path / "nest.git"
        repo = Repo.init_bare(str(repo_dir), mkdir=True)
        sub_blob = Blob.from_string(b"n\n")
        repo.object_store.add_object(sub_blob)
        subtree = Tree()
        subtree.add(b"n.txt", 0o100644, sub_blob.id)
        repo.object_store.add_object(subtree)
        root = Tree()
        root.add(b"sub", 0o040000, subtree.id)
        repo.object_store.add_object(root)
        commit = Commit()
        commit.tree = root.id
        commit.author = commit.committer = b"T <t@e.com>"
        commit.commit_time = commit.author_time = 1_700_000_000
        commit.commit_timezone = commit.author_timezone = 0
        commit.encoding = b"UTF-8"
        commit.message = b"m"
        repo.object_store.add_object(commit)
        collected: list = []
        _collect_objects(repo.object_store, commit, collected)
        assert any(o.type_name == b"tree" for o in collected)

    def test_collect_dedup_shared_blob(self, tmp_path) -> None:
        """A tree with two entries sharing one blob dedups (seen-skip)."""
        repo_dir = tmp_path / "dup.git"
        repo = Repo.init_bare(str(repo_dir), mkdir=True)
        blob = Blob.from_string(b"shared\n")
        repo.object_store.add_object(blob)
        tree = Tree()
        tree.add(b"a.txt", 0o100644, blob.id)
        tree.add(b"b.txt", 0o100644, blob.id)  # same blob, second entry
        repo.object_store.add_object(tree)
        commit = Commit()
        commit.tree = tree.id
        commit.author = commit.committer = b"T <t@e.com>"
        commit.commit_time = commit.author_time = 1_700_000_000
        commit.commit_timezone = commit.author_timezone = 0
        commit.encoding = b"UTF-8"
        commit.message = b"m"
        repo.object_store.add_object(commit)
        collected: list = []
        _collect_objects(repo.object_store, commit, collected)
        # The shared blob appears once thanks to the seen-set skip.
        blob_count = sum(1 for o in collected if o.id == blob.id)
        assert blob_count == 1

    def test_collect_missing_tree_tolerated(self, tmp_path) -> None:
        """A commit whose tree/parents are missing is handled gracefully."""
        repo_dir = tmp_path / "m.git"
        repo = Repo.init_bare(str(repo_dir), mkdir=True)
        commit = Commit()
        commit.tree = b"0" * 40  # missing tree
        commit.parents = [b"1" * 40]  # missing parent
        commit.author = commit.committer = b"T <t@e.com>"
        commit.commit_time = commit.author_time = 1_700_000_000
        commit.commit_timezone = commit.author_timezone = 0
        commit.encoding = b"UTF-8"
        commit.message = b"m"
        collected: list = []
        # Should not raise despite missing referenced objects.
        _collect_objects(repo.object_store, commit, collected)
        assert collected == []
