"""CLI entry point for GitGateway.

Provides subcommands for managing repos, groups, access, PRs,
webhooks, mirroring, and semantic search.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

LOG = logging.getLogger(__name__)


def main(args: list[str] | None = None) -> int:
    """Main CLI entry point.

    Args:
        args: Optional argument list (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 for success).
    """
    parser = _build_parser()
    parsed = parser.parse_args(args)

    if not hasattr(parsed, "func"):
        parser.print_help()
        return 1

    return parsed.func(parsed)


def _build_parser() -> argparse.ArgumentParser:  # pylint: disable=too-many-locals,too-many-statements
    """Build the argument parser with all subcommands.

    Returns:
        Configured ArgumentParser.
    """
    parser = argparse.ArgumentParser(
        prog="gitgateway",
        description="GitGateway - Serverless Git hosting platform",
    )
    parser.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="Increase verbosity",
    )

    subparsers = parser.add_subparsers(dest="command")

    # configure
    configure_parser = subparsers.add_parser(
        "configure", help="Configure the CLI",
    )
    configure_parser.add_argument("--api-url", help="API endpoint URL")
    configure_parser.add_argument("--idp-url", help="Identity provider URL")
    configure_parser.add_argument("--region", help="AWS region")
    configure_parser.add_argument("--domain-name", help="Custom domain")
    configure_parser.add_argument(
        "--from-stack", help="Import config from CloudFormation stack",
    )
    configure_parser.set_defaults(func=_handle_configure)

    # repo
    repo_parser = subparsers.add_parser("repo", help="Manage repositories")
    repo_sub = repo_parser.add_subparsers(dest="repo_command")

    repo_create = repo_sub.add_parser("create", help="Create a repository")
    repo_create.add_argument("name", help="Repository as group/name")
    repo_create.set_defaults(func=_handle_repo_create)

    repo_list = repo_sub.add_parser("list", help="List repositories")
    repo_list.add_argument("--group", help="Filter by group")
    repo_list.set_defaults(func=_handle_repo_list)

    repo_delete = repo_sub.add_parser("delete", help="Delete a repository")
    repo_delete.add_argument("name", help="Repository as group/name")
    repo_delete.set_defaults(func=_handle_repo_delete)

    repo_fork = repo_sub.add_parser("fork", help="Fork a repository")
    repo_fork.add_argument("name", help="Source repository as group/name")
    repo_fork.add_argument("--target", required=True, help="Target group")
    repo_fork.set_defaults(func=_handle_repo_fork)

    # group
    group_parser = subparsers.add_parser("group", help="Manage groups")
    group_sub = group_parser.add_subparsers(dest="group_command")

    group_create = group_sub.add_parser("create", help="Create a group")
    group_create.add_argument("name", help="Group name")
    group_create.set_defaults(func=_handle_group_create)

    group_list = group_sub.add_parser("list", help="List groups")
    group_list.set_defaults(func=_handle_group_list)

    # access
    access_parser = subparsers.add_parser("access", help="Manage access")
    access_sub = access_parser.add_subparsers(dest="access_command")

    access_set = access_sub.add_parser("set", help="Set access")
    access_set.add_argument("user", help="Username")
    access_set.add_argument("resource", help="Group or group/repo")
    access_set.add_argument(
        "level", choices=["admin", "contributor", "readonly"],
    )
    access_set.set_defaults(func=_handle_access_set)

    access_list = access_sub.add_parser("list", help="List user access")
    access_list.add_argument("user", help="Username")
    access_list.set_defaults(func=_handle_access_list)

    # pr
    pr_parser = subparsers.add_parser("pr", help="Manage pull requests")
    pr_sub = pr_parser.add_subparsers(dest="pr_command")

    pr_create = pr_sub.add_parser("create", help="Create a PR")
    pr_create.add_argument("repo", help="Repository as group/name")
    pr_create.add_argument("--source", required=True, help="Source branch")
    pr_create.add_argument("--target", default="main", help="Target branch")
    pr_create.add_argument("--title", required=True, help="PR title")
    pr_create.set_defaults(func=_handle_pr_create)

    pr_list = pr_sub.add_parser("list", help="List PRs")
    pr_list.add_argument("repo", help="Repository as group/name")
    pr_list.add_argument("--status", choices=["open", "merged", "closed"])
    pr_list.set_defaults(func=_handle_pr_list)

    pr_merge = pr_sub.add_parser("merge", help="Merge a PR")
    pr_merge.add_argument("repo", help="Repository as group/name")
    pr_merge.add_argument("number", type=int, help="PR number")
    pr_merge.add_argument(
        "--strategy", choices=["merge", "squash", "fast-forward"],
        default="merge",
    )
    pr_merge.set_defaults(func=_handle_pr_merge)

    # deploy
    deploy_parser = subparsers.add_parser(
        "deploy", help="Deploy GitGateway infrastructure",
    )
    deploy_parser.add_argument(
        "--domain-name", required=True, help="Custom domain",
    )
    deploy_parser.add_argument(
        "--acm-certificate-arn", required=True, help="ACM cert ARN",
    )
    deploy_parser.add_argument("--region", default="us-east-1")
    deploy_parser.add_argument("--profile", help="AWS profile")
    deploy_parser.set_defaults(func=_handle_deploy)

    return parser


# --- Handler stubs ---
# These will call the actual API client in production.
# For now they output JSON to stdout for testability.


def _handle_configure(args: argparse.Namespace) -> int:
    """Handle configure command."""
    config = {}
    if args.api_url:
        config["api_url"] = args.api_url
    if args.idp_url:
        config["idp_url"] = args.idp_url
    if args.region:
        config["region"] = args.region
    if args.domain_name:
        config["domain_name"] = args.domain_name
    if args.from_stack:
        config["from_stack"] = args.from_stack

    _output({"action": "configure", "config": config})
    return 0


def _handle_repo_create(args: argparse.Namespace) -> int:
    """Handle repo create command."""
    _output({"action": "repo.create", "name": args.name})
    return 0


def _handle_repo_list(args: argparse.Namespace) -> int:
    """Handle repo list command."""
    _output({"action": "repo.list", "group": args.group})
    return 0


def _handle_repo_delete(args: argparse.Namespace) -> int:
    """Handle repo delete command."""
    _output({"action": "repo.delete", "name": args.name})
    return 0


def _handle_repo_fork(args: argparse.Namespace) -> int:
    """Handle repo fork command."""
    _output({
        "action": "repo.fork",
        "name": args.name,
        "target": args.target,
    })
    return 0


def _handle_group_create(args: argparse.Namespace) -> int:
    """Handle group create command."""
    _output({"action": "group.create", "name": args.name})
    return 0


def _handle_group_list(args: argparse.Namespace) -> int:
    """Handle group list command."""  # pylint: disable=unused-argument
    _output({"action": "group.list"})
    return 0


def _handle_access_set(args: argparse.Namespace) -> int:
    """Handle access set command."""
    _output({
        "action": "access.set",
        "user": args.user,
        "resource": args.resource,
        "level": args.level,
    })
    return 0


def _handle_access_list(args: argparse.Namespace) -> int:
    """Handle access list command."""
    _output({"action": "access.list", "user": args.user})
    return 0


def _handle_pr_create(args: argparse.Namespace) -> int:
    """Handle PR create command."""
    _output({
        "action": "pr.create",
        "repo": args.repo,
        "source": args.source,
        "target": args.target,
        "title": args.title,
    })
    return 0


def _handle_pr_list(args: argparse.Namespace) -> int:
    """Handle PR list command."""
    _output({
        "action": "pr.list",
        "repo": args.repo,
        "status": args.status,
    })
    return 0


def _handle_pr_merge(args: argparse.Namespace) -> int:
    """Handle PR merge command."""
    _output({
        "action": "pr.merge",
        "repo": args.repo,
        "number": args.number,
        "strategy": args.strategy,
    })
    return 0


def _handle_deploy(args: argparse.Namespace) -> int:
    """Handle deploy command."""
    _output({
        "action": "deploy",
        "domain_name": args.domain_name,
        "acm_certificate_arn": args.acm_certificate_arn,
        "region": args.region,
        "profile": args.profile,
    })
    return 0


def _output(data: dict) -> None:
    """Write JSON output to stdout.

    Args:
        data: Dictionary to serialize.
    """
    sys.stdout.write(json.dumps(data) + "\n")
