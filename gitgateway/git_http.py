"""Git Smart HTTP protocol handler for GitGateway.

Implements the server side of the Git Smart HTTP protocol by invoking
git-http-backend as a CGI subprocess. Handles:
- GET /<group>/<repo>.git/info/refs?service=<service> (ref discovery)
- POST /<group>/<repo>.git/git-upload-pack (fetch/clone)
- POST /<group>/<repo>.git/git-receive-pack (push)
"""

from __future__ import annotations

import logging
import os
import subprocess  # nosec B404 - Required to invoke git-http-backend CGI
from dataclasses import dataclass, field
from pathlib import Path

from gitgateway.models import AccessLevel

LOG = logging.getLogger(__name__)

# Threshold for large packfile responses (20MB)
LARGE_TRANSFER_THRESHOLD = 20 * 1024 * 1024

# Default git binary path (Lambda layer provides this)
GIT_HTTP_BACKEND_PATH = "/usr/libexec/git-core/git-http-backend"


class GitHttpError(Exception):
    """Raised when a Git HTTP operation fails."""

    def __init__(self, message: str, status_code: int = 500) -> None:
        """Initialize with message and HTTP status code.

        Args:
            message: Error description.
            status_code: HTTP status code to return to client.
        """
        super().__init__(message)
        self.status_code = status_code


@dataclass
class GitHttpRequest:
    """Represents a Git HTTP protocol request.

    Attributes:
        method: HTTP method (GET or POST).
        path_info: The path after the repo (e.g., /info/refs).
        query_string: Query parameters (e.g., service=git-upload-pack).
        content_type: Request content type.
        body: Request body bytes (for POST requests).
        remote_user: Authenticated username.
        group: Repository group name.
        repo: Repository name.
    """

    method: str
    path_info: str
    query_string: str = ""
    content_type: str = ""
    body: bytes = b""
    remote_user: str = ""
    group: str = ""
    repo: str = ""


@dataclass
class GitHttpResponse:
    """Represents a Git HTTP protocol response.

    Attributes:
        status_code: HTTP status code.
        content_type: Response content type.
        body: Response body bytes.
        headers: Additional response headers.
    """

    status_code: int = 200
    content_type: str = ""
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)


def parse_git_request(  # pylint: disable=too-many-positional-arguments
    method: str,
    path: str,
    query_string: str = "",
    content_type: str = "",
    body: bytes = b"",
    remote_user: str = "",
) -> GitHttpRequest:
    """Parse a raw HTTP request into a GitHttpRequest.

    Expected path format: /<group>/<repo>.git/<git-path>

    Args:
        method: HTTP method.
        path: Full request path.
        query_string: Query string.
        content_type: Content-Type header value.
        body: Request body.
        remote_user: Authenticated username.

    Returns:
        A parsed GitHttpRequest.

    Raises:
        GitHttpError: If the path format is invalid.
    """
    # Strip leading slash
    clean_path = path.lstrip("/")

    # Find the .git/ separator
    git_idx = clean_path.find(".git/")
    if git_idx == -1:
        raise GitHttpError("Invalid git URL format", status_code=400)

    # Extract group/repo from prefix
    repo_path = clean_path[:git_idx + 4]  # includes .git
    git_path = clean_path[git_idx + 4:]  # path after .git

    parts = repo_path.split("/")
    if len(parts) < 2:
        raise GitHttpError("Invalid repository path", status_code=400)

    # Last part is repo.git, everything before is group
    repo_with_git = parts[-1]
    repo_name = repo_with_git[:-4]  # Remove .git suffix
    group_name = "/".join(parts[:-1])

    return GitHttpRequest(
        method=method,
        path_info=f"/{repo_path}/{git_path}",
        query_string=query_string,
        content_type=content_type,
        body=body,
        remote_user=remote_user,
        group=group_name,
        repo=repo_name,
    )


def get_required_access_level(request: GitHttpRequest) -> AccessLevel:
    """Determine the access level required for a request.

    Args:
        request: The parsed Git HTTP request.

    Returns:
        The minimum AccessLevel required.
    """
    service = ""
    if request.query_string:
        for param in request.query_string.split("&"):
            if param.startswith("service="):
                service = param[8:]
                break

    if service == "git-receive-pack" or "git-receive-pack" in request.path_info:
        return AccessLevel.CONTRIBUTOR

    return AccessLevel.READONLY


def build_cgi_environ(
    request: GitHttpRequest,
    repo_root: Path,
) -> dict[str, str]:
    """Build CGI environment variables for git-http-backend.

    Args:
        request: The parsed Git HTTP request.
        repo_root: Path to the directory containing the bare repo.

    Returns:
        Dictionary of environment variables.
    """
    environ = {
        "GIT_PROJECT_ROOT": str(repo_root),
        "GIT_HTTP_EXPORT_ALL": "1",
        "PATH_INFO": request.path_info,
        "QUERY_STRING": request.query_string,
        "REQUEST_METHOD": request.method,
        "CONTENT_TYPE": request.content_type,
        "REMOTE_USER": request.remote_user,
        "SERVER_PROTOCOL": "HTTP/1.1",
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    if request.body:
        environ["CONTENT_LENGTH"] = str(len(request.body))

    return environ


def execute_git_http_backend(
    request: GitHttpRequest,
    repo_root: Path,
    git_http_backend: str = GIT_HTTP_BACKEND_PATH,
) -> GitHttpResponse:
    """Execute git-http-backend as a CGI subprocess.

    Args:
        request: The parsed Git HTTP request.
        repo_root: Path to the directory containing the bare repo.
        git_http_backend: Path to the git-http-backend binary.

    Returns:
        A GitHttpResponse with the CGI output.

    Raises:
        GitHttpError: If the subprocess fails.
    """
    environ = build_cgi_environ(request, repo_root)

    try:
        result = subprocess.run(  # nosec B603 - git_http_backend path is controlled
            [git_http_backend],
            input=request.body,
            capture_output=True,
            env=environ,
            timeout=300,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitHttpError(
            f"git-http-backend not found at {git_http_backend}",
            status_code=500,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise GitHttpError(
            "git-http-backend timed out",
            status_code=504,
        ) from exc

    if result.returncode != 0 and not result.stdout:
        stderr_msg = result.stderr.decode("utf-8", errors="replace")
        raise GitHttpError(
            f"git-http-backend failed: {stderr_msg}",
            status_code=500,
        )

    return _parse_cgi_response(result.stdout)


def _parse_cgi_response(raw_output: bytes) -> GitHttpResponse:
    """Parse CGI output into a GitHttpResponse.

    CGI output format:
        Header: value\\r\\n
        ...\\r\\n
        \\r\\n
        <body>

    Args:
        raw_output: Raw bytes from the CGI process stdout.

    Returns:
        A parsed GitHttpResponse.
    """
    # Find the header/body separator
    separator = b"\r\n\r\n"
    sep_idx = raw_output.find(separator)
    if sep_idx == -1:
        # Try just \n\n
        separator = b"\n\n"
        sep_idx = raw_output.find(separator)

    if sep_idx == -1:
        # No headers, treat entire output as body
        return GitHttpResponse(status_code=200, body=raw_output)

    header_bytes = raw_output[:sep_idx]
    body = raw_output[sep_idx + len(separator):]

    # Parse headers
    headers: dict[str, str] = {}
    status_code = 200
    content_type = ""

    for line in header_bytes.decode("utf-8", errors="replace").split("\n"):
        line = line.strip("\r")
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()

        if key == "status":
            # CGI Status header: "200 OK" or "404 Not Found"
            try:
                status_code = int(value.split()[0])
            except (ValueError, IndexError):
                pass
        elif key == "content-type":
            content_type = value
        else:
            headers[key] = value

    return GitHttpResponse(
        status_code=status_code,
        content_type=content_type,
        body=body,
        headers=headers,
    )


def check_large_transfer(response: GitHttpResponse) -> GitHttpResponse:
    """Check if the response exceeds the large transfer threshold.

    If the response body exceeds LARGE_TRANSFER_THRESHOLD, this would
    normally write the packfile to S3 and return a redirect header.
    For now, it adds the X-GitGateway-LargeTransfer header to signal
    the client helper.

    Args:
        response: The git HTTP response to check.

    Returns:
        The response, potentially modified with large transfer header.
    """
    if len(response.body) > LARGE_TRANSFER_THRESHOLD:
        response.headers["X-GitGateway-LargeTransfer"] = "true"
        LOG.warning(
            "Large transfer detected: %d bytes exceeds threshold",
            len(response.body),
        )
    return response


def handle_git_request(
    request: GitHttpRequest,
    repo_root: Path,
    git_http_backend: str = GIT_HTTP_BACKEND_PATH,
) -> GitHttpResponse:
    """Handle a complete Git HTTP request.

    This is the main entry point for the Git protocol handler.
    It executes git-http-backend and checks for large transfers.

    Args:
        request: The parsed Git HTTP request.
        repo_root: Path to the directory containing bare repos.
        git_http_backend: Path to the git-http-backend binary.

    Returns:
        A GitHttpResponse ready to send to the client.

    Raises:
        GitHttpError: If the operation fails.
    """
    response = execute_git_http_backend(request, repo_root, git_http_backend)
    return check_large_transfer(response)
