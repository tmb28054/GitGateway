"""Admin web UI view handlers for GitGateway.

Renders admin pages for group management, access control,
branch protection, mirroring, audit logs, and settings.
"""

from __future__ import annotations

import logging

from gitgateway.web.routes import render_template

LOG = logging.getLogger(__name__)


def render_admin_groups(
    groups: list[dict],
    is_admin: bool = True,
) -> str:
    """Render the admin group management page.

    Args:
        groups: List of group dicts with 'name', 'description', 'member_count'.
        is_admin: Whether the current user is an admin.

    Returns:
        Rendered HTML.
    """
    return render_template(
        "admin_groups.html",
        groups=groups,
        is_admin=is_admin,
    )


def render_admin_access(
    users: list[dict],
    resource: str = "",
) -> str:
    """Render the access management page.

    Args:
        users: List of user access dicts with 'username', 'level', 'resource'.
        resource: The resource being managed.

    Returns:
        Rendered HTML.
    """
    return render_template(
        "admin_access.html",
        users=users,
        resource=resource,
    )


def render_admin_audit(
    events: list[dict],
    resource: str = "",
) -> str:
    """Render the audit log viewer page.

    Args:
        events: List of audit event dicts.
        resource: Optional resource filter.

    Returns:
        Rendered HTML.
    """
    return render_template(
        "admin_audit.html",
        events=events,
        resource=resource,
    )


def render_repo_settings(
    repo: dict,
    mirror_config: dict | None = None,
    branch_protections: list[dict] | None = None,
) -> str:
    """Render the repository settings page.

    Args:
        repo: Repository metadata dict.
        mirror_config: Optional mirror configuration.
        branch_protections: Optional list of branch protections.

    Returns:
        Rendered HTML.
    """
    return render_template(
        "repo_settings.html",
        repo=repo,
        mirror_config=mirror_config,
        branch_protections=branch_protections or [],
    )
