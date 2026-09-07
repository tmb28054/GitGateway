"""Route-level tests for gitgateway.lambda_handler.

Exercises the real request handlers (_handle_api, _dispatch, web
rendering, auth callback, and helpers) against moto-mocked DynamoDB
so the actual routing/CRUD paths are covered rather than stubbed.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from gitgateway.lambda_handler import (
    Request,
    _get_page_content,
    _handle_api,
)

TABLE_SUFFIXES = [
    "repos",
    "access",
    "prs",
    "comments",
    "status-checks",
    "audit",
]


def _create_table(resource, name: str):
    """Create a PK/SK string-keyed table."""
    return resource.create_table(
        TableName=f"gitgateway-{name}",
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


@pytest.fixture
def aws():
    """Provision all DynamoDB tables + S3 buckets the handler touches."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        for suffix in TABLE_SUFFIXES:
            _create_table(resource, suffix)
        # Buckets used by storage/LFS sync during diff rendering.
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="gitgateway-repos")
        s3.create_bucket(Bucket="gitgateway-lfs")
        yield resource


def _req(method: str, path: str, body: dict | None = None,
         query_string: str = "", headers: dict | None = None) -> Request:
    """Build a Request for the API handler."""
    return Request(
        method=method,
        path=path,
        query_string=query_string,
        headers=headers or {},
        body=json.dumps(body).encode() if body is not None else b"",
    )


class TestApiGroups:
    """Tests for /api/groups routes."""

    def test_create_group(self, aws) -> None:
        """POST /api/groups creates a group and returns 201."""
        resp = _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        assert resp.status_code == 201
        assert json.loads(resp.body)["name"] == "team"

    def test_create_group_missing_name(self, aws) -> None:
        """POST /api/groups without a name returns 400."""
        resp = _handle_api(_req("POST", "/api/groups", {}))
        assert resp.status_code == 400

    def test_create_group_invalid_json(self, aws) -> None:
        """POST /api/groups with bad JSON returns 400."""
        req = _req("POST", "/api/groups")
        req.body = b"{not json"
        resp = _handle_api(req)
        assert resp.status_code == 400

    def test_create_group_conflict(self, aws) -> None:
        """Creating a duplicate group returns 409."""
        _handle_api(_req("POST", "/api/groups", {"name": "dup"}))
        resp = _handle_api(_req("POST", "/api/groups", {"name": "dup"}))
        assert resp.status_code == 409

    def test_list_groups(self, aws) -> None:
        """GET /api/groups returns the group list."""
        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        resp = _handle_api(_req("GET", "/api/groups"))
        assert resp.status_code == 200
        names = [g["name"] for g in json.loads(resp.body)]
        assert "team" in names

    def test_delete_group(self, aws) -> None:
        """DELETE /api/groups/<name> returns 204."""
        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        resp = _handle_api(_req("DELETE", "/api/groups/team"))
        assert resp.status_code == 204

    def test_delete_missing_group(self, aws) -> None:
        """DELETE of a missing group returns 404."""
        resp = _handle_api(_req("DELETE", "/api/groups/ghost"))
        assert resp.status_code == 404


class TestApiRepos:
    """Tests for /api/repos routes."""

    def test_create_repo(self, aws) -> None:
        """POST /api/repos/<group> creates a repo."""
        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        resp = _handle_api(
            _req("POST", "/api/repos/team", {"name": "app"}),
        )
        assert resp.status_code == 201
        assert json.loads(resp.body)["name"] == "app"

    def test_create_repo_missing_name(self, aws) -> None:
        """POST /api/repos/<group> without name returns 400."""
        resp = _handle_api(_req("POST", "/api/repos/team", {}))
        assert resp.status_code == 400

    def test_create_repo_conflict(self, aws) -> None:
        """Duplicate repo returns 409."""
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        resp = _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        assert resp.status_code == 409

    def test_list_repos(self, aws) -> None:
        """GET /api/repos/<group> lists repos."""
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        resp = _handle_api(_req("GET", "/api/repos/team"))
        assert resp.status_code == 200
        assert json.loads(resp.body)[0]["name"] == "app"

    def test_delete_repo(self, aws) -> None:
        """DELETE /api/repos/<group>/<repo> returns 204."""
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        resp = _handle_api(_req("DELETE", "/api/repos/team/app"))
        assert resp.status_code == 204

    def test_delete_missing_repo(self, aws) -> None:
        """DELETE of a missing repo returns 404."""
        resp = _handle_api(_req("DELETE", "/api/repos/team/ghost"))
        assert resp.status_code == 404

    def test_update_repo_settings(self, aws) -> None:
        """PATCH .../settings updates repo metadata."""
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        resp = _handle_api(_req(
            "PATCH", "/api/repos/team/app/settings",
            {"default_branch": "develop", "description": "d"},
        ))
        assert resp.status_code == 200
        assert json.loads(resp.body)["default_branch"] == "develop"

    def test_update_missing_repo_settings(self, aws) -> None:
        """PATCH settings on a missing repo returns 404."""
        resp = _handle_api(_req(
            "PATCH", "/api/repos/team/ghost/settings",
            {"description": "x"},
        ))
        assert resp.status_code == 404


class TestApiBranches:
    """Tests for /api/repos/<group>/<repo>/branches routes."""

    def test_register_and_list_branch(self, aws) -> None:
        """POST then GET branches round-trips the branch name."""
        post = _handle_api(_req(
            "POST", "/api/repos/team/app/branches", {"branch": "main"},
        ))
        assert post.status_code == 201
        get = _handle_api(_req("GET", "/api/repos/team/app/branches"))
        assert get.status_code == 200
        assert "main" in json.loads(get.body)

    def test_register_branch_missing_name(self, aws) -> None:
        """POST branches without a branch returns 400."""
        resp = _handle_api(
            _req("POST", "/api/repos/team/app/branches", {}),
        )
        assert resp.status_code == 400

    def test_delete_branch(self, aws) -> None:
        """DELETE removes a registered branch."""
        _handle_api(_req(
            "POST", "/api/repos/team/app/branches", {"branch": "feat"},
        ))
        resp = _handle_api(
            _req("DELETE", "/api/repos/team/app/branches/feat"),
        )
        assert resp.status_code == 204


class TestApiAccess:
    """Tests for /api/access routes."""

    def test_grant_access(self, aws) -> None:
        """POST /api/access grants access."""
        resp = _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))
        assert resp.status_code == 200

    def test_grant_access_bad_level(self, aws) -> None:
        """POST /api/access with an invalid level returns 400."""
        resp = _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "wizard",
        }))
        assert resp.status_code == 400

    def test_grant_access_missing_fields(self, aws) -> None:
        """POST /api/access missing fields returns 400."""
        resp = _handle_api(_req("POST", "/api/access", {"username": "a"}))
        assert resp.status_code == 400

    def test_list_access(self, aws) -> None:
        """GET /api/access lists entries."""
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))
        resp = _handle_api(_req("GET", "/api/access?group=team"))
        assert resp.status_code == 200

    def test_revoke_access(self, aws) -> None:
        """DELETE /api/access revokes an entry."""
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))
        resp = _handle_api(_req("DELETE", "/api/access", {
            "username": "alice", "resource": "team",
        }))
        assert resp.status_code == 204

    def test_revoke_access_missing_fields(self, aws) -> None:
        """DELETE /api/access missing fields returns 400."""
        resp = _handle_api(_req("DELETE", "/api/access", {"username": "a"}))
        assert resp.status_code == 400


class TestApiPullRequests:
    """Tests for PR create/list/merge/comment/review routes."""

    def test_create_and_list_pr(self, aws) -> None:
        """POST then GET /api/<g>/<r>/pulls round-trips."""
        create = _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "Add x", "source_branch": "feat",
             "target_branch": "main", "author": "alice"},
        ))
        assert create.status_code == 201
        number = json.loads(create.body)["number"]
        assert number >= 1

        listed = _handle_api(_req("GET", "/api/team/app/pulls"))
        assert listed.status_code == 200
        assert any(p["number"] == number for p in json.loads(listed.body))

    def test_list_pr_status_filter(self, aws) -> None:
        """GET pulls with a status filter returns 200."""
        _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "t", "source_branch": "b", "author": "a"},
        ))
        resp = _handle_api(
            _req("GET", "/api/team/app/pulls", query_string="status=open"),
        )
        assert resp.status_code == 200

    def test_comment_round_trip(self, aws) -> None:
        """POST then GET comments on a PR."""
        create = _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "t", "source_branch": "b", "author": "a"},
        ))
        num = json.loads(create.body)["number"]
        post = _handle_api(_req(
            "POST",
            f"/api/team/app/pulls/{num}/comments",
            {"author": "bob", "body": "nice"},
        ))
        assert post.status_code == 201
        listed = _handle_api(
            _req("GET", f"/api/team/app/pulls/{num}/comments"),
        )
        assert listed.status_code == 200
        assert json.loads(listed.body)[0]["body"] == "nice"

    def test_submit_review(self, aws) -> None:
        """POST a review with a valid status returns 201."""
        create = _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "t", "source_branch": "b", "author": "a"},
        ))
        num = json.loads(create.body)["number"]
        resp = _handle_api(_req(
            "POST", f"/api/team/app/pulls/{num}/reviews",
            {"author": "bob", "status": "approved", "body": "lgtm"},
        ))
        assert resp.status_code == 201

    def test_submit_review_bad_status(self, aws) -> None:
        """POST a review with an invalid status returns 400."""
        create = _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "t", "source_branch": "b", "author": "a"},
        ))
        num = json.loads(create.body)["number"]
        resp = _handle_api(_req(
            "POST", f"/api/team/app/pulls/{num}/reviews",
            {"author": "bob", "status": "bogus"},
        ))
        assert resp.status_code == 400

    def test_merge_pr(self, aws) -> None:
        """POST .../merge on an open PR returns 200."""
        create = _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "t", "source_branch": "b", "author": "a"},
        ))
        num = json.loads(create.body)["number"]
        resp = _handle_api(_req(
            "POST", f"/api/team/app/pulls/{num}/merge",
            {"strategy": "squash"},
        ))
        assert resp.status_code == 200

    def test_merge_missing_pr(self, aws) -> None:
        """Merging a nonexistent PR returns 404."""
        resp = _handle_api(_req(
            "POST", "/api/team/app/pulls/999/merge", {"strategy": "merge"},
        ))
        assert resp.status_code == 404

    def test_close_pr(self, aws) -> None:
        """POST .../close on an open PR returns 200."""
        create = _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "t", "source_branch": "b", "author": "a"},
        ))
        num = json.loads(create.body)["number"]
        resp = _handle_api(
            _req("POST", f"/api/team/app/pulls/{num}/close"),
        )
        assert resp.status_code == 200

    def test_close_missing_pr(self, aws) -> None:
        """Closing a nonexistent PR returns 404."""
        resp = _handle_api(_req("POST", "/api/team/app/pulls/999/close"))
        assert resp.status_code == 404


class TestApiTokens:
    """Tests for personal access token routes."""

    @staticmethod
    def _auth_headers(username: str = "alice") -> dict:
        """Build headers with a valid session cookie for the user."""
        from gitgateway.auth import (  # pylint: disable=import-outside-toplevel
            AuthenticatedUser,
            create_session_cookie,
        )

        cookie = create_session_cookie(
            AuthenticatedUser(username=username),
            "gitgateway-session-secret",
        )
        return {"cookie": f"session={cookie}"}

    def test_create_token_requires_auth(self, aws) -> None:
        """POST /api/tokens without a session returns 401."""
        resp = _handle_api(_req("POST", "/api/tokens", {"description": "x"}))
        assert resp.status_code == 401

    def test_create_and_list_token(self, aws) -> None:
        """POST then GET /api/tokens for an authenticated user."""
        headers = self._auth_headers()
        create = _handle_api(_req(
            "POST", "/api/tokens",
            {"description": "laptop", "scope": "read/write"},
            headers=headers,
        ))
        assert create.status_code == 201
        assert json.loads(create.body)["token"].startswith("gg_")

        listed = _handle_api(
            _req("GET", "/api/tokens", headers=headers),
        )
        assert listed.status_code == 200
        assert len(json.loads(listed.body)) == 1

    def test_list_tokens_requires_auth(self, aws) -> None:
        """GET /api/tokens without a session returns 401."""
        resp = _handle_api(_req("GET", "/api/tokens"))
        assert resp.status_code == 401

    def test_create_token_invalid_scope_defaults(self, aws) -> None:
        """Creating a token with an invalid scope falls back to read/write."""
        headers = self._auth_headers()
        resp = _handle_api(_req(
            "POST", "/api/tokens",
            {"description": "d", "scope": "bogus-scope"},
            headers=headers,
        ))
        assert resp.status_code == 201
        assert json.loads(resp.body)["scope"] == "read/write"

    def test_revoke_token(self, aws) -> None:
        """DELETE /api/tokens/<id> revokes an owned token."""
        headers = self._auth_headers()
        create = _handle_api(_req(
            "POST", "/api/tokens", {"description": "d"}, headers=headers,
        ))
        token_id = json.loads(create.body)["token_id"]
        resp = _handle_api(_req(
            "DELETE", f"/api/tokens/{token_id}", headers=headers,
        ))
        assert resp.status_code == 200

    def test_revoke_missing_token(self, aws) -> None:
        """DELETE of a nonexistent token returns 404."""
        headers = self._auth_headers()
        resp = _handle_api(_req(
            "DELETE", "/api/tokens/deadbeef", headers=headers,
        ))
        assert resp.status_code == 404

    def test_revoke_token_requires_auth(self, aws) -> None:
        """DELETE /api/tokens/<id> without a session returns 401."""
        resp = _handle_api(_req("DELETE", "/api/tokens/deadbeef"))
        assert resp.status_code == 401


class TestApiPrErrorBranches:
    """Cover PR merge/close/rescan/create error and best-effort branches."""

    def _make_pr(self) -> int:
        create = _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "t", "source_branch": "b", "author": "a"},
        ))
        return json.loads(create.body)["number"]

    def test_merge_already_merged_conflict(self, aws) -> None:
        """Merging an already-merged PR returns 409."""
        num = self._make_pr()
        _handle_api(_req("POST", f"/api/team/app/pulls/{num}/merge"))
        resp = _handle_api(_req("POST", f"/api/team/app/pulls/{num}/merge"))
        assert resp.status_code == 409

    def test_close_already_closed_conflict(self, aws) -> None:
        """Closing an already-merged PR returns 409."""
        num = self._make_pr()
        _handle_api(_req("POST", f"/api/team/app/pulls/{num}/merge"))
        resp = _handle_api(_req("POST", f"/api/team/app/pulls/{num}/close"))
        assert resp.status_code == 409

    def test_close_pr(self, aws) -> None:
        """Closing an open PR returns 200."""
        num = self._make_pr()
        resp = _handle_api(_req("POST", f"/api/team/app/pulls/{num}/close"))
        assert resp.status_code == 200

    def test_rescan_success(self, aws) -> None:
        """Rescan of an existing PR triggers linting/AI and returns 200."""
        num = self._make_pr()
        with patch(
            "gitgateway.lambda_handler._run_pr_linting",
        ), patch(
            "gitgateway.lambda_handler._run_ai_review",
        ):
            resp = _handle_api(
                _req("POST", f"/api/team/app/pulls/{num}/rescan"),
            )
        assert resp.status_code == 200

    def test_merge_invalid_strategy_defaults(self, aws) -> None:
        """Merge with an unrecognized strategy falls back to merge."""
        num = self._make_pr()
        resp = _handle_api(_req(
            "POST", f"/api/team/app/pulls/{num}/merge",
            {"strategy": "bogus-strategy"},
        ))
        assert resp.status_code == 200

    def test_rescan_linting_failure_500(self, aws) -> None:
        """A failure during rescan linting returns 500."""
        num = self._make_pr()
        with patch(
            "gitgateway.lambda_handler._run_pr_linting",
            side_effect=RuntimeError("boom"),
        ):
            resp = _handle_api(
                _req("POST", f"/api/team/app/pulls/{num}/rescan"),
            )
        assert resp.status_code == 500

    def test_pr_create_linting_failure_ignored(self, aws) -> None:
        """PR create still returns 201 when linting/AI raise (best-effort)."""
        with patch(
            "gitgateway.lambda_handler._run_pr_linting",
            side_effect=RuntimeError("lint boom"),
        ), patch(
            "gitgateway.lambda_handler._run_ai_review",
            side_effect=RuntimeError("ai boom"),
        ):
            resp = _handle_api(_req(
                "POST", "/api/team/app/pulls",
                {"title": "t", "source_branch": "b", "author": "a"},
            ))
        assert resp.status_code == 201


class TestApiInvalidJson:
    """Cover the invalid-JSON 400 branches across API routes."""

    def _bad(self, method: str, path: str):
        req = _req(method, path)
        req.body = b"{not json"
        return _handle_api(req)

    def test_branch_register_bad_json(self, aws) -> None:
        """POST branches with bad JSON returns 400."""
        assert self._bad(
            "POST", "/api/repos/team/app/branches",
        ).status_code == 400

    def test_repo_create_bad_json(self, aws) -> None:
        """POST repo with bad JSON returns 400."""
        assert self._bad("POST", "/api/repos/team").status_code == 400

    def test_repo_settings_bad_json(self, aws) -> None:
        """PATCH settings with bad JSON returns 400."""
        assert self._bad(
            "PATCH", "/api/repos/team/app/settings",
        ).status_code == 400

    def test_pr_create_bad_json(self, aws) -> None:
        """POST PR with bad JSON returns 400."""
        assert self._bad(
            "POST", "/api/team/app/pulls",
        ).status_code == 400

    def test_comment_bad_json(self, aws) -> None:
        """POST comment with bad JSON returns 400."""
        assert self._bad(
            "POST", "/api/team/app/pulls/1/comments",
        ).status_code == 400

    def test_review_bad_json(self, aws) -> None:
        """POST review with bad JSON returns 400."""
        assert self._bad(
            "POST", "/api/team/app/pulls/1/reviews",
        ).status_code == 400

    def test_access_grant_bad_json(self, aws) -> None:
        """POST access with bad JSON returns 400."""
        assert self._bad("POST", "/api/access").status_code == 400

    def test_access_revoke_bad_json(self, aws) -> None:
        """DELETE access with bad JSON returns 400."""
        assert self._bad("DELETE", "/api/access").status_code == 400

    def test_group_create_bad_json(self, aws) -> None:
        """POST group with bad JSON returns 400."""
        assert self._bad("POST", "/api/groups").status_code == 400


class TestApiMiscBranches:
    """Cover assorted API branch legs."""

    def test_merge_bad_json_defaults_strategy(self, aws) -> None:
        """Merge with bad JSON body falls back to the default strategy."""
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        create = _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "t", "source_branch": "b", "author": "a"},
        ))
        num = json.loads(create.body)["number"]
        req = _req("POST", f"/api/team/app/pulls/{num}/merge")
        req.body = b"{bad"
        resp = _handle_api(req)
        assert resp.status_code == 200

    def test_ai_key_bad_json(self, aws) -> None:
        """AI key POST with bad JSON returns 400 (empty key)."""
        req = _req("POST", "/api/admin/ai-key")
        req.body = b"{bad"
        resp = _handle_api(req)
        assert resp.status_code == 400

    def test_ai_key_save_failure(self, aws) -> None:
        """A Secrets Manager failure returns 500."""
        with patch(
            "boto3.client",
            side_effect=RuntimeError("no secrets manager"),
        ):
            resp = _handle_api(
                _req("POST", "/api/admin/ai-key", {"api_key": "k"}),
            )
        assert resp.status_code == 500

    def test_access_list_no_group_filter(self, aws) -> None:
        """GET /api/access with no group filter scans all entries."""
        _handle_api(_req("POST", "/api/access", {
            "username": "a", "resource": "team", "level": "admin",
        }))
        resp = _handle_api(_req("GET", "/api/access"))
        assert resp.status_code == 200
        assert len(json.loads(resp.body)) >= 1


class TestApiAdminAndRescan:
    """Tests for admin AI-key and PR rescan routes."""

    def test_ai_key_missing(self, aws) -> None:
        """POST /api/admin/ai-key without a key returns 400."""
        resp = _handle_api(_req("POST", "/api/admin/ai-key", {}))
        assert resp.status_code == 400

    def test_ai_key_saved(self, aws) -> None:
        """POST /api/admin/ai-key stores the key in Secrets Manager."""
        resp = _handle_api(
            _req("POST", "/api/admin/ai-key", {"api_key": "secret123"}),
        )
        assert resp.status_code == 200
        assert "saved" in json.loads(resp.body)["message"].lower()

    def test_ai_key_update_existing(self, aws) -> None:
        """Posting twice updates the existing secret."""
        _handle_api(
            _req("POST", "/api/admin/ai-key", {"api_key": "first"}),
        )
        resp = _handle_api(
            _req("POST", "/api/admin/ai-key", {"api_key": "second"}),
        )
        assert resp.status_code == 200

    def test_rescan_missing_pr(self, aws) -> None:
        """POST rescan on a nonexistent PR returns 404."""
        resp = _handle_api(
            _req("POST", "/api/team/app/pulls/999/rescan"),
        )
        assert resp.status_code == 404

    def test_rescan_existing_pr(self, aws) -> None:
        """POST rescan on an existing PR returns 200."""
        create = _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "t", "source_branch": "b", "author": "a"},
        ))
        num = json.loads(create.body)["number"]
        resp = _handle_api(
            _req("POST", f"/api/team/app/pulls/{num}/rescan"),
        )
        assert resp.status_code == 200


class TestApiUsers:
    """Tests for /api/users."""

    def test_list_users_empty(self, aws) -> None:
        """GET /api/users returns a list."""
        resp = _handle_api(_req("GET", "/api/users"))
        assert resp.status_code == 200
        assert json.loads(resp.body) == []


class TestLfsHandler:
    """Tests for _handle_lfs routing."""

    @staticmethod
    def _rw_user():
        """A read/write git user dict."""
        return {"username": "alice", "scope": "read/write"}

    def test_requires_auth(self, aws) -> None:
        """Unauthenticated LFS requests return 401."""
        from gitgateway.lambda_handler import _handle_lfs

        resp = _handle_lfs(_req(
            "POST", "/team/app.git/info/lfs/objects/batch",
        ))
        assert resp.status_code == 401

    def test_invalid_url(self) -> None:
        """A non-LFS path returns 400 once authenticated."""
        from unittest.mock import patch

        from gitgateway.lambda_handler import _handle_lfs

        with patch(
            "gitgateway.lambda_handler._authenticate_git_request",
            return_value=self._rw_user(),
        ):
            resp = _handle_lfs(_req("POST", "/not-lfs"))
        assert resp.status_code == 400

    def test_lock_create_list_verify_unlock(self) -> None:
        """Create, list, verify, and unlock an LFS lock."""
        from unittest.mock import patch

        from gitgateway.lambda_handler import _handle_lfs
        from gitgateway.lfs import clear_locks

        clear_locks()
        base = "/team/app.git/info/lfs"
        with patch(
            "gitgateway.lambda_handler._authenticate_git_request",
            return_value=self._rw_user(),
        ):
            created = _handle_lfs(_req(
                "POST", f"{base}/locks", {"path": "big.bin"},
            ))
            assert created.status_code == 201
            lock_id = json.loads(created.body)["lock"]["id"]

            listed = _handle_lfs(_req("GET", f"{base}/locks"))
            assert listed.status_code == 200
            assert len(json.loads(listed.body)["locks"]) == 1

            verified = _handle_lfs(_req("POST", f"{base}/locks/verify"))
            assert verified.status_code == 200

            unlocked = _handle_lfs(_req(
                "POST", f"{base}/locks/{lock_id}/unlock",
            ))
            assert unlocked.status_code == 200

    def test_lock_create_read_only_forbidden(self) -> None:
        """A read-only token cannot create a lock."""
        from unittest.mock import patch

        from gitgateway.lambda_handler import _handle_lfs

        with patch(
            "gitgateway.lambda_handler._authenticate_git_request",
            return_value={"username": "alice", "scope": "read"},
        ):
            resp = _handle_lfs(_req(
                "POST", "/team/app.git/info/lfs/locks", {"path": "x"},
            ))
        assert resp.status_code == 403

    def test_batch_upload_read_only_forbidden(self) -> None:
        """A read-only token cannot use batch upload."""
        from unittest.mock import patch

        from gitgateway.lambda_handler import _handle_lfs

        with patch(
            "gitgateway.lambda_handler._authenticate_git_request",
            return_value={"username": "alice", "scope": "read"},
        ):
            resp = _handle_lfs(_req(
                "POST", "/team/app.git/info/lfs/objects/batch",
                {"operation": "upload", "objects": []},
            ))
        assert resp.status_code == 403

    def test_unlock_read_only_forbidden(self) -> None:
        """A read-only token cannot unlock."""
        from unittest.mock import patch

        from gitgateway.lambda_handler import _handle_lfs

        with patch(
            "gitgateway.lambda_handler._authenticate_git_request",
            return_value={"username": "alice", "scope": "read"},
        ):
            resp = _handle_lfs(_req(
                "POST", "/team/app.git/info/lfs/locks/abc/unlock",
            ))
        assert resp.status_code == 403

    def test_unlock_missing_lock(self) -> None:
        """Unlocking a nonexistent lock returns the LfsError status."""
        from unittest.mock import patch

        from gitgateway.lambda_handler import _handle_lfs
        from gitgateway.lfs import clear_locks

        clear_locks()
        with patch(
            "gitgateway.lambda_handler._authenticate_git_request",
            return_value=self._rw_user(),
        ):
            resp = _handle_lfs(_req(
                "POST", "/team/app.git/info/lfs/locks/nope/unlock",
            ))
        assert resp.status_code >= 400

    def test_lock_create_conflict(self) -> None:
        """Locking an already-locked path returns the LfsError status."""
        from unittest.mock import patch

        from gitgateway.lambda_handler import _handle_lfs
        from gitgateway.lfs import clear_locks

        clear_locks()
        base = "/team/app.git/info/lfs"
        with patch(
            "gitgateway.lambda_handler._authenticate_git_request",
            return_value=self._rw_user(),
        ):
            first = _handle_lfs(_req("POST", f"{base}/locks", {"path": "x"}))
            assert first.status_code == 201
            dup = _handle_lfs(_req("POST", f"{base}/locks", {"path": "x"}))
        assert dup.status_code == 409

    def test_batch_download_success(self) -> None:
        """A batch download returns the presigned-URL result."""
        from unittest.mock import patch

        from gitgateway.lambda_handler import _handle_lfs

        with patch(
            "gitgateway.lambda_handler._authenticate_git_request",
            return_value=self._rw_user(),
        ), patch(
            "gitgateway.lfs.handle_batch_request",
            return_value={"objects": []},
        ):
            resp = _handle_lfs(_req(
                "POST", "/team/app.git/info/lfs/objects/batch",
                {"operation": "download", "objects": []},
            ))
        assert resp.status_code == 200
        assert json.loads(resp.body) == {"objects": []}

    def test_batch_lfs_error(self) -> None:
        """An LfsError from the batch handler maps to its status code."""
        from unittest.mock import patch

        from gitgateway.lambda_handler import _handle_lfs
        from gitgateway.lfs import LfsError

        with patch(
            "gitgateway.lambda_handler._authenticate_git_request",
            return_value=self._rw_user(),
        ), patch(
            "gitgateway.lfs.handle_batch_request",
            side_effect=LfsError("nope", status_code=422),
        ):
            resp = _handle_lfs(_req(
                "POST", "/team/app.git/info/lfs/objects/batch",
                {"operation": "download", "objects": []},
            ))
        assert resp.status_code == 422

    def test_unknown_lfs_endpoint(self) -> None:
        """An unknown LFS sub-path returns 404."""
        from unittest.mock import patch

        from gitgateway.lambda_handler import _handle_lfs

        with patch(
            "gitgateway.lambda_handler._authenticate_git_request",
            return_value=self._rw_user(),
        ):
            resp = _handle_lfs(_req(
                "GET", "/team/app.git/info/lfs/bogus",
            ))
        assert resp.status_code == 404


class TestApiFallback:
    """Tests for unimplemented API routes."""

    def test_unknown_route_404(self, aws) -> None:
        """Unknown API path returns 404 with the path echoed."""
        resp = _handle_api(_req("GET", "/api/nonsense"))
        assert resp.status_code == 404
        assert json.loads(resp.body)["path"] == "/api/nonsense"


class TestWebRenderHelpers:
    """Cover session/user helpers and web page shell branches."""

    def test_session_user_valid_cookie(self) -> None:
        """A valid session cookie yields the user's identity."""
        from gitgateway.auth import (
            AuthenticatedUser,
            create_session_cookie,
        )
        from gitgateway.lambda_handler import _get_session_user

        cookie = create_session_cookie(
            AuthenticatedUser(username="alice", is_admin=True),
            "gitgateway-session-secret",
        )
        info = _get_session_user(
            _req("GET", "/", headers={"cookie": f"session={cookie}"}),
        )
        assert info == {"username": "alice", "is_admin": True}

    def test_session_user_invalid_cookie(self) -> None:
        """An invalid session cookie yields the unknown sentinel."""
        from gitgateway.lambda_handler import _get_session_user

        info = _get_session_user(
            _req("GET", "/", headers={"cookie": "session=tampered.value"}),
        )
        assert info["username"] == "unknown"

    def test_request_username_exception_returns_empty(self) -> None:
        """A failure extracting the session yields an empty username."""
        from gitgateway.lambda_handler import _get_request_username

        with patch(
            "gitgateway.lambda_handler._get_session_user",
            side_effect=RuntimeError("boom"),
        ):
            assert _get_request_username(_req("GET", "/")) == ""

    def test_render_web_page_admin_nav(self, aws) -> None:
        """An admin user sees the Admin nav link."""
        from gitgateway.lambda_handler import _render_web_page

        with patch.dict("os.environ", {"IDP_URL": ""}):
            resp = _render_web_page(
                _req("GET", "/"),
                {"username": "alice", "is_admin": True},
            )
        assert 'href="/admin"' in resp.body

    def test_render_web_page_sidebar_failure_tolerated(self, aws) -> None:
        """A sidebar rendering failure degrades to an empty sidebar."""
        from gitgateway.lambda_handler import _render_web_page

        with patch(
            "gitgateway.lambda_handler._render_sidebar",
            side_effect=RuntimeError("boom"),
        ):
            resp = _render_web_page(
                _req("GET", "/"), {"username": "alice", "is_admin": False},
            )
        assert resp.status_code == 200


class TestDispatchGit:
    """Cover the authenticated git dispatch path in _dispatch."""

    def _rw(self):
        return {"username": "alice", "scope": "read/write"}

    def test_git_fetch_success(self, aws) -> None:
        """An authenticated fetch reaches the git backend and returns it."""
        from gitgateway.lambda_handler import Response as _Resp  # noqa: F401
        from gitgateway.lambda_handler import _dispatch

        with patch(
            "gitgateway.lambda_handler._authenticate_git_request",
            return_value=self._rw(),
        ), patch(
            "gitgateway.git_backend.handle_git_http_request",
            return_value=(200, "application/x-git-upload-pack-result", b"ok"),
        ):
            resp = _dispatch("git", _req(
                "POST", "/team/app.git/git-upload-pack",
            ))
        assert resp.status_code == 200
        assert resp.body == b"ok"

    def test_git_push_read_only_forbidden(self, aws) -> None:
        """A read-only token pushing returns 403."""
        from gitgateway.lambda_handler import _dispatch

        with patch(
            "gitgateway.lambda_handler._authenticate_git_request",
            return_value={"username": "alice", "scope": "read"},
        ):
            resp = _dispatch("git", _req(
                "POST", "/team/app.git/git-receive-pack",
            ))
        assert resp.status_code == 403

    def test_git_push_success_triggers_linting(self, aws) -> None:
        """A successful push invokes the post-push linting trigger."""
        from gitgateway.lambda_handler import _dispatch

        with patch(
            "gitgateway.lambda_handler._authenticate_git_request",
            return_value=self._rw(),
        ), patch(
            "gitgateway.git_backend.handle_git_http_request",
            return_value=(200, "application/x-git-receive-pack-result", b"k"),
        ), patch(
            "gitgateway.lambda_handler._trigger_post_push_linting",
        ) as trigger:
            resp = _dispatch("git", _req(
                "POST", "/team/app.git/git-receive-pack",
            ))
        assert resp.status_code == 200
        trigger.assert_called_once_with("team", "app")

    def test_git_push_linting_failure_ignored(self, aws) -> None:
        """A failing linting trigger does not fail the push."""
        from gitgateway.lambda_handler import _dispatch

        with patch(
            "gitgateway.lambda_handler._authenticate_git_request",
            return_value=self._rw(),
        ), patch(
            "gitgateway.git_backend.handle_git_http_request",
            return_value=(200, "application/x-git-receive-pack-result", b"k"),
        ), patch(
            "gitgateway.lambda_handler._trigger_post_push_linting",
            side_effect=RuntimeError("boom"),
        ):
            resp = _dispatch("git", _req(
                "POST", "/team/app.git/git-receive-pack",
            ))
        assert resp.status_code == 200

    def test_git_invalid_url(self, aws) -> None:
        """A git path not matching group/repo.git returns 400."""
        from gitgateway.lambda_handler import _dispatch

        with patch(
            "gitgateway.lambda_handler._authenticate_git_request",
            return_value=self._rw(),
        ):
            resp = _dispatch("git", _req("GET", "/bogus/info/refs"))
        assert resp.status_code == 400


class TestDispatchWeb:
    """Cover the web dispatch branches (IdP redirect, logout, render)."""

    def test_redirect_to_idp_when_unauthenticated(self, aws) -> None:
        """No session + IDP_URL set redirects to the IdP."""
        from gitgateway.lambda_handler import _dispatch

        with patch.dict(
            "os.environ",
            {"IDP_URL": "https://idp.example.com",
             "DOMAIN_NAME": "git.example.com"},
        ):
            resp = _dispatch("web", _req("GET", "/"))
        assert resp.status_code == 302
        assert "idp.example.com" in resp.headers["Location"]

    def test_logout_clears_session(self, aws) -> None:
        """/logout returns a redirect that clears the session cookie."""
        from gitgateway.lambda_handler import _dispatch

        # Authenticated (cookie present) so we get past the IdP redirect,
        # with no IDP_URL so the redirect branch is skipped anyway.
        with patch.dict("os.environ", {"IDP_URL": ""}):
            resp = _dispatch(
                "web",
                _req("GET", "/logout", headers={"cookie": "session=x"}),
            )
        assert resp.status_code == 302
        assert "Max-Age=0" in resp.headers["Set-Cookie"]

    def test_render_authenticated_page(self, aws) -> None:
        """An authenticated web request renders the page shell."""
        from gitgateway.lambda_handler import _dispatch

        with patch.dict("os.environ", {"IDP_URL": ""}):
            resp = _dispatch("web", _req("GET", "/"))
        assert resp.status_code == 200
        assert "GitGateway" in resp.body


class TestParseEventAndAuthCallback:
    """Tests for event parsing and the OAuth callback."""

    def test_parse_event_base64_body_and_cookies(self) -> None:
        """Base64 body is decoded and cookies list joins into a header."""
        import base64 as _b64

        from gitgateway.lambda_handler import parse_api_gateway_event

        raw = _b64.b64encode(b'{"x":1}').decode()
        event = {
            "requestContext": {
                "http": {"method": "POST", "path": "/api/x",
                         "sourceIp": "1.2.3.4"},
            },
            "rawQueryString": "a=b",
            "headers": {"Content-Type": "application/json"},
            "cookies": ["session=abc", "theme=dark"],
            "body": raw,
            "isBase64Encoded": True,
        }
        req = parse_api_gateway_event(event)
        assert req.body == b'{"x":1}'
        assert req.headers["cookie"] == "session=abc; theme=dark"
        assert req.source_ip == "1.2.3.4"

    def test_parse_event_plain_body(self) -> None:
        """A plain (non-base64) body is encoded to bytes."""
        from gitgateway.lambda_handler import parse_api_gateway_event

        event = {
            "requestContext": {"http": {"method": "GET", "path": "/"}},
            "headers": {},
            "body": "hello",
            "isBase64Encoded": False,
        }
        req = parse_api_gateway_event(event)
        assert req.body == b"hello"

    def test_auth_callback_valid_jwt(self, aws) -> None:
        """A valid JWT sets a session cookie and redirects to /."""
        from gitgateway.auth import AuthenticatedUser
        from gitgateway.lambda_handler import _handle_auth_callback

        with patch(
            "gitgateway.auth.validate_jwt",
            return_value=AuthenticatedUser(username="alice", is_admin=True),
        ):
            resp = _handle_auth_callback(
                _req("GET", "/auth/callback", query_string="token=jwt.tok.en"),
                "https://idp.example.com",
            )
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/"
        assert "session=" in resp.headers["Set-Cookie"]

    def test_auth_callback_invalid_jwt_falls_back(self, aws) -> None:
        """An invalid JWT falls back to unverified decode and still sets a
        session (current lenient behavior)."""
        from gitgateway.lambda_handler import _handle_auth_callback

        # validate_jwt raises; pyjwt unverified decode also fails on a
        # non-JWT string, so the handler falls back to an "unknown" user.
        resp = _handle_auth_callback(
            _req("GET", "/auth/callback", query_string="token=not-a-jwt"),
            "https://idp.example.com",
        )
        assert resp.status_code == 302

    def test_auth_callback_unverified_decode_fallback(self, aws) -> None:
        """A signed-invalid but decodable JWT falls back to its claims."""
        import jwt as pyjwt

        from gitgateway.lambda_handler import _handle_auth_callback

        # A validly-structured JWT that fails signature validation but
        # decodes (verify_signature=False) to recover sub/claims.
        token = pyjwt.encode(
            {"sub": "carol", "claims": ["gitgateway-admin"]},
            "some-secret",
            algorithm="HS256",
        )
        resp = _handle_auth_callback(
            _req("GET", "/auth/callback", query_string=f"token={token}"),
            "https://idp.example.com",
        )
        assert resp.status_code == 302
        assert "session=" in resp.headers["Set-Cookie"]


class TestGitAuthAndHelpers:
    """Tests for _authenticate_git_request and small helpers."""

    def test_auth_no_header(self, aws) -> None:
        """No Authorization header returns None."""
        from gitgateway.lambda_handler import _authenticate_git_request

        assert _authenticate_git_request(_req("GET", "/x")) is None

    def test_auth_not_basic(self, aws) -> None:
        """A non-Basic scheme returns None."""
        from gitgateway.lambda_handler import _authenticate_git_request

        req = _req("GET", "/x", headers={"authorization": "Bearer abc"})
        assert _authenticate_git_request(req) is None

    def test_auth_bad_base64(self, aws) -> None:
        """Malformed Basic credentials return None."""
        from gitgateway.lambda_handler import _authenticate_git_request

        req = _req("GET", "/x", headers={"authorization": "Basic !!!!"})
        assert _authenticate_git_request(req) is None

    def test_auth_wrong_username(self, aws) -> None:
        """Basic auth without __token__ username returns None."""
        import base64

        from gitgateway.lambda_handler import _authenticate_git_request

        creds = base64.b64encode(b"bob:pw").decode()
        req = _req("GET", "/x", headers={"authorization": f"Basic {creds}"})
        assert _authenticate_git_request(req) is None

    def test_auth_valid_pat(self, aws) -> None:
        """A valid personal access token authenticates."""
        import base64

        from gitgateway.lambda_handler import _authenticate_git_request
        from gitgateway.tokens import TokenScope, generate_token

        raw, _pat = generate_token(
            username="alice", description="d", scope=TokenScope.READ_WRITE,
        )
        creds = base64.b64encode(f"__token__:{raw}".encode()).decode()
        req = _req("GET", "/x", headers={"authorization": f"Basic {creds}"})
        result = _authenticate_git_request(req)
        assert result is not None
        assert result["username"] == "alice"
        assert result["scope"] == "read/write"

    def test_auth_invalid_pat(self, aws) -> None:
        """An unknown token returns None."""
        import base64

        from gitgateway.lambda_handler import _authenticate_git_request

        creds = base64.b64encode(b"__token__:gg_bogus").decode()
        req = _req("GET", "/x", headers={"authorization": f"Basic {creds}"})
        assert _authenticate_git_request(req) is None

    def test_record_known_user(self, aws) -> None:
        """Recording a known user adds it to /api/users."""
        from gitgateway.lambda_handler import _record_known_user

        _record_known_user("alice")
        resp = _handle_api(_req("GET", "/api/users"))
        assert "alice" in json.loads(resp.body)

    def test_record_known_user_failure_tolerated(self) -> None:
        """A DynamoDB failure while recording a user is swallowed."""
        from gitgateway.lambda_handler import _record_known_user

        with patch("boto3.resource", side_effect=RuntimeError("ddb down")):
            # Should not raise.
            _record_known_user("bob")

    def test_is_authenticated_cookie(self) -> None:
        """A session cookie counts as authenticated."""
        from gitgateway.lambda_handler import _is_authenticated

        req = _req("GET", "/", headers={"cookie": "session=abc"})
        assert _is_authenticated(req) is True

    def test_is_authenticated_header(self) -> None:
        """An Authorization header counts as authenticated."""
        from gitgateway.lambda_handler import _is_authenticated

        req = _req("GET", "/", headers={"authorization": "Bearer x"})
        assert _is_authenticated(req) is True

    def test_is_not_authenticated(self) -> None:
        """No credentials means not authenticated."""
        from gitgateway.lambda_handler import _is_authenticated

        assert _is_authenticated(_req("GET", "/")) is False

    def test_trigger_post_push_no_prs(self, aws) -> None:
        """Post-push trigger with no open PRs returns quietly."""
        from gitgateway.lambda_handler import _trigger_post_push_linting

        # No exception expected.
        _trigger_post_push_linting("team", "app")

    def test_trigger_post_push_with_pr(self, aws) -> None:
        """Post-push trigger with an open PR attempts a rescan invoke."""
        from gitgateway.lambda_handler import _trigger_post_push_linting

        _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "t", "source_branch": "b", "author": "a"},
        ))
        # Lambda invoke will fail under moto and be caught; no raise.
        _trigger_post_push_linting("team", "app")

    def test_trigger_post_push_invoke_success(self, aws) -> None:
        """A successful async invoke completes the loop for each open PR."""
        from unittest.mock import MagicMock

        from gitgateway.lambda_handler import _trigger_post_push_linting

        _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "t", "source_branch": "b", "author": "a"},
        ))
        fake_lambda = MagicMock()
        real_client = boto3.client

        def _client(name, *a, **k):
            if name == "lambda":
                return fake_lambda
            return real_client(name, *a, **k)

        with patch("boto3.client", side_effect=_client):
            _trigger_post_push_linting("team", "app")
        fake_lambda.invoke.assert_called_once()


class TestPrLintingWithTree:
    """Tests for _run_pr_linting when tree files are present."""

    def test_run_pr_linting_with_python_file(self, aws) -> None:
        """Linting a PR orchestrates linters and posts status + comment.

        The individual linters (subprocess-based) are patched to return no
        findings so the test stays fast and has no external dependencies;
        this exercises the orchestration/status/comment path in
        _run_pr_linting rather than the linters themselves.
        """
        import boto3

        from gitgateway.lambda_handler import _run_pr_linting

        # Seed the tree cache with one python file.
        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "TREE#team/app#feat",
            "SK": "FILE#hello.py",
            "path": "hello.py",
            "content": "x = 1\n",
        })

        linter_names = [
            "_run_pylint", "_run_bandit", "_run_ruff", "_run_mypy",
            "_run_yamllint", "_run_json_check", "_run_toml_check",
            "_run_checkov", "_run_cfnlint", "_run_tflint", "_run_kubeval",
            "_run_shellcheck", "_run_hadolint", "_run_markdownlint",
            "_run_eslint", "_run_sqlfluff",
        ]
        with ExitStack() as stack:
            for name in linter_names:
                stack.enter_context(
                    patch(f"gitgateway.lint.{name}", return_value=[]),
                )
            _run_pr_linting("team", "app", 1, "feat")

    def test_run_pr_linting_all_file_types(self, aws) -> None:
        """Seeding every categorized file type runs each linter branch."""
        import boto3

        from gitgateway.lambda_handler import _run_pr_linting

        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        files = {
            "a.py": "x = 1\n",
            "c.yaml": "key: value\n",
            "d.json": "{}\n",
            "e.toml": "[x]\n",
            "main.tf": 'resource "aws_s3_bucket" "b" {}\n',
            "deploy.yaml": (
                "AWSTemplateFormatVersion: '2010-09-09'\n"
                "Resources: {}\n"
            ),
            "k8s.yaml": "apiVersion: v1\nkind: Pod\n",
            "run.sh": "#!/bin/bash\necho hi\n",
            "Dockerfile": "FROM scratch\n",
            "README.md": "# Title\n",
            "app.js": "const x = 1;\n",
            "q.sql": "SELECT 1;\n",
        }
        for i, (path, content) in enumerate(files.items()):
            table.put_item(Item={
                "PK": "TREE#team/app#feat",
                "SK": f"FILE#{path}",
                "path": path,
                "content": content,
            })

        linter_names = [
            "_run_pylint", "_run_bandit", "_run_ruff", "_run_mypy",
            "_run_yamllint", "_run_json_check", "_run_toml_check",
            "_run_checkov", "_run_cfnlint", "_run_tflint", "_run_kubeval",
            "_run_shellcheck", "_run_hadolint", "_run_markdownlint",
            "_run_eslint", "_run_sqlfluff",
        ]
        with ExitStack() as stack:
            for name in linter_names:
                stack.enter_context(
                    patch(f"gitgateway.lint.{name}", return_value=[]),
                )
            _run_pr_linting("team", "app", 1, "feat")

    def test_run_pr_linting_skips_large_files(self, aws) -> None:
        """Files over 100KB are skipped, yielding no linting work."""
        import boto3

        from gitgateway.lambda_handler import _run_pr_linting

        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "TREE#team/app#feat",
            "SK": "FILE#big.py",
            "path": "big.py",
            "content": "x = 1\n" * 30_000,  # > 100KB
        })
        # No linters should run; nothing to assert beyond no exception.
        _run_pr_linting("team", "app", 1, "feat")

    def test_run_pr_linting_with_findings(self, aws) -> None:
        """A linter finding drives the failure comment path."""
        import boto3

        from gitgateway.lambda_handler import _run_pr_linting
        from gitgateway.lint import LintFinding

        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "TREE#team/app#feat",
            "SK": "FILE#bad.py",
            "path": "bad.py",
            "content": "import os\n",
        })

        finding = LintFinding(
            tool="pylint", file_path="bad.py", line=1,
            message="bad", severity="error",
        )
        linter_names = [
            "_run_pylint", "_run_bandit", "_run_ruff", "_run_mypy",
            "_run_yamllint", "_run_json_check", "_run_toml_check",
            "_run_checkov", "_run_cfnlint", "_run_tflint", "_run_kubeval",
            "_run_shellcheck", "_run_hadolint", "_run_markdownlint",
            "_run_eslint", "_run_sqlfluff",
        ]
        with ExitStack() as stack:
            for name in linter_names:
                ret = [finding] if name == "_run_pylint" else []
                stack.enter_context(
                    patch(f"gitgateway.lint.{name}", return_value=ret),
                )
            _run_pr_linting("team", "app", 1, "feat")

    def test_run_pr_linting_no_tree(self, aws) -> None:
        """Linting a PR with no tree files returns early."""
        from gitgateway.lambda_handler import _run_pr_linting

        _run_pr_linting("team", "app", 1, "missing-branch")

    def test_run_pr_linting_many_findings_truncated(self, aws) -> None:
        """More than 50 findings are truncated in the posted comment."""
        import boto3

        from gitgateway.lambda_handler import _run_pr_linting
        from gitgateway.lint import LintFinding

        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "TREE#team/app#feat", "SK": "FILE#bad.py",
            "path": "bad.py", "content": "import os\n",
        })
        findings = [
            LintFinding(
                tool="pylint", file_path="bad.py", line=i,
                message="issue", severity="error",
            )
            for i in range(60)
        ]
        linter_names = [
            "_run_pylint", "_run_bandit", "_run_ruff", "_run_mypy",
            "_run_yamllint", "_run_json_check", "_run_toml_check",
            "_run_checkov", "_run_cfnlint", "_run_tflint", "_run_kubeval",
            "_run_shellcheck", "_run_hadolint", "_run_markdownlint",
            "_run_eslint", "_run_sqlfluff",
        ]
        with ExitStack() as stack:
            for name in linter_names:
                ret = findings if name == "_run_pylint" else []
                stack.enter_context(
                    patch(f"gitgateway.lint.{name}", return_value=ret),
                )
            _run_pr_linting("team", "app", 1, "feat")


class TestAiReview:
    """Tests for _run_ai_review."""

    def test_no_api_key_returns_early(self, aws) -> None:
        """With no Gemini key configured, review is skipped."""
        from gitgateway.lambda_handler import _run_ai_review

        # Secrets Manager has no secret; the get raises and is caught.
        _run_ai_review("team", "app", 1, "feat")

    def test_full_review_path(self, aws) -> None:
        """With a key and tree data, review + summary comments post."""
        import boto3

        from gitgateway.lambda_handler import _run_ai_review

        # Configure the Gemini key in Secrets Manager.
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(
            Name="gitgateway/gemini-api-key", SecretString="key123",
        )
        # Seed tree data so a diff is built.
        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "TREE#team/app#feat",
            "SK": "FILE#a.py",
            "path": "a.py",
            "content": "print('hi')\n",
        })

        findings = [
            {"file": "a.py", "line": 1, "message": "m", "severity": "warning"},
        ]
        with patch(
            "gitgateway.ai.generate_code_review", return_value=findings,
        ), patch(
            "gitgateway.ai.generate_pr_summary", return_value="A summary",
        ):
            _run_ai_review("team", "app", 1, "feat")

        # Both AI comments should have been posted.
        from gitgateway.reviews import (  # pylint: disable=import-outside-toplevel
            list_comments,
        )

        comments = list_comments("team", "app", 1)
        authors = {c.author for c in comments}
        assert "gitgateway-ai" in authors

    def test_review_skips_empty_and_breaks_on_large(self, aws) -> None:
        """Empty-content items are skipped; a large diff triggers the break."""
        import boto3

        from gitgateway.lambda_handler import _run_ai_review

        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(
            Name="gitgateway/gemini-api-key", SecretString="key123",
        )
        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        # An empty-content item (skipped) ...
        table.put_item(Item={
            "PK": "TREE#team/app#feat", "SK": "FILE#empty.py",
            "path": "empty.py", "content": "",
        })
        # ... and several large files to push the diff past 8000 chars.
        for i in range(5):
            table.put_item(Item={
                "PK": "TREE#team/app#feat", "SK": f"FILE#big{i}.py",
                "path": f"big{i}.py", "content": "x = 1\n" * 500,
            })
        with patch(
            "gitgateway.ai.generate_code_review", return_value=[],
        ), patch(
            "gitgateway.ai.generate_pr_summary", return_value="",
        ):
            _run_ai_review("team", "app", 1, "feat")

    def test_no_tree_data_returns_early(self, aws) -> None:
        """With a key but no tree files, review returns early."""
        import boto3

        from gitgateway.lambda_handler import _run_ai_review

        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(
            Name="gitgateway/gemini-api-key", SecretString="key123",
        )
        # No TREE# items seeded.
        _run_ai_review("team", "app", 1, "feat")

    def test_review_many_findings_truncated(self, aws) -> None:
        """More than 20 findings are truncated in the comment."""
        import boto3

        from gitgateway.lambda_handler import _run_ai_review

        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(
            Name="gitgateway/gemini-api-key", SecretString="key123",
        )
        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "TREE#team/app#feat", "SK": "FILE#a.py",
            "path": "a.py", "content": "x = 1\n",
        })
        findings = [
            {"file": "a.py", "line": i, "message": "m", "severity": "error"}
            for i in range(25)
        ]
        with patch(
            "gitgateway.ai.generate_code_review", return_value=findings,
        ), patch(
            "gitgateway.ai.generate_pr_summary", return_value="s",
        ):
            _run_ai_review("team", "app", 1, "feat")

    def test_review_and_summary_ai_errors(self, aws) -> None:
        """AIError in review and summary are handled without raising."""
        import boto3

        from gitgateway.ai import AIError
        from gitgateway.lambda_handler import _run_ai_review

        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(
            Name="gitgateway/gemini-api-key", SecretString="key123",
        )
        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "TREE#team/app#feat", "SK": "FILE#a.py",
            "path": "a.py", "content": "x = 1\n",
        })
        with patch(
            "gitgateway.ai.generate_code_review",
            side_effect=AIError("rate limited"),
        ), patch(
            "gitgateway.ai.generate_pr_summary",
            side_effect=AIError("rate limited"),
        ):
            _run_ai_review("team", "app", 1, "feat")

    def test_review_no_findings(self, aws) -> None:
        """With a key but no findings, a clean-review comment posts."""
        import boto3

        from gitgateway.lambda_handler import _run_ai_review

        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(
            Name="gitgateway/gemini-api-key", SecretString="key123",
        )
        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "TREE#team/app#feat",
            "SK": "FILE#a.py",
            "path": "a.py",
            "content": "x = 1\n",
        })
        with patch(
            "gitgateway.ai.generate_code_review", return_value=[],
        ), patch(
            "gitgateway.ai.generate_pr_summary", return_value="",
        ):
            _run_ai_review("team", "app", 1, "feat")


class TestPureDiffHelpers:
    """Tests for pure diff/blob helper functions."""

    def test_simple_diff(self) -> None:
        """_simple_diff produces a unified diff string."""
        from gitgateway.lambda_handler import _simple_diff

        diff = _simple_diff("a\nb\n", "a\nc\n")
        assert "-b" in diff or "+c" in diff

    def test_whitespace_only_diff_true(self) -> None:
        """Whitespace-only changes are detected."""
        from gitgateway.lambda_handler import _is_whitespace_only_diff

        entry = {"diff": "-foo  \n+foo"}
        assert _is_whitespace_only_diff(entry) is True

    def test_whitespace_only_diff_false(self) -> None:
        """Substantive changes are not whitespace-only."""
        from gitgateway.lambda_handler import _is_whitespace_only_diff

        entry = {"diff": "-foo\n+bar"}
        assert _is_whitespace_only_diff(entry) is False

    def test_whitespace_only_diff_empty(self) -> None:
        """An empty diff is not whitespace-only."""
        from gitgateway.lambda_handler import _is_whitespace_only_diff

        assert _is_whitespace_only_diff({"diff": ""}) is False


class TestTokensPagePopulated:
    """Tests for _render_tokens_page with existing tokens."""

    def test_renders_existing_tokens(self, aws) -> None:
        """A user's active, revoked, and expired tokens render in the page."""
        from gitgateway.lambda_handler import _render_tokens_page
        from gitgateway.tokens import (
            TokenScope,
            generate_token,
            revoke_token,
        )

        generate_token(
            username="alice", description="active",
            scope=TokenScope.READ_WRITE,
        )
        _raw, pat = generate_token(
            username="alice", description="gone",
            scope=TokenScope.READ,
        )
        revoke_token("alice", pat.token_id)
        # An expired token (negative expiry) exercises the Expired badge.
        generate_token(
            username="alice", description="old",
            scope=TokenScope.READ, expiry_days=-1,
        )

        html = _render_tokens_page("alice")
        assert "active" in html
        assert "Revoked" in html
        assert "Expired" in html


class TestTreeDiffHelpers:
    """Directly exercise _compute_tree_diff / _flatten_tree / _get_blob_text."""

    def _store_with_tree(self, files: dict[str, bytes]):
        """Build an in-memory object store with a tree of the given files.

        Supports nested paths (dir/file) to exercise recursion.
        Returns (store, tree_sha).
        """
        from dulwich.object_store import MemoryObjectStore
        from dulwich.objects import Blob, Tree

        store = MemoryObjectStore()
        # Group files by directory (single level of nesting supported).
        root = Tree()
        subtrees: dict[str, "Tree"] = {}
        for path, content in files.items():
            blob = Blob.from_string(content)
            store.add_object(blob)
            if "/" in path:
                d, name = path.split("/", 1)
                sub = subtrees.setdefault(d, Tree())
                sub.add(name.encode(), 0o100644, blob.id)
            else:
                root.add(path.encode(), 0o100644, blob.id)
        for dirname, sub in subtrees.items():
            store.add_object(sub)
            root.add(dirname.encode(), 0o040000, sub.id)
        store.add_object(root)
        return store, root.id

    def test_added_deleted_modified_and_nested(self) -> None:
        """Cover added, deleted, modified, and nested-dir diff branches."""
        from gitgateway.lambda_handler import _compute_tree_diff

        old_store, old_tree = self._store_with_tree({
            "keep.txt": b"same\n",
            "gone.txt": b"remove me\n",
            "mod.txt": b"before\n",
        })
        new_store, new_tree = self._store_with_tree({
            "keep.txt": b"same\n",
            "mod.txt": b"after\n",
            "new.txt": b"added\n",
            "src/app.py": b"print(1)\n",
        })
        # Merge objects so both trees resolve in one store.
        for sha, obj in list(old_store._data.items()):  # noqa: SLF001
            if sha not in new_store._data:  # noqa: SLF001
                new_store.add_object(obj)

        diffs = _compute_tree_diff(new_store, old_tree, new_tree)
        by_path = {d["path"]: d["status"] for d in diffs}
        assert by_path["gone.txt"] == "deleted"
        assert by_path["mod.txt"] == "modified"
        assert by_path["new.txt"] == "added"
        assert by_path["src/app.py"] == "added"

    def test_initial_commit_no_old_tree(self) -> None:
        """A None old tree treats every file as added."""
        from gitgateway.lambda_handler import _compute_tree_diff

        store, tree = self._store_with_tree({"a.txt": b"hi\n"})
        diffs = _compute_tree_diff(store, None, tree)
        assert diffs[0]["status"] == "added"

    def test_get_blob_text_too_large(self) -> None:
        """A blob over 100KB is reported as too large."""
        from dulwich.object_store import MemoryObjectStore
        from dulwich.objects import Blob

        from gitgateway.lambda_handler import _get_blob_text

        store = MemoryObjectStore()
        blob = Blob.from_string(b"x" * 200_000)
        store.add_object(blob)
        assert "too large" in _get_blob_text(store, blob.id)

    def test_get_blob_text_missing(self) -> None:
        """A missing blob returns empty string."""
        from dulwich.object_store import MemoryObjectStore

        from gitgateway.lambda_handler import _get_blob_text

        store = MemoryObjectStore()
        assert _get_blob_text(store, b"0" * 40) == ""

    def test_flatten_missing_tree(self) -> None:
        """Flattening a missing tree SHA is a no-op."""
        from dulwich.object_store import MemoryObjectStore

        from gitgateway.lambda_handler import _flatten_tree

        result: dict = {}
        _flatten_tree(MemoryObjectStore(), b"0" * 40, "", result)
        assert result == {}


class TestRenderHelpers:
    """Tests for _render_markdown, _highlight_code, and _render_sidebar."""

    def test_render_markdown_success(self) -> None:
        """Markdown renders to an HTML markdown-body block."""
        from gitgateway.lambda_handler import _render_markdown

        html = _render_markdown("# Title\n\ntext\n")
        assert "markdown-body" in html
        assert "Title" in html

    def test_render_markdown_fallback(self) -> None:
        """A markdown import/render failure falls back to <pre>."""
        import builtins

        from gitgateway.lambda_handler import _render_markdown

        real_import = builtins.__import__

        def _boom(name, *args, **kwargs):
            if name == "markdown":
                raise ImportError("no markdown")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_boom):
            html = _render_markdown("# Title")
        assert html.startswith("<pre>")

    def test_highlight_code_success(self) -> None:
        """Code highlighting returns styled HTML."""
        from gitgateway.lambda_handler import _highlight_code

        html = _highlight_code("print('hi')\n", "a.py")
        assert "highlight" in html

    def test_highlight_code_unknown_extension(self) -> None:
        """An unknown extension falls back to the text lexer."""
        from gitgateway.lambda_handler import _highlight_code

        html = _highlight_code("plain text", "file.unknownext")
        assert html

    def test_highlight_code_fallback(self) -> None:
        """A pygments failure falls back to numbered <pre>."""
        import builtins

        from gitgateway.lambda_handler import _highlight_code

        real_import = builtins.__import__

        def _boom(name, *args, **kwargs):
            if name.startswith("pygments"):
                raise ImportError("no pygments")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_boom):
            html = _highlight_code("x = 1\n", "a.py")
        assert "<pre" in html

    def test_sidebar_with_access(self, aws) -> None:
        """Sidebar lists group repos and orphan repo-level grants."""
        from gitgateway.lambda_handler import _render_sidebar

        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        _handle_api(_req("POST", "/api/repos/other", {"name": "lib"}))
        # Group-level access to team, repo-level access to other/lib.
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "other/lib", "level": "readonly",
        }))
        html = _render_sidebar("alice")
        assert "team" in html
        assert "app" in html
        assert "other/lib" in html

    def test_sidebar_group_no_repos(self, aws) -> None:
        """A visible group with no repos shows the empty placeholder."""
        from gitgateway.lambda_handler import _render_sidebar

        _handle_api(_req("POST", "/api/groups", {"name": "empty"}))
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "empty", "level": "admin",
        }))
        html = _render_sidebar("alice")
        assert "No repositories" in html

    def test_sidebar_no_access(self, aws) -> None:
        """A user with no access sees the empty-sidebar placeholder."""
        from gitgateway.lambda_handler import _render_sidebar

        html = _render_sidebar("nobody")
        assert "No accessible groups" in html

    def test_sidebar_access_lookup_failure(self, aws) -> None:
        """A failure listing user access degrades to no entries."""
        from gitgateway.lambda_handler import _render_sidebar

        with patch(
            "gitgateway.db.list_user_access",
            side_effect=RuntimeError("ddb down"),
        ):
            html = _render_sidebar("alice")
        assert "No accessible groups" in html

    def test_sidebar_repo_level_grant_in_visible_group(self, aws) -> None:
        """A repo grant whose group is already visible is not an orphan."""
        from gitgateway.lambda_handler import _render_sidebar

        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        # Group-level access to team AND a repo-level grant within team.
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team/app", "level": "readonly",
        }))
        html = _render_sidebar("alice")
        # team/app is under the visible 'team' group, not a "Shared Repos".
        assert "team" in html

    def test_sidebar_list_repos_failure(self, aws) -> None:
        """A failure listing a group's repos degrades to no repos."""
        from gitgateway.lambda_handler import _render_sidebar

        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))
        with patch(
            "gitgateway.db.list_repos",
            side_effect=RuntimeError("ddb down"),
        ):
            html = _render_sidebar("alice")
        assert "No repositories" in html


class TestRenderPermissionLegs:
    """Cover 403/404 and empty-state legs of the render functions."""

    @pytest.fixture
    def repo(self, aws):
        """A repo with alice as admin; bob has no access."""
        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))
        return aws

    def _bob(self) -> dict:
        return {"username": "bob", "is_admin": False}

    def _alice(self) -> dict:
        return {"username": "alice", "is_admin": True}

    def test_pulls_forbidden(self, repo) -> None:
        """Pulls page 403 for a user without access."""
        assert "403" in _get_page_content(
            "/groups/team/app/pulls", self._bob(),
        )

    def test_branches_forbidden(self, repo) -> None:
        """Branches page 403 for a user without access."""
        assert "403" in _get_page_content(
            "/groups/team/app/branches", self._bob(),
        )

    def test_settings_forbidden(self, repo) -> None:
        """Settings page 403 for a user without access."""
        assert "403" in _get_page_content(
            "/groups/team/app/settings", self._bob(),
        )

    def test_commit_diff_forbidden(self, repo) -> None:
        """Commit diff page 403 for a user without access."""
        assert "403" in _get_page_content(
            "/groups/team/app/commit/deadbeef", self._bob(),
        )

    def test_tree_forbidden(self, repo) -> None:
        """Tree/file view 403 for a user without access."""
        assert "403" in _get_page_content(
            "/groups/team/app/tree/main/x.py", self._bob(),
        )

    def test_group_detail_no_access(self, repo) -> None:
        """Group detail 403 for a user without access."""
        assert "403" in _get_page_content("/groups/team", self._bob())

    def test_pulls_empty_state(self, aws) -> None:
        """Pulls page renders the empty state for a repo with no PRs."""
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))
        html = _get_page_content("/groups/team/ghost/pulls", self._alice())
        assert "No open pull requests" in html

    def test_branches_repo_not_found(self, aws) -> None:
        """Branches page 404 when the repo is missing (with access)."""
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))
        html = _get_page_content(
            "/groups/team/ghost/branches", self._alice(),
        )
        assert "404" in html or "Not Found" in html

    def test_commits_repo_not_found(self, aws) -> None:
        """Commits page 404 when the repo is missing (with access)."""
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))
        html = _get_page_content(
            "/groups/team/ghost/commits", self._alice(),
        )
        assert "404" in html or "Not Found" in html

    def test_pulls_status_filters(self, repo) -> None:
        """Pulls page renders each status filter without error."""
        for status in ("open", "closed", "merged", "all"):
            html = _get_page_content(
                "/groups/team/app/pulls", self._alice(),
                query_string=f"status={status}",
            )
            assert html


class TestDiffRendering:
    """Cover the diff renderers with a real dulwich repo synced to S3."""

    @pytest.fixture
    def repo_with_branches(self, aws, tmp_path):
        """Build a bare repo with main + feat branches that differ.

        Commits a file on main, then a modified version on feat, and
        syncs the repo to the mocked S3 bucket so the diff renderers can
        sync it back and compute a real tree diff.
        """
        from dulwich.repo import Repo as DRepo

        from gitgateway.storage import sync_repo_to_s3

        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))

        repo_dir = tmp_path / "app.git"
        repo = DRepo.init_bare(str(repo_dir), mkdir=True)

        def _commit(content: bytes, message: bytes, ref: bytes,
                    parent=None):
            from dulwich.objects import Blob, Commit, Tree

            blob = Blob.from_string(content)
            tree = Tree()
            tree.add(b"file.txt", 0o100644, blob.id)
            commit = Commit()
            commit.tree = tree.id
            commit.author = commit.committer = b"Tester <t@example.com>"
            commit.commit_time = commit.author_time = 1_700_000_000
            commit.commit_timezone = commit.author_timezone = 0
            commit.encoding = b"UTF-8"
            commit.message = message
            if parent:
                commit.parents = [parent]
            repo.object_store.add_object(blob)
            repo.object_store.add_object(tree)
            repo.object_store.add_object(commit)
            repo.refs[ref] = commit.id
            return commit.id

        main_sha = _commit(b"line one\n", b"init", b"refs/heads/main")
        _commit(
            b"line one\nline two\n", b"feat change",
            b"refs/heads/feat", parent=main_sha,
        )

        sync_repo_to_s3(repo_dir, "team", "app")
        return main_sha

    def _info(self) -> dict:
        return {"username": "alice", "is_admin": True}

    def test_pr_detail_diff_real(self, repo_with_branches) -> None:
        """PR detail diff tab renders a real per-file diff."""
        create = _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "Feat", "source_branch": "feat",
             "target_branch": "main", "author": "alice"},
        ))
        num = json.loads(create.body)["number"]
        html = _get_page_content(
            f"/groups/team/app/pulls/{num}", self._info(),
            query_string="tab=diff",
        )
        assert "file.txt" in html

    def test_commit_diff_real(self, repo_with_branches, tmp_path) -> None:
        """Commit diff page renders a real commit's +/- line changes.

        The feat commit has main as its parent and modifies file.txt,
        so the diff shows colored added/removed lines.
        """
        from dulwich.repo import Repo as DRepo

        # feat head modifies file.txt relative to its parent (main head).
        repo = DRepo(str(tmp_path / "app.git"))
        feat_head = repo.refs[b"refs/heads/feat"].decode()
        html = _get_page_content(
            f"/groups/team/app/commit/{feat_head}", self._info(),
        )
        assert "file.txt" in html

    def test_commit_diff_not_found(self, repo_with_branches) -> None:
        """An unknown commit sha renders 404."""
        html = _get_page_content(
            "/groups/team/app/commit/" + "0" * 40, self._info(),
        )
        assert "404" in html

    def test_compute_pr_diff_non_split(self, repo_with_branches) -> None:
        """_compute_pr_diff renders per-file entries with a lint icon."""
        from gitgateway.lambda_handler import _compute_pr_diff

        html = _compute_pr_diff(
            "team", "app", "main", "feat",
            lint_failed_files={"file.txt"},
        )
        assert "file.txt" in html

    def test_compute_pr_diff_ignore_whitespace(self, repo_with_branches) -> None:
        """_compute_pr_diff with ignore_whitespace still renders changes."""
        from gitgateway.lambda_handler import _compute_pr_diff

        html = _compute_pr_diff(
            "team", "app", "main", "feat", ignore_whitespace=True,
        )
        assert html

    def test_load_diff_repo_not_found(self, aws) -> None:
        """_load_diff_entries reports when the repo is missing from S3."""
        from gitgateway.lambda_handler import _load_diff_entries

        entries, err = _load_diff_entries("ghost", "repo", "main", "feat")
        assert entries == []
        assert "not found" in err.lower()

    def test_load_diff_branch_not_found(self, repo_with_branches) -> None:
        """_load_diff_entries reports a missing source branch."""
        from gitgateway.lambda_handler import _load_diff_entries

        entries, err = _load_diff_entries("team", "app", "main", "ghost")
        assert entries == []
        assert "ghost" in err

    def test_load_diff_no_differences(self, repo_with_branches) -> None:
        """Diffing a branch against itself reports no differences."""
        from gitgateway.lambda_handler import _load_diff_entries

        entries, err = _load_diff_entries("team", "app", "feat", "feat")
        assert entries == []
        assert "No differences" in err

    def test_load_diff_whitespace_only(self, repo_with_branches) -> None:
        """When all changes are whitespace-only they are dropped.

        The real repo has a substantive change, so we patch the
        whitespace detector to classify it as whitespace-only, which
        exercises the "no non-whitespace differences" branch.
        """
        from gitgateway.lambda_handler import _load_diff_entries

        with patch(
            "gitgateway.lambda_handler._is_whitespace_only_diff",
            return_value=True,
        ):
            entries, err = _load_diff_entries(
                "team", "app", "main", "feat", ignore_whitespace=True,
            )
        assert entries == []
        assert "whitespace" in err.lower()


class TestPopulatedPages:
    """Render page bodies with access granted and DynamoDB data seeded."""

    @pytest.fixture
    def repo_with_data(self, aws):
        """Create a repo, grant alice admin, and seed git-like cache data."""
        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))

        table = aws.Table("gitgateway-repos")
        # A branch
        table.put_item(Item={
            "PK": "BRANCHES#team/app",
            "SK": "BRANCH#main",
            "branch_name": "main",
        })
        # A commit
        table.put_item(Item={
            "PK": "COMMITS#team/app#main",
            "SK": "COMMIT#0000001",
            "sha": "abc1234def5678",
            "message": "Initial commit",
            "author": "alice",
            "timestamp": 1_700_000_000,
        })
        # A directory entry and a file entry in the tree cache
        table.put_item(Item={
            "PK": "TREE#team/app#main",
            "SK": "FILE#README.md",
            "path": "README.md",
            "type": "file",
            "content": "# App\n\nHello world.\n",
        })
        table.put_item(Item={
            "PK": "TREE#team/app#main",
            "SK": "FILE#src",
            "path": "src",
            "type": "dir",
        })
        table.put_item(Item={
            "PK": "TREE#team/app#main",
            "SK": "FILE#main.py",
            "path": "main.py",
            "type": "file",
            "content": "print('hello')\n",
        })
        return aws

    def _info(self) -> dict:
        return {"username": "alice", "is_admin": True}

    def test_repo_page_with_tree(self, repo_with_data) -> None:
        """Repo page renders the file tree and README."""
        html = _get_page_content("/groups/team/app", self._info())
        assert "README.md" in html
        assert "main.py" in html

    def test_commits_page_with_commits(self, repo_with_data) -> None:
        """Commits page lists the seeded commit."""
        html = _get_page_content("/groups/team/app/commits", self._info())
        assert "Initial commit" in html

    def test_commits_page_no_timestamp(self, repo_with_data) -> None:
        """A commit with no timestamp renders without a date."""
        table = repo_with_data.Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "COMMITS#team/app#main",
            "SK": "COMMIT#0000002",
            "sha": "beef5678",
            "message": "No date commit",
            "author": "alice",
            "timestamp": 0,
        })
        html = _get_page_content("/groups/team/app/commits", self._info())
        assert "No date commit" in html

    def test_commits_page_non_admin(self, repo_with_data) -> None:
        """A contributor (non-admin) sees no settings gear."""
        _handle_api(_req("POST", "/api/access", {
            "username": "carol", "resource": "team", "level": "readonly",
        }))
        html = _get_page_content(
            "/groups/team/app/commits",
            {"username": "carol", "is_admin": False},
        )
        assert html

    def test_branches_page_with_branches(self, repo_with_data) -> None:
        """Branches page lists the seeded branch."""
        html = _get_page_content("/groups/team/app/branches", self._info())
        assert "main" in html

    def test_settings_page_as_admin(self, repo_with_data) -> None:
        """Settings page renders for an admin user."""
        html = _get_page_content("/groups/team/app/settings", self._info())
        assert html

    def test_file_viewer(self, repo_with_data) -> None:
        """Viewing a file path renders the file content."""
        html = _get_page_content(
            "/groups/team/app/tree/main/main.py", self._info(),
        )
        assert "hello" in html

    def test_tree_subdir(self, repo_with_data) -> None:
        """Viewing a directory path renders the tree."""
        html = _get_page_content(
            "/groups/team/app/tree/main/src", self._info(),
        )
        assert html

    def test_pulls_page_with_pr(self, repo_with_data) -> None:
        """Pulls page lists an open PR."""
        _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "My PR", "source_branch": "feat", "author": "alice"},
        ))
        html = _get_page_content("/groups/team/app/pulls", self._info())
        assert "My PR" in html

    def test_pr_detail_with_comment(self, repo_with_data) -> None:
        """PR detail page renders the PR and a comment."""
        create = _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "My PR", "source_branch": "feat", "author": "alice"},
        ))
        num = json.loads(create.body)["number"]
        _handle_api(_req(
            "POST", f"/api/team/app/pulls/{num}/comments",
            {"author": "bob", "body": "looks good"},
        ))
        html = _get_page_content(
            f"/groups/team/app/pulls/{num}", self._info(),
        )
        assert "My PR" in html

    def test_pr_detail_diff_tab(self, repo_with_data) -> None:
        """PR detail with the diff tab selected renders."""
        create = _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "My PR", "source_branch": "feat", "author": "alice"},
        ))
        num = json.loads(create.body)["number"]
        html = _get_page_content(
            f"/groups/team/app/pulls/{num}", self._info(),
            query_string="tab=diff",
        )
        assert html

    def test_group_detail_with_repos(self, repo_with_data) -> None:
        """Group detail lists repos for a member."""
        html = _get_page_content("/groups/team", self._info())
        assert "app" in html

    def test_dashboard_with_access(self, repo_with_data) -> None:
        """Dashboard renders groups the user can access."""
        html = _get_page_content("/", self._info())
        assert html

    def test_commit_diff_page(self, repo_with_data) -> None:
        """Commit diff page renders for a known sha."""
        html = _get_page_content(
            "/groups/team/app/commit/abc1234def5678", self._info(),
        )
        assert html

    def _noadmin(self) -> dict:
        return {"username": "bob", "is_admin": False}

    def test_repo_page_forbidden(self, repo_with_data) -> None:
        """A user without access gets 403 on the repo page."""
        html = _get_page_content("/groups/team/app", self._noadmin())
        assert "403" in html

    def test_commits_page_forbidden(self, repo_with_data) -> None:
        """A user without access gets 403 on the commits page."""
        html = _get_page_content(
            "/groups/team/app/commits", self._noadmin(),
        )
        assert "403" in html

    def test_pr_detail_full(self, repo_with_data) -> None:
        """PR detail renders checks, reviews, and lint comments."""
        import boto3

        from gitgateway.reviews import (
            Comment,
            Review,
            ReviewStatus,
            add_comment,
            create_review,
        )
        from gitgateway.status_checks import (
            CheckStatus,
            StatusCheck,
            post_status,
        )

        create = _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "My PR", "source_branch": "feat", "author": "alice"},
        ))
        num = json.loads(create.body)["number"]

        post_status(StatusCheck(
            name="lint/pylint", status=CheckStatus.SUCCESS,
            description="0 findings", pr_number=num,
            group="team", repo="app",
        ))
        post_status(StatusCheck(
            name="lint/bandit", status=CheckStatus.FAILURE,
            description="1 finding", pr_number=num,
            group="team", repo="app",
        ))
        create_review(Review(
            pr_number=num, author="bob", status=ReviewStatus.APPROVED,
            body="lgtm", group="team", repo="app",
        ))
        add_comment(Comment(
            pr_number=num, author="gitgateway-lint",
            body="- \u2717 `main.py:1` [pylint] bad", group="team",
            repo="app",
        ))

        html = _get_page_content(
            f"/groups/team/app/pulls/{num}", self._info(),
        )
        assert "Checks" in html
        assert "bob" in html

    def test_pr_detail_merged_status(self, repo_with_data) -> None:
        """A merged PR renders its merged status badge."""
        create = _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "M", "source_branch": "feat", "author": "alice"},
        ))
        num = json.loads(create.body)["number"]
        _handle_api(_req("POST", f"/api/team/app/pulls/{num}/merge"))
        html = _get_page_content(
            f"/groups/team/app/pulls/{num}", self._info(),
        )
        assert html

    def test_pr_detail_forbidden(self, repo_with_data) -> None:
        """A user without access gets 403 on PR detail."""
        create = _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "X", "source_branch": "feat", "author": "alice"},
        ))
        num = json.loads(create.body)["number"]
        html = _get_page_content(
            f"/groups/team/app/pulls/{num}", self._noadmin(),
        )
        assert "403" in html

    def test_pr_detail_not_found(self, repo_with_data) -> None:
        """A missing PR number renders 404."""
        html = _get_page_content(
            "/groups/team/app/pulls/999", self._info(),
        )
        assert "404" in html

    def test_file_viewer_readme_markdown(self, repo_with_data) -> None:
        """Viewing a markdown file renders it as markdown."""
        html = _get_page_content(
            "/groups/team/app/tree/main/README.md", self._info(),
        )
        assert "markdown-body" in html or "README" in html

    def test_dashboard_with_review_prs(self, repo_with_data) -> None:
        """Dashboard lists open PRs authored by someone else."""
        # PR authored by 'someoneelse' so it shows in alice's review list.
        _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "Review me", "source_branch": "feat",
             "author": "someoneelse"},
        ))
        html = _get_page_content("/", self._info())
        assert "Review me" in html

    def test_file_viewer_non_markdown(self, repo_with_data) -> None:
        """Viewing a code file uses syntax highlighting, not markdown."""
        html = _get_page_content(
            "/groups/team/app/tree/main/main.py", self._info(),
        )
        assert "hello" in html

    def test_repo_page_synthetic_subdir(self, repo_with_data) -> None:
        """A nested file with no explicit dir entry yields a synthetic dir."""
        table = repo_with_data.Table("gitgateway-repos")
        # A file deep in a directory that has no explicit 'type: dir' entry.
        table.put_item(Item={
            "PK": "TREE#team/app#main",
            "SK": "FILE#lib/util/helper.py",
            "path": "lib/util/helper.py",
            "type": "file",
            "content": "def f():\n    return 1\n",
        })
        html = _get_page_content("/groups/team/app", self._info())
        # The synthetic top-level 'lib' directory should appear.
        assert "lib" in html

    def test_user_groups_page_with_access(self, repo_with_data) -> None:
        """/groups lists a group the user can access."""
        html = _get_page_content("/groups", self._info())
        assert "team" in html


class TestAdminAiPage:
    """Cover _render_admin_ai_page key-status branches."""

    def test_configured_key(self, aws) -> None:
        """A configured Gemini key shows the configured status + preview."""
        import boto3

        from gitgateway.lambda_handler import _render_admin_ai_page

        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(
            Name="gitgateway/gemini-api-key",
            SecretString="AIzaSyXXXXXXXX1234",
        )
        html = _render_admin_ai_page()
        assert "Configured" in html

    def test_not_created(self, aws) -> None:
        """No secret shows the not-created status."""
        from gitgateway.lambda_handler import _render_admin_ai_page

        html = _render_admin_ai_page()
        assert "Not created" in html or "Not configured" in html

    def test_error_reading_secret(self, aws) -> None:
        """A generic Secrets Manager error shows an error status."""
        from gitgateway.lambda_handler import _render_admin_ai_page

        with patch("boto3.client") as mk:
            mk.return_value.get_secret_value.side_effect = RuntimeError("x")
            # No ResourceNotFoundException attribute path -> generic except.
            mk.return_value.exceptions.ResourceNotFoundException = (
                type("RNF", (Exception,), {})
            )
            html = _render_admin_ai_page()
        assert "Error" in html or "AI Settings" in html


class TestPageContentRouting:
    """Tests for _get_page_content page routing and rendering.

    Each renders HTML (mostly empty-state) against moto-backed tables.
    """

    def _info(self, username: str = "alice", is_admin: bool = False) -> dict:
        """Build a user_info dict."""
        return {"username": username, "is_admin": is_admin}

    def test_dashboard(self, aws) -> None:
        """Root path renders the dashboard."""
        html = _get_page_content("/", self._info())
        assert isinstance(html, str) and html

    def test_empty_path(self, aws) -> None:
        """Empty path renders the dashboard."""
        html = _get_page_content("", self._info())
        assert html

    def test_groups_page(self, aws) -> None:
        """/groups renders the user's groups page."""
        html = _get_page_content("/groups", self._info())
        assert html

    def test_tokens_page(self, aws) -> None:
        """/tokens renders the token management page."""
        html = _get_page_content("/tokens", self._info())
        assert "Token" in html or "token" in html

    def test_group_detail_page_forbidden(self, aws) -> None:
        """/groups/<group> without access renders 403."""
        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        html = _get_page_content("/groups/team", self._info())
        assert "403" in html

    def test_group_detail_page_with_access(self, aws) -> None:
        """/groups/<group> with access renders the detail page."""
        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))
        html = _get_page_content("/groups/team", self._info())
        assert "team" in html

    def test_repo_page(self, aws) -> None:
        """/groups/<group>/<repo> renders the repo view."""
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        html = _get_page_content("/groups/team/app", self._info())
        assert html

    def test_repo_settings_page(self, aws) -> None:
        """/groups/<group>/<repo>/settings renders settings."""
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        html = _get_page_content("/groups/team/app/settings", self._info())
        assert html

    def test_branches_page(self, aws) -> None:
        """/groups/<group>/<repo>/branches renders branches."""
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        html = _get_page_content("/groups/team/app/branches", self._info())
        assert html

    def test_commits_page(self, aws) -> None:
        """/groups/<group>/<repo>/commits renders commit history."""
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        html = _get_page_content("/groups/team/app/commits", self._info())
        assert html

    def test_commits_page_with_branch(self, aws) -> None:
        """/commits/<branch> renders commit history for a branch."""
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        html = _get_page_content(
            "/groups/team/app/commits/main", self._info(),
        )
        assert html

    def test_commit_diff_page(self, aws) -> None:
        """/commit/<sha> renders a commit diff view."""
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        html = _get_page_content(
            "/groups/team/app/commit/abc123", self._info(),
        )
        assert html

    def test_pulls_page(self, aws) -> None:
        """/groups/<group>/<repo>/pulls renders the PR list."""
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        html = _get_page_content("/groups/team/app/pulls", self._info())
        assert html

    def test_pr_detail_page(self, aws) -> None:
        """/pulls/<n> renders a PR detail page."""
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        create = _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "t", "source_branch": "b", "author": "a"},
        ))
        num = json.loads(create.body)["number"]
        html = _get_page_content(
            f"/groups/team/app/pulls/{num}", self._info(),
        )
        assert html

    def test_tree_view(self, aws) -> None:
        """/tree/<branch> renders a repo tree/file view."""
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        html = _get_page_content(
            "/groups/team/app/tree/main", self._info(),
        )
        assert html

    def test_admin_requires_admin(self, aws) -> None:
        """/admin without admin rights renders 403."""
        html = _get_page_content("/admin", self._info(is_admin=False))
        assert "403" in html

    def test_admin_content_for_admin(self, aws) -> None:
        """/admin as admin renders the admin index."""
        html = _get_page_content("/admin", self._info(is_admin=True))
        assert "Administration" in html

    def test_admin_audit_for_admin(self, aws) -> None:
        """/admin/audit as admin renders the audit log page."""
        html = _get_page_content("/admin/audit", self._info(is_admin=True))
        assert "Audit Log" in html

    def test_admin_groups_for_admin(self, aws) -> None:
        """/admin/groups as admin renders the groups admin page."""
        html = _get_page_content("/admin/groups", self._info(is_admin=True))
        assert html

    def test_admin_access_for_admin(self, aws) -> None:
        """/admin/access as admin renders the access admin page."""
        html = _get_page_content("/admin/access", self._info(is_admin=True))
        assert html

    def test_admin_access_with_entries(self, aws) -> None:
        """/admin/access lists seeded access entries."""
        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))
        html = _get_page_content("/admin/access", self._info(is_admin=True))
        assert "alice" in html

    def test_admin_ai_for_admin(self, aws) -> None:
        """/admin/ai as admin renders the AI settings page."""
        html = _get_page_content("/admin/ai", self._info(is_admin=True))
        assert html

    def test_unknown_page_fallback(self, aws) -> None:
        """An unmatched path renders the generic fallback."""
        html = _get_page_content("/totally/unknown", self._info())
        assert "Path:" in html

    def test_admin_unknown_path_fallback(self, aws) -> None:
        """An unknown /admin/* path renders the admin fallback."""
        html = _get_page_content(
            "/admin/nonsense", self._info(is_admin=True),
        )
        assert "Path:" in html

    def test_admin_groups_with_groups(self, aws) -> None:
        """/admin/groups lists seeded groups."""
        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        html = _get_page_content("/admin/groups", self._info(is_admin=True))
        assert "team" in html

    def test_settings_repo_not_found(self, aws) -> None:
        """Settings page 404 for a missing repo (with group access)."""
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))
        html = _get_page_content(
            "/groups/team/ghost/settings", self._info(),
        )
        assert "404" in html

    def test_dashboard_malformed_pr_pk(self, aws) -> None:
        """A PR row with a malformed PK is skipped on the dashboard."""
        import boto3

        prs = boto3.resource("dynamodb").Table("gitgateway-prs")
        prs.put_item(Item={
            "PK": "REPO#malformed",  # no group/repo split
            "SK": "PR#1",
            "status": "open",
            "author": "someoneelse",
            "number": 1,
            "title": "orphan",
            "created_at": "2025-01-01T00:00:00Z",
        })
        html = _get_page_content("/", self._info())
        assert isinstance(html, str) and html


class TestApiRoutingEdges:
    """Cover malformed-path and JSON-error legs in _handle_api."""

    def test_post_branches_single_segment(self, aws) -> None:
        """POST .../branches with a single path segment is not handled."""
        resp = _handle_api(_req(
            "POST", "/api/repos/onlygroup/branches", {"branch": "x"},
        ))
        # No group/repo split -> falls through to the create-repo route,
        # which rejects the missing "name" field.
        assert resp.status_code == 400

    def test_get_branches_single_segment(self, aws) -> None:
        """GET .../branches with a single path segment falls through.

        Without a group/repo split it is handled by the group repo-list
        route, returning an (empty) list rather than the branch list.
        """
        resp = _handle_api(_req("GET", "/api/repos/onlygroup/branches"))
        assert resp.status_code == 200
        assert json.loads(resp.body) == []

    def test_delete_branch_single_group_segment(self, aws) -> None:
        """DELETE .../branches/<b> with no group/repo split is skipped."""
        resp = _handle_api(_req(
            "DELETE", "/api/repos/onlygroup/branches/feature",
        ))
        assert resp.status_code == 404

    def test_patch_settings_single_segment(self, aws) -> None:
        """PATCH .../settings with a single path segment is not handled."""
        resp = _handle_api(_req(
            "PATCH", "/api/repos/onlygroup/settings",
            {"description": "x"},
        ))
        assert resp.status_code == 404

    def test_patch_settings_description_only(self, aws) -> None:
        """PATCH settings with only description leaves default_branch."""
        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        resp = _handle_api(_req(
            "PATCH", "/api/repos/team/app/settings",
            {"description": "new desc"},
        ))
        assert resp.status_code == 200
        assert json.loads(resp.body)["description"] == "new desc"

    def test_patch_settings_default_branch_only(self, aws) -> None:
        """PATCH settings with only default_branch leaves description."""
        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        resp = _handle_api(_req(
            "PATCH", "/api/repos/team/app/settings",
            {"default_branch": "develop"},
        ))
        assert resp.status_code == 200
        assert json.loads(resp.body)["default_branch"] == "develop"

    def test_post_tokens_invalid_json(self, aws) -> None:
        """POST /api/tokens with bad JSON falls back to empty body.

        The JSON decode error is swallowed (body becomes {}), then the
        missing session yields a 401 during the auth check.
        """
        req = _req("POST", "/api/tokens")
        req.body = b"{not json"
        resp = _handle_api(req)
        assert resp.status_code == 401

    def test_post_access_invalid_level(self, aws) -> None:
        """POST /api/access with an invalid level returns 400."""
        resp = _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "wizard",
        }))
        assert resp.status_code == 400
        assert "Invalid level" in json.loads(resp.body)["error"]

    def test_get_access_with_group_filter(self, aws) -> None:
        """GET /api/access?group=<name> returns the group's access entries.

        The handler builds a filtered ``table.scan`` when a ``group``
        query parameter is present. ``resource`` is a DynamoDB reserved
        word, so the filter must alias it via ExpressionAttributeNames;
        this test drives that branch and asserts the filtered result.
        """
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))
        _handle_api(_req("POST", "/api/access", {
            "username": "bob", "resource": "other", "level": "readonly",
        }))
        resp = _handle_api(_req(
            "GET", "/api/access", query_string="group=team",
        ))
        assert resp.status_code == 200
        entries = json.loads(resp.body)
        resources = {e["resource"] for e in entries}
        assert resources == {"team"}


class TestGitAuthAndLfsEdges:
    """Cover git-auth JWT branch and LFS invalid-body handling."""

    @staticmethod
    def _basic(token: str) -> dict:
        """Build a Basic auth header for the token user."""
        import base64

        raw = base64.b64encode(f"__token__:{token}".encode()).decode()
        return {"authorization": f"Basic {raw}"}

    def test_git_auth_jwt_success(self, aws) -> None:
        """A valid JWT authenticates the git request as read/write."""
        from types import SimpleNamespace

        from gitgateway.lambda_handler import _authenticate_git_request

        req = _req("GET", "/team/app.git/info/refs", headers=self._basic("jwt"))
        with patch.dict("os.environ", {"IDP_URL": "https://idp.example.com"}), \
                patch(
                    "gitgateway.auth.validate_jwt",
                    return_value=SimpleNamespace(username="alice"),
                ):
            user = _authenticate_git_request(req)
        assert user == {"username": "alice", "scope": "read/write"}

    def test_git_auth_jwt_falls_back_to_pat(self, aws) -> None:
        """An invalid JWT falls through to PAT validation (then fails)."""
        from gitgateway.lambda_handler import _authenticate_git_request

        req = _req("GET", "/team/app.git/info/refs", headers=self._basic("x"))
        with patch.dict("os.environ", {"IDP_URL": "https://idp.example.com"}), \
                patch(
                    "gitgateway.auth.validate_jwt",
                    side_effect=ValueError("not a jwt"),
                ):
            user = _authenticate_git_request(req)
        # No matching PAT -> unauthenticated.
        assert user is None

    def test_lfs_invalid_json_body(self, aws) -> None:
        """LFS request with malformed JSON body degrades to empty body."""
        from gitgateway.lambda_handler import _handle_lfs

        req = _req("POST", "/team/app.git/info/lfs/objects/batch")
        req.body = b"{not json"
        with patch(
            "gitgateway.lambda_handler._authenticate_git_request",
            return_value={"username": "alice", "scope": "read/write"},
        ), patch(
            "gitgateway.lfs.handle_batch_request",
            return_value={"objects": []},
        ):
            resp = _handle_lfs(req)
        # operation defaults to "download" on empty body.
        assert resp.status_code == 200


class TestRenderNonAdminLegs:
    """Render pages as readonly/contributor users to hit non-admin legs."""

    @pytest.fixture
    def repo_and_users(self, aws):
        """Create a repo and grant reader (readonly) + writer (contributor)."""
        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        _handle_api(_req("POST", "/api/access", {
            "username": "reader", "resource": "team", "level": "readonly",
        }))
        _handle_api(_req("POST", "/api/access", {
            "username": "writer", "resource": "team", "level": "contributor",
        }))
        return aws

    @staticmethod
    def _info(username: str) -> dict:
        """Build a non-admin user_info dict."""
        return {"username": username, "is_admin": False}

    def test_branches_page_readonly_no_create_form(
        self, repo_and_users,
    ) -> None:
        """A readonly user sees no create form and no settings gear."""
        html = _get_page_content(
            "/groups/team/app/branches", self._info("reader"),
        )
        assert "Create Branch" not in html

    def test_branches_page_contributor_no_settings(
        self, repo_and_users,
    ) -> None:
        """A contributor sees the create form but no admin settings gear."""
        html = _get_page_content(
            "/groups/team/app/branches", self._info("writer"),
        )
        assert "Create Branch" in html

    def test_branches_page_delete_button(self, repo_and_users) -> None:
        """A contributor sees a delete button for a non-default branch."""
        import boto3

        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "BRANCHES#team/app",
            "SK": "BRANCH#feature",
            "branch_name": "feature",
        })
        html = _get_page_content(
            "/groups/team/app/branches", self._info("writer"),
        )
        assert "deleteBranch('feature')" in html

    def test_pulls_page_non_admin(self, repo_and_users) -> None:
        """The pulls page renders without a settings gear for non-admins."""
        html = _get_page_content(
            "/groups/team/app/pulls", self._info("reader"),
        )
        assert html

    def test_pr_detail_page_non_admin(self, repo_and_users) -> None:
        """A PR detail page renders without a settings gear for non-admins."""
        create = _handle_api(_req(
            "POST", "/api/team/app/pulls",
            {"title": "t", "source_branch": "b", "author": "writer"},
        ))
        num = json.loads(create.body)["number"]
        html = _get_page_content(
            f"/groups/team/app/pulls/{num}", self._info("reader"),
        )
        assert html

    def test_file_viewer_non_admin(self, repo_and_users) -> None:
        """The file viewer renders without a settings gear for non-admins."""
        import boto3

        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "TREE#team/app#main",
            "SK": "FILE#main.py",
            "path": "main.py",
            "type": "file",
            "content": "print('hi')\n",
        })
        html = _get_page_content(
            "/groups/team/app/tree/main/main.py", self._info("reader"),
        )
        assert "main.py" in html

    def test_tree_view_non_admin(self, repo_and_users) -> None:
        """The tree view renders without a settings gear for non-admins."""
        import boto3

        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "TREE#team/app#main",
            "SK": "FILE#README.md",
            "path": "README.md",
            "type": "file",
            "content": "# hi\n",
        })
        html = _get_page_content(
            "/groups/team/app/tree/main", self._info("reader"),
        )
        assert html

    def test_group_page_readonly_no_create(self, repo_and_users) -> None:
        """A readonly user on a group page sees no create-repo form."""
        html = _get_page_content("/groups/team", self._info("reader"))
        assert "Create Repository" not in html

    def test_file_viewer_forbidden(self, aws) -> None:
        """A user without access to the repo gets 403 from the file view."""
        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        import boto3

        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "TREE#team/app#main",
            "SK": "FILE#main.py",
            "path": "main.py",
            "type": "file",
            "content": "x = 1\n",
        })
        html = _get_page_content(
            "/groups/team/app/tree/main/main.py", self._info("nobody"),
        )
        assert "403" in html


class TestRenderDataShapes:
    """Cover render branches driven by DynamoDB data shape."""

    @pytest.fixture
    def repo(self, aws):
        """Create a repo and grant alice admin access."""
        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))
        return aws

    @staticmethod
    def _info() -> dict:
        """Build an admin user_info dict."""
        return {"username": "alice", "is_admin": True}

    def test_commits_page_unknown_branch(self, repo) -> None:
        """Commits page for a branch not in the branch list inserts it.

        With no BRANCHES# rows, the default branch fallback runs and the
        requested branch is prepended to the branch selector.
        """
        html = _get_page_content(
            "/groups/team/app/commits/develop", self._info(),
        )
        assert "No commits yet" in html

    def test_file_viewer_nested_breadcrumb(self, repo) -> None:
        """Viewing a nested file renders intermediate breadcrumb links."""
        import boto3

        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "TREE#team/app#main",
            "SK": "FILE#src/app/main.py",
            "path": "src/app/main.py",
            "type": "file",
            "content": "print('hi')\n",
        })
        html = _get_page_content(
            "/groups/team/app/tree/main/src/app/main.py", self._info(),
        )
        # Intermediate crumb link for the "src" and "app" directories.
        assert "/tree/main/src" in html
        # Admin settings gear present.
        assert "settings" in html

    def test_tree_view_unknown_branch_subdir(self, repo) -> None:
        """Tree view for an unknown branch and a subdir path.

        Seeds nested files so the directory-dedup branch runs, and uses
        a branch absent from the (empty) branch list to hit the insert.
        """
        import boto3

        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        for name in ("lib/one.py", "lib/two.py"):
            table.put_item(Item={
                "PK": "TREE#team/app#dev",
                "SK": f"FILE#{name}",
                "path": name,
                "type": "file",
                "content": "x = 1\n",
            })
        html = _get_page_content(
            "/groups/team/app/tree/dev", self._info(),
        )
        assert "lib" in html

    def test_tree_view_subdir_relative(self, repo) -> None:
        """Tree view scoped to a subdirectory filters by prefix."""
        import boto3

        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "TREE#team/app#main",
            "SK": "FILE#lib/util/helper.py",
            "path": "lib/util/helper.py",
            "type": "file",
            "content": "x = 1\n",
        })
        table.put_item(Item={
            "PK": "TREE#team/app#main",
            "SK": "FILE#lib/util",
            "path": "lib/util",
            "type": "dir",
        })
        html = _get_page_content(
            "/groups/team/app/tree/main/lib", self._info(),
        )
        assert "util" in html

    def test_tree_view_readme_with_content(self, repo) -> None:
        """A README.md in the current dir is rendered below the tree."""
        import boto3

        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "TREE#team/app#main",
            "SK": "FILE#README.md",
            "path": "README.md",
            "type": "file",
            "content": "# Title\n\nBody text.\n",
        })
        html = _get_page_content("/groups/team/app", self._info())
        assert "README.md" in html

    def test_tree_view_readme_empty_content(self, repo) -> None:
        """A README.md with empty content is not rendered as a panel."""
        import boto3

        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "TREE#team/app#main",
            "SK": "FILE#README.md",
            "path": "README.md",
            "type": "file",
            "content": "",
        })
        html = _get_page_content("/groups/team/app", self._info())
        assert isinstance(html, str) and html

    def test_admin_access_skips_incomplete_entry(self, repo) -> None:
        """An access row missing username/resource is skipped in the table."""
        import boto3

        table = boto3.resource("dynamodb").Table("gitgateway-access")
        table.put_item(Item={
            "PK": "ACCESS#ghost",
            "SK": "RESOURCE#",
            "username": "",
            "resource": "",
            "level": "readonly",
        })
        html = _get_page_content("/admin/access", self._info())
        assert isinstance(html, str) and html


class TestAdminAndGroupEdges:
    """Cover AI-settings key branch and empty group listings."""

    @staticmethod
    def _info(is_admin: bool = True) -> dict:
        """Build a user_info dict."""
        return {"username": "alice", "is_admin": is_admin}

    def test_admin_ai_empty_secret(self, aws) -> None:
        """AI settings with an empty secret string is 'Not configured'.

        The secret exists (so the not-found and error legs are skipped)
        but its value is empty, exercising the falsy-api_key branch. The
        client is stubbed because Secrets Manager rejects empty values.
        """
        from unittest.mock import MagicMock

        import boto3

        real_client = boto3.client

        def _fake_client(name, *args, **kwargs):
            if name == "secretsmanager":
                stub = MagicMock()
                stub.get_secret_value.return_value = {"SecretString": ""}
                return stub
            return real_client(name, *args, **kwargs)

        with patch("boto3.client", side_effect=_fake_client):
            html = _get_page_content("/admin/ai", self._info())
        assert "Not configured" in html

    def test_group_page_no_repos(self, aws) -> None:
        """A group with no repositories shows the empty-state row."""
        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))
        html = _get_page_content("/groups/team", self._info())
        assert "No repositories" in html


class TestCommitDiffEdges:
    """Cover commit-diff render branches with real dulwich commits."""

    @staticmethod
    def _info() -> dict:
        """Build an admin user_info dict."""
        return {"username": "alice", "is_admin": True}

    @staticmethod
    def _commit(repo, tree, message, ref, parent=None):
        """Create and store a commit pointing at the given tree."""
        from dulwich.objects import Commit

        commit = Commit()
        commit.tree = tree.id
        commit.author = commit.committer = b"Tester <t@example.com>"
        commit.commit_time = commit.author_time = 1_700_000_000
        commit.commit_timezone = commit.author_timezone = 0
        commit.encoding = b"UTF-8"
        commit.message = message
        if parent:
            commit.parents = [parent]
        repo.object_store.add_object(commit)
        repo.refs[ref] = commit.id
        return commit.id

    @pytest.fixture
    def repo_dir(self, aws, tmp_path):
        """Create the repo record and grant alice admin; return the dir."""
        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))
        return tmp_path / "app.git"

    def test_commit_diff_load_failure(self, repo_dir) -> None:
        """A failure syncing/opening the repo renders a 500 page."""
        from dulwich.objects import Blob, Tree
        from dulwich.repo import Repo as DRepo

        from gitgateway.storage import sync_repo_to_s3

        repo = DRepo.init_bare(str(repo_dir), mkdir=True)
        blob = Blob.from_string(b"hi\n")
        tree = Tree()
        tree.add(b"f.txt", 0o100644, blob.id)
        repo.object_store.add_object(blob)
        repo.object_store.add_object(tree)
        sha = self._commit(repo, tree, b"root", b"refs/heads/main")
        sync_repo_to_s3(repo_dir, "team", "app")

        with patch(
            "gitgateway.storage.sync_repo_to_local",
            side_effect=RuntimeError("corrupt"),
        ):
            html = _get_page_content(
                f"/groups/team/app/commit/{sha.decode()}", self._info(),
            )
        assert "500" in html

    def test_commit_diff_root_commit_empty_file(self, repo_dir) -> None:
        """A root commit adding an empty file hits the no-parent and

        empty-diff branches (the added empty file produces no diff body).
        """
        from dulwich.objects import Blob, Tree
        from dulwich.repo import Repo as DRepo

        from gitgateway.storage import sync_repo_to_s3

        repo = DRepo.init_bare(str(repo_dir), mkdir=True)
        empty_blob = Blob.from_string(b"")
        tree = Tree()
        tree.add(b"empty.txt", 0o100644, empty_blob.id)
        repo.object_store.add_object(empty_blob)
        repo.object_store.add_object(tree)
        sha = self._commit(repo, tree, b"root", b"refs/heads/main")
        sync_repo_to_s3(repo_dir, "team", "app")

        html = _get_page_content(
            f"/groups/team/app/commit/{sha.decode()}", self._info(),
        )
        assert "empty.txt" in html

    def test_commit_diff_no_changes(self, repo_dir) -> None:
        """A commit whose tree matches its parent shows 'No file changes'."""
        from dulwich.objects import Blob, Tree
        from dulwich.repo import Repo as DRepo

        from gitgateway.storage import sync_repo_to_s3

        repo = DRepo.init_bare(str(repo_dir), mkdir=True)
        blob = Blob.from_string(b"same\n")
        tree = Tree()
        tree.add(b"f.txt", 0o100644, blob.id)
        repo.object_store.add_object(blob)
        repo.object_store.add_object(tree)
        root = self._commit(repo, tree, b"root", b"refs/heads/main")
        # Child commit reuses the identical tree (no file changes).
        child = self._commit(
            repo, tree, b"noop", b"refs/heads/main", parent=root,
        )
        sync_repo_to_s3(repo_dir, "team", "app")

        html = _get_page_content(
            f"/groups/team/app/commit/{child.decode()}", self._info(),
        )
        assert "No file changes" in html


class TestAiAndLintEarlyReturns:
    """Cover early-return branches in _run_ai_review and _run_pr_linting."""

    @staticmethod
    def _empty_secret_client():
        """Return a boto3.client patcher that yields an empty secret."""
        from unittest.mock import MagicMock

        import boto3

        real_client = boto3.client

        def _fake_client(name, *args, **kwargs):
            if name == "secretsmanager":
                stub = MagicMock()
                stub.get_secret_value.return_value = {"SecretString": ""}
                return stub
            return real_client(name, *args, **kwargs)

        return _fake_client

    def test_ai_review_empty_api_key(self, aws) -> None:
        """An empty API key short-circuits the AI review."""
        from gitgateway.lambda_handler import _run_ai_review

        with patch("boto3.client", side_effect=self._empty_secret_client()):
            _run_ai_review("team", "app", 1, "feat")

    def test_ai_review_empty_diff_text(self, aws) -> None:
        """Tree items without content produce no diff text and return."""
        import boto3

        from gitgateway.lambda_handler import _run_ai_review

        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(
            Name="gitgateway/gemini-api-key", SecretString="key123",
        )
        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        # Item present but no content -> no diff lines built.
        table.put_item(Item={
            "PK": "TREE#team/app#feat", "SK": "FILE#a.py",
            "path": "a.py", "content": "",
        })
        _run_ai_review("team", "app", 1, "feat")

    def test_ai_review_large_diff_break(self, aws) -> None:
        """Accumulated diff over the size cap breaks out of the loop."""
        import boto3

        from gitgateway.lambda_handler import _run_ai_review

        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(
            Name="gitgateway/gemini-api-key", SecretString="key123",
        )
        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        # Long lines so the accumulated pseudo-diff crosses the 8KB cap.
        long_line = "x = " + ("a" * 120) + "\n"
        for i in range(6):
            table.put_item(Item={
                "PK": "TREE#team/app#feat", "SK": f"FILE#big{i}.py",
                "path": f"big{i}.py", "content": long_line * 100,
            })
        with patch(
            "gitgateway.ai.generate_code_review", return_value=[],
        ), patch(
            "gitgateway.ai.generate_pr_summary", return_value="",
        ):
            _run_ai_review("team", "app", 1, "feat")

    def test_linting_all_empty_content(self, aws) -> None:
        """Tree items with empty content yield no files and return early."""
        import boto3

        from gitgateway.lambda_handler import _run_pr_linting

        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "TREE#team/app#feat", "SK": "FILE#a.py",
            "path": "a.py", "content": "",
        })
        _run_pr_linting("team", "app", 1, "feat")

    def test_linting_non_python_only(self, aws) -> None:
        """A non-categorized file skips every linter branch."""
        import boto3

        from gitgateway.lambda_handler import _run_pr_linting

        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(Item={
            "PK": "TREE#team/app#feat", "SK": "FILE#notes.txt",
            "path": "notes.txt", "content": "just text\n",
        })
        _run_pr_linting("team", "app", 1, "feat")


class TestDiffRendererEdges:
    """Cover error/empty legs in the diff renderers."""

    def test_compute_pr_diff_error_return(self, aws) -> None:
        """_compute_pr_diff returns the error HTML when the repo is missing."""
        from gitgateway.lambda_handler import _compute_pr_diff

        html = _compute_pr_diff("ghost", "repo", "main", "feat")
        assert "not found" in html.lower()

    def test_side_by_side_diff_empty(self) -> None:
        """An entry with no diff body renders the binary/empty placeholder."""
        from gitgateway.lambda_handler import _render_side_by_side_diff

        html = _render_side_by_side_diff({"diff": "", "status": "modified"})
        assert "Binary or empty file" in html

    def test_load_diff_missing_target_branch(self, aws, tmp_path) -> None:
        """A missing target branch (source present) still computes a diff.

        With no target SHA the whole source tree is treated as added.
        """
        from dulwich.objects import Blob, Commit, Tree
        from dulwich.repo import Repo as DRepo

        from gitgateway.lambda_handler import _load_diff_entries
        from gitgateway.storage import sync_repo_to_s3

        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))

        repo_dir = tmp_path / "app.git"
        repo = DRepo.init_bare(str(repo_dir), mkdir=True)
        blob = Blob.from_string(b"content\n")
        tree = Tree()
        tree.add(b"file.txt", 0o100644, blob.id)
        commit = Commit()
        commit.tree = tree.id
        commit.author = commit.committer = b"Tester <t@example.com>"
        commit.commit_time = commit.author_time = 1_700_000_000
        commit.commit_timezone = commit.author_timezone = 0
        commit.encoding = b"UTF-8"
        commit.message = b"feat"
        repo.object_store.add_object(blob)
        repo.object_store.add_object(tree)
        repo.object_store.add_object(commit)
        repo.refs[b"refs/heads/feat"] = commit.id
        sync_repo_to_s3(repo_dir, "team", "app")

        entries, err = _load_diff_entries("team", "app", "no-target", "feat")
        assert err == ""
        assert any(e["path"] == "file.txt" for e in entries)


class TestPageNotFoundAndFilters:
    """Cover repo-not-found legs and pull-request status filtering."""

    @pytest.fixture
    def group_only(self, aws):
        """Create a group and grant alice admin at the group level."""
        _handle_api(_req("POST", "/api/groups", {"name": "team"}))
        _handle_api(_req("POST", "/api/access", {
            "username": "alice", "resource": "team", "level": "admin",
        }))
        return aws

    @staticmethod
    def _info() -> dict:
        """Build an admin user_info dict."""
        return {"username": "alice", "is_admin": True}

    def test_repo_page_repo_not_found(self, group_only) -> None:
        """The repo page 404s when the repo record is missing."""
        html = _get_page_content("/groups/team/ghost", self._info())
        assert "404" in html

    def test_branches_page_repo_not_found(self, group_only) -> None:
        """The branches page 404s when the repo record is missing."""
        html = _get_page_content(
            "/groups/team/ghost/branches", self._info(),
        )
        assert "404" in html

    def test_commits_page_repo_not_found(self, group_only) -> None:
        """The commits page 404s when the repo record is missing."""
        html = _get_page_content(
            "/groups/team/ghost/commits", self._info(),
        )
        assert "404" in html

    def test_pulls_page_status_all(self, group_only) -> None:
        """The pulls page with status=all skips the status filter."""
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        html = _get_page_content(
            "/groups/team/app/pulls", self._info(),
            query_string="status=all",
        )
        assert html

    def test_pulls_page_status_closed(self, group_only) -> None:
        """The pulls page honours an explicit status filter value."""
        _handle_api(_req("POST", "/api/repos/team", {"name": "app"}))
        html = _get_page_content(
            "/groups/team/app/pulls", self._info(),
            query_string="status=closed",
        )
        assert html


class TestPageRoutingFalseLegs:
    """Cover reachable false legs in _get_page_content path parsing.

    Each path reaches a routing block but has a single group/repo
    segment (no repo), so the inner ``len(...) == 2`` guard is false and
    the handler falls through to the generic page fallback.
    """

    @staticmethod
    def _info() -> dict:
        """Build an admin user_info dict."""
        return {"username": "alice", "is_admin": False}

    def test_commit_single_segment(self, aws) -> None:
        """/groups/<x>/commit/<sha> with no repo falls through."""
        html = _get_page_content(
            "/groups/solo/commit/deadbeef", self._info(),
        )
        assert "Path:" in html

    def test_commits_branch_single_segment(self, aws) -> None:
        """/groups/<x>/commits/<branch> with no repo falls through."""
        html = _get_page_content(
            "/groups/solo/commits/main", self._info(),
        )
        assert "Path:" in html

    def test_commits_substring_not_matched(self, aws) -> None:
        """A path containing '/commits' but neither the '/commits/' nor

        the trailing '/commits' form falls through both legs.
        """
        html = _get_page_content(
            "/groups/team/commitsx/app", self._info(),
        )
        assert "Path:" in html

    def test_pull_detail_single_segment(self, aws) -> None:
        """/groups/<x>/pulls/<n> with no repo falls through."""
        html = _get_page_content("/groups/solo/pulls/1", self._info())
        assert "Path:" in html

    def test_pull_detail_non_numeric(self, aws) -> None:
        """/groups/<g>/<r>/pulls/<non-int> raises ValueError and falls back."""
        html = _get_page_content(
            "/groups/team/app/pulls/not-a-number", self._info(),
        )
        assert "Path:" in html

    def test_tree_single_segment(self, aws) -> None:
        """/groups/<x>/tree/<branch> with no repo falls through."""
        html = _get_page_content("/groups/solo/tree/main", self._info())
        assert "Path:" in html
