"""Tests for gitgateway.cli module."""

from __future__ import annotations

import json

from gitgateway.cli import main


class TestMainEntryPoint:
    """Tests for main() function."""

    def test_no_args_returns_1(self, capsys) -> None:
        """Returns 1 and prints help when no args."""
        result = main([])
        assert result == 1
        captured = capsys.readouterr()
        assert "gitgateway" in captured.out

    def test_verbose_flag(self, capsys) -> None:
        """Accepts -v flag."""
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
        """Creates a repository."""
        result = main(["repo", "create", "team/app"])
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["action"] == "repo.create"
        assert output["name"] == "team/app"

    def test_repo_list(self, capsys) -> None:
        """Lists repositories."""
        result = main(["repo", "list", "--group", "team"])
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["action"] == "repo.list"
        assert output["group"] == "team"

    def test_repo_list_all(self, capsys) -> None:
        """Lists all repositories."""
        result = main(["repo", "list"])
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["group"] is None

    def test_repo_delete(self, capsys) -> None:
        """Deletes a repository."""
        result = main(["repo", "delete", "team/old"])
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["action"] == "repo.delete"

    def test_repo_fork(self, capsys) -> None:
        """Forks a repository."""
        result = main(["repo", "fork", "team/app", "--target", "alice"])
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["action"] == "repo.fork"
        assert output["target"] == "alice"


class TestGroupCommands:
    """Tests for group subcommands."""

    def test_group_create(self, capsys) -> None:
        """Creates a group."""
        result = main(["group", "create", "newteam"])
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["action"] == "group.create"
        assert output["name"] == "newteam"

    def test_group_list(self, capsys) -> None:
        """Lists groups."""
        result = main(["group", "list"])
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["action"] == "group.list"


class TestAccessCommands:
    """Tests for access subcommands."""

    def test_access_set(self, capsys) -> None:
        """Sets access."""
        result = main(["access", "set", "alice", "team/app", "contributor"])
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["action"] == "access.set"
        assert output["user"] == "alice"
        assert output["level"] == "contributor"

    def test_access_list(self, capsys) -> None:
        """Lists user access."""
        result = main(["access", "list", "bob"])
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["action"] == "access.list"
        assert output["user"] == "bob"


class TestPrCommands:
    """Tests for PR subcommands."""

    def test_pr_create(self, capsys) -> None:
        """Creates a PR."""
        result = main([
            "pr", "create", "team/app",
            "--source", "feat/x", "--title", "Add feature",
        ])
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["action"] == "pr.create"
        assert output["source"] == "feat/x"
        assert output["title"] == "Add feature"
        assert output["target"] == "main"

    def test_pr_list(self, capsys) -> None:
        """Lists PRs."""
        result = main(["pr", "list", "team/app", "--status", "open"])
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["action"] == "pr.list"
        assert output["status"] == "open"

    def test_pr_merge(self, capsys) -> None:
        """Merges a PR."""
        result = main([
            "pr", "merge", "team/app", "5", "--strategy", "squash",
        ])
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["action"] == "pr.merge"
        assert output["number"] == 5
        assert output["strategy"] == "squash"


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
