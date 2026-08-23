"""Git remote helper for GitGateway large transfers.

Implements a git remote helper that transparently handles large
packfile transfers via S3 when the server signals that the response
exceeds the Lambda streaming limit.

Usage:
    git clone gitgateway::https://git.example.com/group/repo.git

Normal flow: passes through to standard HTTPS git operations.
Large transfer: detects X-GitGateway-LargeTransfer header and
fetches the packfile from S3 instead.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

LOG = logging.getLogger(__name__)

LARGE_TRANSFER_HEADER = "X-GitGateway-LargeTransfer"


@dataclass
class RemoteHelperConfig:
    """Configuration for the remote helper.

    Attributes:
        api_url: The GitGateway API URL.
        token: Authentication token (JWT).
        s3_bucket: S3 bucket for large packfiles.
    """

    api_url: str = ""
    token: str = ""
    s3_bucket: str = ""


def parse_remote_url(url: str) -> str:
    """Parse the remote URL from the git remote helper protocol.

    Strips the 'gitgateway::' prefix if present.

    Args:
        url: The raw URL from git.

    Returns:
        The cleaned HTTPS URL.
    """
    prefix = "gitgateway::"
    if url.startswith(prefix):
        return url[len(prefix):]
    return url


def is_large_transfer_response(headers: dict[str, str]) -> bool:
    """Check if a response indicates a large transfer.

    Args:
        headers: Response headers dict.

    Returns:
        True if the large transfer header is present.
    """
    return headers.get(LARGE_TRANSFER_HEADER, "").lower() == "true"


def build_auth_header(token: str) -> dict[str, str]:
    """Build the authorization header for requests.

    Args:
        token: The JWT token.

    Returns:
        Dictionary with Authorization header.
    """
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def main(args: list[str] | None = None) -> int:
    """Main entry point for the git remote helper.

    This is registered as the 'git-remote-gitgateway' console script.
    Git invokes this with the remote name and URL as arguments.

    Args:
        args: Command line arguments (remote_name, url).

    Returns:
        Exit code.
    """
    argv = args if args is not None else sys.argv[1:]

    if len(argv) < 2:
        sys.stderr.write(
            "Usage: git-remote-gitgateway <remote> <url>\n",
        )
        return 1

    remote_url = parse_remote_url(argv[1])
    LOG.info("GitGateway remote helper for: %s", remote_url)

    # In production, this would:
    # 1. Read capabilities from stdin
    # 2. Handle 'connect' command for git protocol
    # 3. Detect large transfers and use S3
    # For now, output capabilities and signal passthrough
    _handle_capabilities()
    return 0


def _handle_capabilities() -> None:
    """Output git remote helper capabilities.

    Signals to git what this helper supports.
    """
    sys.stdout.write("capabilities\n")
    sys.stdout.write("connect\n")
    sys.stdout.write("\n")
    sys.stdout.flush()
