"""Web UI route handlers for GitGateway.

Handles rendering of HTML pages using Jinja2 templates with HTMX
for dynamic interactions. Covers login, dashboard, repository
browsing, and file viewing.
"""

from __future__ import annotations

import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

LOG = logging.getLogger(__name__)

# Template directory - relative to this file
_TEMPLATE_DIR = Path(__file__).parent / "templates"

# Initialize Jinja2 environment
_env: Environment | None = None  # pylint: disable=invalid-name


def _get_env() -> Environment:
    """Get or create the Jinja2 environment.

    Returns:
        Configured Jinja2 Environment.
    """
    global _env  # pylint: disable=global-statement
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(["html"]),
        )
    return _env


def reset_env() -> None:
    """Reset the Jinja2 environment. Used in testing."""
    global _env  # pylint: disable=global-statement
    _env = None


def render_template(template_name: str, **context) -> str:
    """Render a Jinja2 template with context.

    Args:
        template_name: Name of the template file.
        **context: Template context variables.

    Returns:
        Rendered HTML string.
    """
    env = _get_env()
    template = env.get_template(template_name)
    return template.render(**context)


def render_login_page(
    idp_url: str = "",
    error: str = "",
) -> str:
    """Render the login page.

    Args:
        idp_url: URL to redirect for OAuth login.
        error: Optional error message to display.

    Returns:
        Rendered HTML.
    """
    return render_template(
        "login.html",
        idp_url=idp_url,
        error=error,
    )


def render_dashboard(
    username: str,
    groups: list[dict],
    notifications_count: int = 0,
) -> str:
    """Render the main dashboard page.

    Args:
        username: The authenticated user's name.
        groups: List of group dicts with 'name' and 'repos' keys.
        notifications_count: Unread notification count.

    Returns:
        Rendered HTML.
    """
    return render_template(
        "dashboard.html",
        username=username,
        groups=groups,
        notifications_count=notifications_count,
    )


def render_repo_page(
    repo: dict,
    files: list[dict],
    branches: list[str],
    current_branch: str = "main",
) -> str:
    """Render a repository detail page with file browser.

    Args:
        repo: Repository metadata dict.
        files: List of file dicts with 'name', 'type', 'path'.
        branches: Available branch names.
        current_branch: Currently selected branch.

    Returns:
        Rendered HTML.
    """
    return render_template(
        "repo.html",
        repo=repo,
        files=files,
        branches=branches,
        current_branch=current_branch,
    )


def render_error_page(
    status_code: int,
    message: str,
) -> str:
    """Render an error page.

    Args:
        status_code: HTTP status code.
        message: Error message.

    Returns:
        Rendered HTML.
    """
    return render_template(
        "error.html",
        status_code=status_code,
        message=message,
    )
