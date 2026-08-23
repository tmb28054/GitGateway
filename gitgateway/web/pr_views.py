"""Pull Request web UI view handlers for GitGateway.

Renders PR list, detail, and diff pages.
"""

from __future__ import annotations

import logging

from gitgateway.web.routes import render_template

LOG = logging.getLogger(__name__)


def render_pr_list(
    repo: dict,
    pull_requests: list[dict],
    current_status: str = "open",
    counts: dict | None = None,
) -> str:
    """Render the PR list page.

    Args:
        repo: Repository metadata dict.
        pull_requests: List of PR dicts.
        current_status: Currently selected filter.
        counts: Dict with open/merged/closed counts.

    Returns:
        Rendered HTML.
    """
    if counts is None:
        counts = {"open": 0, "merged": 0, "closed": 0}

    return render_template(
        "pr_list.html",
        repo=repo,
        pull_requests=pull_requests,
        current_status=current_status,
        counts=counts,
    )


def render_pr_detail(  # pylint: disable=too-many-positional-arguments
    repo: dict,
    pr: dict,
    status_checks: list[dict] | None = None,
    reviews: list[dict] | None = None,
    can_merge: bool = False,
    checks_passing: bool = False,
) -> str:
    """Render the PR detail page.

    Args:
        repo: Repository metadata dict.
        pr: Pull request dict.
        status_checks: List of status check dicts.
        reviews: List of review dicts.
        can_merge: Whether current user can merge.
        checks_passing: Whether all required checks pass.

    Returns:
        Rendered HTML.
    """
    return render_template(
        "pr_detail.html",
        repo=repo,
        pr=pr,
        status_checks=status_checks or [],
        reviews=reviews or [],
        can_merge=can_merge,
        checks_passing=checks_passing,
    )
