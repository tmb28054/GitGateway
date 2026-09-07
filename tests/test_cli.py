"""Tests for gitgateway.cli module."""

from __future__ import annotations

import json
from unittest.mock import patch

from gitgateway.cli import main


class TestMainEntryPoint:
    """Tests for main() function."""

    def test_no_args_returns_1(self, capsys) -> None:
        """Returns 1 and prints help when no args."""
        result = main([])
        assert result == 1
        captured = capsys.readouterr()
        assert "gitgateway" in captured.out

    def test_verbose_flag(self) -> None:
        """Accepts -v flag."""
        with patch(
            "gitgateway.cli._api_request", return_value=[],
        ):
            result = main(["-v", "group", "list"])
        assert result == 0


class TestConfigure:
    """Tests for configure subcommand."""

    def test_configure_api_url(self, capsys) -> None:
        """Sets api_url config."""
        result = main(["configure", "--api-url", "https://api.example.com"])
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["config"]["api_url"] == "https://api.example.com"

    def test_configure_from_stack(self, capsys) -> None:
        """Imports from stack."""
        result = main(["configure", "--from-stack", "my-stack"])
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["config"]["from_stack"] == "my-stack"

    def test_configure_all_options(self, capsys) -> None:
        """Sets all config options."""
        result = main([
            "configure",
            "--api-url", "https://x.com",
            "--idp-url", "https://idp.x.com",
            "--region", "eu-west-1",
            "--domain-name", "git.x.com",
        ])
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["config"]["region"] == "eu-west-1"


class TestRepoCommands:
    """Tests for repo subcommands."""

    def test_repo_create(self, capsys) -> None:
        """Creates a repository via POST /api/repos/<group>."""
        with patch(
            "gitgateway.cli._api_request",
            return_value={"name": "app", "group": "team"},
        ) as mock_req:
            result = main(["repo", "create", "team/app"])
        assert result == 0
        mock_req.assert_called_once_with(
            "POST", "/api/repos/team", {"name": "app"},
        )
        output = json.loads(capsys.readouterr().out)
        assert output["name"] == "app"

    def test_repo_create_invalid_name(self) -> None:
        """Returns 1 when name is not group/repo."""
        result = main(["repo", "create", "noslash"])
        assert result == 1

    def test_repo_list(self, capsys) -> None:
        """Lists repositories for a group."""
        with patch(
            "gitgateway.cli._api_request",
            return_value=[{"name": "app"}],
        ) as mock_req:
            result = main(["repo", "list", "--group", "team"])
        assert result == 0
        mock_req.assert_called_once_with("GET", "/api/repos/team")
        output = json.loads(capsys.readouterr().out)
        assert output == [{"name": "app"}]

    def test_repo_list_all(self) -> None:
        """Lists all repositories when no group given."""
        with patch(
            "gitgateway.cli._api_request", return_value=[],
        ) as mock_req:
            result = main(["repo", "list"])
        assert result == 0
        mock_req.assert_called_once_with("GET", "/api/repos")

    def test_repo_delete(self) -> None:
        """Deletes a repository via DELETE."""
        with patch(
            "gitgateway.cli._api_request", return_value=None,
        ) as mock_req:
            result = main(["repo", "delete", "team/old"])
        assert result == 0
        mock_req.assert_called_once_with("DELETE", "/api/repos/team/old")

    def test_repo_fork(self, capsys) -> None:
        """Forks a repository to a target group."""
        with patch(
            "gitgateway.cli._api_request",
            return_value={"forked": True},
        ) as mock_req:
            result = main(["repo", "fork", "team/app", "--target", "alice"])
        assert result == 0
        mock_req.assert_called_once_with(
            "POST", "/api/repos/team/app/fork", {"target_group": "alice"},
        )
        output = json.loads(capsys.readouterr().out)
        assert output["forked"] is True


class TestGroupCommands:
    """Tests for group subcommands."""

    def test_group_create(self, capsys) -> None:
        """Creates a group via POST /api/groups."""
        with patch(
            "gitgateway.cli._api_request",
            return_value={"name": "newteam"},
        ) as mock_req:
            result = main(["group", "create", "newteam"])
        assert result == 0
        mock_req.assert_called_once_with(
            "POST", "/api/groups", {"name": "newteam"},
        )
        output = json.loads(capsys.readouterr().out)
        assert output["name"] == "newteam"

    def test_group_list(self, capsys) -> None:
        """Lists groups via GET /api/groups."""
        with patch(
            "gitgateway.cli._api_request",
            return_value=[{"name": "team"}],
        ) as mock_req:
            result = main(["group", "list"])
        assert result == 0
        mock_req.assert_called_once_with("GET", "/api/groups")
        output = json.loads(capsys.readouterr().out)
        assert output == [{"name": "team"}]


class TestAccessCommands:
    """Tests for access subcommands."""

    def test_access_set(self, capsys) -> None:
        """Sets access via POST /api/access."""
        with patch(
            "gitgateway.cli._api_request",
            return_value={"ok": True},
        ) as mock_req:
            result = main(
                ["access", "set", "alice", "team/app", "contributor"],
            )
        assert result == 0
        mock_req.assert_called_once_with(
            "POST", "/api/access",
            {"username": "alice", "resource": "team/app",
             "level": "contributor"},
        )

    def test_access_list(self, capsys) -> None:
        """Lists a user's access via GET /api/access."""
        with patch(
            "gitgateway.cli._api_request",
            return_value=[{"resource": "team/app", "level": "admin"}],
        ) as mock_req:
            result = main(["access", "list", "bob"])
        assert result == 0
        mock_req.assert_called_once_with("GET", "/api/access?user=bob")


class TestPrCommands:
    """Tests for PR subcommands."""

    def test_pr_create(self, capsys) -> None:
        """Creates a PR via POST /api/<group>/<repo>/pulls."""
        with patch(
            "gitgateway.cli._api_request",
            return_value={"number": 1},
        ) as mock_req:
            result = main([
                "pr", "create", "team/app",
                "--source", "feat/x", "--title", "Add feature",
            ])
        assert result == 0
        mock_req.assert_called_once_with(
            "POST", "/api/team/app/pulls",
            {"title": "Add feature", "source_branch": "feat/x",
             "target_branch": "main"},
        )
        output = json.loads(capsys.readouterr().out)
        assert output["number"] == 1

    def test_pr_list(self, capsys) -> None:
        """Lists PRs with a status filter."""
        with patch(
            "gitgateway.cli._api_request",
            return_value=[{"number": 1}],
        ) as mock_req:
            result = main(["pr", "list", "team/app", "--status", "open"])
        assert result == 0
        mock_req.assert_called_once_with(
            "GET", "/api/team/app/pulls?status=open",
        )

    def test_pr_merge(self, capsys) -> None:
        """Merges a PR via POST .../merge with a strategy."""
        with patch(
            "gitgateway.cli._api_request",
            return_value={"merged": True},
        ) as mock_req:
            result = main([
                "pr", "merge", "team/app", "5", "--strategy", "squash",
            ])
        assert result == 0
        mock_req.assert_called_once_with(
            "POST", "/api/team/app/pulls/5/merge", {"strategy": "squash"},
        )


class TestDeployCommand:
    """Tests for deploy subcommand."""

    def test_deploy(self, capsys) -> None:
        """Deploys infrastructure."""
        result = main([
            "deploy",
            "--domain-name", "git.example.com",
            "--acm-certificate-arn", "arn:aws:acm:us-east-1:123:cert/abc",
            "--region", "eu-west-1",
            "--profile", "prod",
        ])
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["action"] == "deploy"
        assert output["domain_name"] == "git.example.com"
        assert output["acm_certificate_arn"] == "arn:aws:acm:us-east-1:123:cert/abc"
        assert output["region"] == "eu-west-1"
        assert output["profile"] == "prod"


class TestApiUrl:
    """Tests for _api_url resolution."""

    def test_env_var_takes_precedence(self) -> None:
        """GITGATEWAY_API_URL wins and trailing slash is stripped."""
        from gitgateway.cli import _api_url

        with patch.dict("os.environ", {"GITGATEWAY_API_URL": "https://x/"}):
            assert _api_url() == "https://x"

    def test_falls_back_to_config(self) -> None:
        """Falls back to the config value when env var is unset."""
        from gitgateway.cli import _api_url

        with patch.dict("os.environ", {"GITGATEWAY_API_URL": ""}), patch(
            "gitgateway.config.get_config_value", return_value="https://c",
        ):
            assert _api_url() == "https://c"

    def test_empty_when_unset(self) -> None:
        """Returns empty string when nothing is configured."""
        from gitgateway.cli import _api_url

        with patch.dict("os.environ", {"GITGATEWAY_API_URL": ""}), patch(
            "gitgateway.config.get_config_value", return_value=None,
        ):
            assert _api_url() == ""


class TestApiRequest:
    """Tests for the _api_request HTTP helper."""

    def _resp(self, status: int, json_data=None, text: str = ""):
        """Build a fake requests.Response."""
        from unittest.mock import MagicMock

        resp = MagicMock()
        resp.status_code = status
        resp.text = text
        if json_data is None:
            resp.json.side_effect = ValueError("no json")
        else:
            resp.json.return_value = json_data
        return resp

    def test_success_returns_json(self) -> None:
        """A 200 with JSON returns the parsed body."""
        from gitgateway.cli import _api_request

        with patch.dict("os.environ", {"GITGATEWAY_API_URL": "https://x"}), \
                patch(
                    "requests.request",
                    return_value=self._resp(200, {"ok": True}),
                ):
            assert _api_request("GET", "/api/x") == {"ok": True}

    def test_posts_body(self) -> None:
        """A body is passed through as json kwarg."""
        from gitgateway.cli import _api_request

        with patch.dict("os.environ", {"GITGATEWAY_API_URL": "https://x"}), \
                patch(
                    "requests.request",
                    return_value=self._resp(201, {"id": 1}),
                ) as req:
            _api_request("POST", "/api/x", {"a": 1})
            assert req.call_args.kwargs["json"] == {"a": 1}

    def test_204_returns_none(self) -> None:
        """A 204 returns None."""
        from gitgateway.cli import _api_request

        with patch.dict("os.environ", {"GITGATEWAY_API_URL": "https://x"}), \
                patch("requests.request", return_value=self._resp(204)):
            assert _api_request("DELETE", "/api/x") is None

    def test_error_with_json_message(self, capsys) -> None:
        """A 4xx with a JSON error writes the message and returns None."""
        from gitgateway.cli import _api_request

        with patch.dict("os.environ", {"GITGATEWAY_API_URL": "https://x"}), \
                patch(
                    "requests.request",
                    return_value=self._resp(400, {"error": "bad"}),
                ):
            assert _api_request("GET", "/api/x") is None
        assert "bad" in capsys.readouterr().err

    def test_error_without_json(self, capsys) -> None:
        """A 4xx without JSON falls back to response text."""
        from gitgateway.cli import _api_request

        with patch.dict("os.environ", {"GITGATEWAY_API_URL": "https://x"}), \
                patch(
                    "requests.request",
                    return_value=self._resp(500, None, text="boom"),
                ):
            assert _api_request("GET", "/api/x") is None
        assert "boom" in capsys.readouterr().err

    def test_success_invalid_json_returns_none(self) -> None:
        """A 200 whose body is not JSON returns None."""
        from gitgateway.cli import _api_request

        with patch.dict("os.environ", {"GITGATEWAY_API_URL": "https://x"}), \
                patch(
                    "requests.request", return_value=self._resp(200, None),
                ):
            assert _api_request("GET", "/api/x") is None


class TestHandlerInvalidNames:
    """Invalid group/repo names return exit code 1."""

    def test_repo_create_invalid(self) -> None:
        """repo create with a bad name returns 1."""
        assert main(["repo", "create", "noslash"]) == 1

    def test_repo_delete_invalid(self) -> None:
        """repo delete with a bad name returns 1."""
        assert main(["repo", "delete", "noslash"]) == 1

    def test_repo_fork_invalid(self) -> None:
        """repo fork with a bad name returns 1."""
        assert main(["repo", "fork", "noslash", "--target", "t"]) == 1

    def test_pr_create_invalid(self) -> None:
        """pr create with a bad repo returns 1."""
        assert main(
            ["pr", "create", "noslash", "--source", "s", "--title", "t"],
        ) == 1

    def test_pr_list_invalid(self) -> None:
        """pr list with a bad repo returns 1."""
        assert main(["pr", "list", "noslash"]) == 1

    def test_pr_merge_invalid(self) -> None:
        """pr merge with a bad repo returns 1."""
        assert main(["pr", "merge", "noslash", "1"]) == 1


class TestHandlerApiFailure:
    """When the API returns nothing, create handlers return 1."""

    def test_repo_create_api_none(self) -> None:
        """repo create returns 1 when the API returns None."""
        with patch("gitgateway.cli._api_request", return_value=None):
            assert main(["repo", "create", "team/app"]) == 1

    def test_group_create_api_none(self) -> None:
        """group create returns 1 when the API returns None."""
        with patch("gitgateway.cli._api_request", return_value=None):
            assert main(["group", "create", "team"]) == 1

    def test_pr_create_api_none(self) -> None:
        """pr create returns 1 when the API returns None."""
        with patch("gitgateway.cli._api_request", return_value=None):
            assert main(
                ["pr", "create", "team/app", "--source", "s",
                 "--title", "t"],
            ) == 1

    def test_repo_fork_api_none(self) -> None:
        """repo fork returns 0 even when the API returns None."""
        with patch("gitgateway.cli._api_request", return_value=None):
            assert main(
                ["repo", "fork", "team/app", "--target", "t"],
            ) == 0

    def test_access_set_api_none(self) -> None:
        """access set returns 0 even when the API returns None."""
        with patch("gitgateway.cli._api_request", return_value=None):
            assert main(
                ["access", "set", "a", "team/app", "contributor"],
            ) == 0

    def test_pr_merge_api_none(self) -> None:
        """pr merge returns 0 even when the API returns None."""
        with patch("gitgateway.cli._api_request", return_value=None):
            assert main(["pr", "merge", "team/app", "1"]) == 0


class TestListHandlersApiNone:
    """List/get handlers return 0 and print nothing when API returns None."""

    def test_repo_list_none(self, capsys) -> None:
        """repo list with a None API result prints nothing, returns 0."""
        with patch("gitgateway.cli._api_request", return_value=None):
            assert main(["repo", "list"]) == 0
        assert capsys.readouterr().out == ""

    def test_group_list_none(self, capsys) -> None:
        """group list with a None API result prints nothing, returns 0."""
        with patch("gitgateway.cli._api_request", return_value=None):
            assert main(["group", "list"]) == 0
        assert capsys.readouterr().out == ""

    def test_access_list_none(self, capsys) -> None:
        """access list with a None API result prints nothing, returns 0."""
        with patch("gitgateway.cli._api_request", return_value=None):
            assert main(["access", "list", "bob"]) == 0
        assert capsys.readouterr().out == ""

    def test_pr_list_none(self, capsys) -> None:
        """pr list with a None API result prints nothing, returns 0."""
        with patch("gitgateway.cli._api_request", return_value=None):
            assert main(["pr", "list", "team/app"]) == 0
        assert capsys.readouterr().out == ""

    def test_pr_list_with_status_none(self, capsys) -> None:
        """pr list --status with a None result prints nothing, returns 0."""
        with patch("gitgateway.cli._api_request", return_value=None):
            assert main(["pr", "list", "team/app", "--status", "open"]) == 0
        assert capsys.readouterr().out == ""
