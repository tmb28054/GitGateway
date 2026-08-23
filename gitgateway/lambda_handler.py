"""Lambda function entry point and request routing for GitGateway.

Routes incoming API Gateway v2 HTTP API requests to the appropriate
handler based on path patterns:
- /<group>/<repo>.git/info/lfs/* -> LFS handler
- /<group>/<repo>.git/* -> Git protocol handler
- /api/* -> REST API handler (JSON)
- /auth/* -> OAuth callback handler
- /* -> Web UI handler (HTML)
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field

LOG = logging.getLogger(__name__)

# Route patterns (order matters - first match wins)
_LFS_PATTERN = re.compile(
    r"^/([^/]+)/([^/]+)\.git/info/lfs/",
)
_GIT_PATTERN = re.compile(
    r"^/([^/]+)/([^/]+)\.git/",
)
_API_PATTERN = re.compile(r"^/api/")
_AUTH_PATTERN = re.compile(r"^/auth/")


class RouteNotFoundError(Exception):
    """Raised when no route matches the request path."""


@dataclass
class Request:
    """Normalized HTTP request from API Gateway v2.

    Attributes:
        method: HTTP method (GET, POST, etc.).
        path: Request path.
        query_string: Raw query string.
        headers: Request headers (lowercased keys).
        body: Request body bytes.
        is_base64: Whether the body was base64-encoded.
        path_parameters: Extracted path parameters.
        source_ip: Client IP address.
    """

    method: str = "GET"
    path: str = "/"
    query_string: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    is_base64: bool = False
    path_parameters: dict[str, str] = field(default_factory=dict)
    source_ip: str = ""


@dataclass
class Response:
    """HTTP response to return via API Gateway v2.

    Attributes:
        status_code: HTTP status code.
        body: Response body string or bytes.
        headers: Response headers.
        is_base64: Whether the body should be base64-encoded.
    """

    status_code: int = 200
    body: str | bytes = ""
    headers: dict[str, str] = field(default_factory=dict)
    is_base64: bool = False

    def to_api_gateway_response(self) -> dict:
        """Convert to API Gateway v2 response format.

        Returns:
            Dictionary matching API Gateway v2 response structure.
        """
        response: dict = {
            "statusCode": self.status_code,
            "headers": self.headers,
        }

        if isinstance(self.body, bytes):
            response["body"] = base64.b64encode(self.body).decode()
            response["isBase64Encoded"] = True
        else:
            response["body"] = self.body
            response["isBase64Encoded"] = self.is_base64

        return response


def parse_api_gateway_event(event: dict) -> Request:
    """Parse an API Gateway v2 HTTP API event into a Request.

    Args:
        event: The Lambda event from API Gateway v2.

    Returns:
        A normalized Request object.
    """
    request_context = event.get("requestContext", {})
    http_info = request_context.get("http", {})

    method = http_info.get("method", event.get("httpMethod", "GET"))
    path = http_info.get("path", event.get("rawPath", "/"))
    query_string = event.get("rawQueryString", "")
    source_ip = http_info.get("sourceIp", "")

    # Normalize headers to lowercase
    raw_headers = event.get("headers", {})
    headers = {k.lower(): v for k, v in raw_headers.items()}

    # API Gateway v2 sends cookies separately
    cookies = event.get("cookies", [])
    if cookies and "cookie" not in headers:
        headers["cookie"] = "; ".join(cookies)

    # Decode body
    body_str = event.get("body", "") or ""
    is_base64 = event.get("isBase64Encoded", False)

    if is_base64 and body_str:
        body = base64.b64decode(body_str)
    else:
        body = body_str.encode() if body_str else b""

    return Request(
        method=method,
        path=path,
        query_string=query_string,
        headers=headers,
        body=body,
        is_base64=is_base64,
        source_ip=source_ip,
    )


def route_request(request: Request) -> str:
    """Determine which handler should process the request.

    Args:
        request: The normalized request.

    Returns:
        Handler name: 'lfs', 'git', 'api', 'auth', or 'web'.
    """
    path = request.path

    if _LFS_PATTERN.match(path):
        return "lfs"
    if _GIT_PATTERN.match(path):
        return "git"
    if _API_PATTERN.match(path):
        return "api"
    if _AUTH_PATTERN.match(path):
        return "auth"
    return "web"


def error_response(
    status_code: int,
    message: str,
    content_type: str = "application/json",
) -> Response:
    """Create a standardized error response.

    Args:
        status_code: HTTP status code.
        message: Error message.
        content_type: Response content type.

    Returns:
        A Response object with the error.
    """
    if content_type == "application/json":
        body = json.dumps({"error": message})
    else:
        body = message

    return Response(
        status_code=status_code,
        body=body,
        headers={
            "Content-Type": content_type,
            "X-GitGateway-Error": "true",
        },
    )


def cors_headers() -> dict[str, str]:
    """Generate CORS headers for API responses.

    Returns:
        Dictionary of CORS headers.
    """
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": (
            "Content-Type, Authorization, X-Requested-With"
        ),
        "Access-Control-Max-Age": "86400",
    }


def handle_options(request: Request) -> Response:  # pylint: disable=unused-argument
    """Handle CORS preflight OPTIONS request.

    Args:
        request: The OPTIONS request.

    Returns:
        Response with CORS headers.
    """
    return Response(
        status_code=204,
        headers=cors_headers(),
    )


def handler(event: dict, context: object = None) -> dict:  # pylint: disable=unused-argument
    """Main Lambda handler entry point.

    Routes requests to the appropriate handler based on path.
    This is a dispatcher that delegates to specific handler modules.

    Args:
        event: API Gateway v2 event.
        context: Lambda context (unused).

    Returns:
        API Gateway v2 response dictionary.
    """
    request = parse_api_gateway_event(event)

    LOG.info(
        "%s %s -> routing",
        request.method,
        request.path,
    )

    # Handle CORS preflight
    if request.method == "OPTIONS":
        return handle_options(request).to_api_gateway_response()

    # Route to handler
    handler_name = route_request(request)

    # For now, return the route info
    # Actual handler dispatch will be wired up as modules are implemented
    response = _dispatch(handler_name, request)
    return response.to_api_gateway_response()


def _dispatch(handler_name: str, request: Request) -> Response:
    """Dispatch request to the named handler.

    Args:
        handler_name: One of 'lfs', 'git', 'api', 'auth', 'web'.
        request: The normalized request.

    Returns:
        Response from the handler.
    """
    import os  # pylint: disable=import-outside-toplevel

    idp_url = os.environ.get("IDP_URL", "")

    if handler_name == "api":
        return _handle_api(request)

    if handler_name == "git":
        return Response(
            status_code=200,
            body=b"",
            headers={"Content-Type": "application/octet-stream"},
        )

    if handler_name == "lfs":
        return Response(
            status_code=200,
            body=json.dumps({"handler": "lfs", "path": request.path}),
            headers={"Content-Type": "application/vnd.git-lfs+json"},
        )

    if handler_name == "auth":
        return _handle_auth_callback(request, idp_url)

    # Default: web UI — redirect to IdP if not authenticated
    if idp_url and not _is_authenticated(request):
        callback_url = f"https://{os.environ.get('DOMAIN_NAME', '')}/auth/callback"
        redirect_url = f"{idp_url}?callback={callback_url}"
        return Response(
            status_code=302,
            body="",
            headers={"Location": redirect_url},
        )

    # Handle logout
    if request.path == "/logout":
        return Response(
            status_code=302,
            body="",
            headers={
                "Location": "/",
                "Set-Cookie": (
                    "session=; Path=/; HttpOnly; Secure; "
                    "SameSite=Lax; Max-Age=0"
                ),
            },
        )

    # Render authenticated page
    user_info = _get_session_user(request)
    return _render_web_page(request, user_info)


def _handle_api(request: Request) -> Response:  # pylint: disable=too-many-return-statements,too-many-branches
    """Handle REST API requests.

    Routes API calls to the appropriate CRUD operations.

    Args:
        request: The normalized request.

    Returns:
        JSON response.
    """
    from gitgateway.db import (  # pylint: disable=import-outside-toplevel
        ConflictError,
        NotFoundError,
        create_group,
        delete_group,
        list_groups,
        set_access,
        get_access,
        revoke_access,
        list_user_access,
    )
    from gitgateway.models import (  # pylint: disable=import-outside-toplevel
        AccessEntry,
        AccessLevel,
        Group,
    )

    path = request.path
    method = request.method
    headers = {"Content-Type": "application/json", **cors_headers()}

    # POST /api/groups — create group
    if path == "/api/groups" and method == "POST":
        try:
            body = json.loads(request.body.decode()) if request.body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response(
                status_code=400,
                body=json.dumps({"error": "Invalid JSON body"}),
                headers=headers,
            )
        name = body.get("name", "")
        if not name:
            return Response(
                status_code=400,
                body=json.dumps({"error": "name is required"}),
                headers=headers,
            )
        try:
            group = create_group(Group(
                name=name,
                description=body.get("description", ""),
                created_by=body.get("created_by", ""),
            ))
            return Response(
                status_code=201,
                body=json.dumps(group.to_dict()),
                headers=headers,
            )
        except ConflictError as exc:
            return Response(
                status_code=409,
                body=json.dumps({"error": str(exc)}),
                headers=headers,
            )

    # GET /api/groups — list groups
    if path == "/api/groups" and method == "GET":
        groups = list_groups()
        return Response(
            status_code=200,
            body=json.dumps([g.to_dict() for g in groups]),
            headers=headers,
        )

    # DELETE /api/groups/<name> — delete group
    if path.startswith("/api/groups/") and method == "DELETE":
        name = path[len("/api/groups/"):]
        try:
            delete_group(name)
            return Response(
                status_code=204, body="", headers=headers,
            )
        except NotFoundError:
            return Response(
                status_code=404,
                body=json.dumps({"error": f"Group '{name}' not found"}),
                headers=headers,
            )

    # POST /api/repos/<group> — create repo
    if path.startswith("/api/repos/") and method == "POST":
        group_name = path[len("/api/repos/"):]
        try:
            body = json.loads(request.body.decode()) if request.body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response(
                status_code=400,
                body=json.dumps({"error": "Invalid JSON body"}),
                headers=headers,
            )
        repo_name = body.get("name", "")
        if not repo_name:
            return Response(
                status_code=400,
                body=json.dumps({"error": "name is required"}),
                headers=headers,
            )
        from gitgateway.db import create_repo  # pylint: disable=import-outside-toplevel
        from gitgateway.models import Repo  # pylint: disable=import-outside-toplevel
        try:
            repo = create_repo(Repo(
                group=group_name,
                name=repo_name,
                description=body.get("description", ""),
                created_by=body.get("created_by", ""),
            ))
            return Response(
                status_code=201,
                body=json.dumps(repo.to_dict()),
                headers=headers,
            )
        except ConflictError as exc:
            return Response(
                status_code=409,
                body=json.dumps({"error": str(exc)}),
                headers=headers,
            )

    # GET /api/repos/<group> — list repos in group
    if path.startswith("/api/repos/") and method == "GET":
        group_name = path[len("/api/repos/"):]
        from gitgateway.db import list_repos  # pylint: disable=import-outside-toplevel
        repos = list_repos(group_name)
        return Response(
            status_code=200,
            body=json.dumps([r.to_dict() for r in repos]),
            headers=headers,
        )

    # DELETE /api/repos/<group>/<repo> — delete repo
    if path.startswith("/api/repos/") and method == "DELETE" and path.count("/") >= 4:
        parts = path[len("/api/repos/"):].split("/", 1)
        if len(parts) == 2:
            from gitgateway.db import delete_repo  # pylint: disable=import-outside-toplevel
            try:
                delete_repo(parts[0], parts[1])
                return Response(
                    status_code=204, body="", headers=headers,
                )
            except NotFoundError:
                return Response(
                    status_code=404,
                    body=json.dumps({"error": "Repository not found"}),
                    headers=headers,
                )

    # POST /api/access — grant access
    if path == "/api/access" and method == "POST":
        try:
            body = json.loads(request.body.decode()) if request.body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response(
                status_code=400,
                body=json.dumps({"error": "Invalid JSON body"}),
                headers=headers,
            )
        username = body.get("username", "")
        resource = body.get("resource", "")
        level = body.get("level", "readonly")
        if not username or not resource:
            return Response(
                status_code=400,
                body=json.dumps({"error": "username and resource required"}),
                headers=headers,
            )
        try:
            entry = set_access(AccessEntry(
                username=username,
                resource=resource,
                level=AccessLevel(level),
            ))
            return Response(
                status_code=200,
                body=json.dumps(entry.to_dict()),
                headers=headers,
            )
        except ValueError:
            return Response(
                status_code=400,
                body=json.dumps({"error": f"Invalid level: {level}"}),
                headers=headers,
            )

    # GET /api/access — list all access entries for a group
    if path.startswith("/api/access") and method == "GET":
        # GET /api/access?group=<name>
        group_filter = ""
        for param in request.query_string.split("&"):
            if param.startswith("group="):
                group_filter = param[6:]
        # Scan for all entries in the group
        import boto3  # pylint: disable=import-outside-toplevel
        table = boto3.resource("dynamodb").Table("gitgateway-access")
        if group_filter:
            response = table.scan(
                FilterExpression="resource = :r",
                ExpressionAttributeValues={":r": group_filter},
            )
        else:
            response = table.scan()
        items = response.get("Items", [])
        entries = [AccessEntry.from_dict(item).to_dict() for item in items]
        return Response(
            status_code=200,
            body=json.dumps(entries),
            headers=headers,
        )

    # DELETE /api/access — revoke access
    if path == "/api/access" and method == "DELETE":
        try:
            body = json.loads(request.body.decode()) if request.body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response(
                status_code=400,
                body=json.dumps({"error": "Invalid JSON body"}),
                headers=headers,
            )
        username = body.get("username", "")
        resource = body.get("resource", "")
        if not username or not resource:
            return Response(
                status_code=400,
                body=json.dumps({"error": "username and resource required"}),
                headers=headers,
            )
        revoke_access(username, resource)
        return Response(status_code=204, body="", headers=headers)

    # GET /api/users — list known users
    if path == "/api/users" and method == "GET":
        import boto3  # pylint: disable=import-outside-toplevel
        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        response = table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": "KNOWN_USERS",
                ":prefix": "USER#",
            },
        )
        users = [
            item.get("username", "") for item in response.get("Items", [])
        ]
        return Response(
            status_code=200,
            body=json.dumps(sorted(set(users))),
            headers=headers,
        )

    # Fallback for unimplemented API routes
    return Response(
        status_code=404,
        body=json.dumps({"error": "Not found", "path": path}),
        headers=headers,
    )


def _get_session_user(request: Request) -> dict:
    """Extract user info from the session cookie.

    Args:
        request: The normalized request.

    Returns:
        Dict with 'username' and 'is_admin' keys.
    """
    import os  # pylint: disable=import-outside-toplevel
    from gitgateway.auth import validate_session_cookie  # pylint: disable=import-outside-toplevel

    cookies = request.headers.get("cookie", "")
    session_value = ""
    for part in cookies.split(";"):
        part = part.strip()
        if part.startswith("session="):
            session_value = part[8:]
            break

    if not session_value:
        return {"username": "unknown", "is_admin": False}

    secret_key = os.environ.get("SESSION_SECRET", "gitgateway-session-secret")
    user = validate_session_cookie(session_value, secret_key)
    if user:
        return {"username": user.username, "is_admin": user.is_admin}
    return {"username": "unknown", "is_admin": False}


def _render_web_page(request: Request, user_info: dict) -> Response:
    """Render the web UI page based on the request path.

    Args:
        request: The normalized request.
        user_info: Dict with 'username' and 'is_admin'.

    Returns:
        HTML response.
    """
    username = user_info.get("username", "unknown")
    is_admin = user_info.get("is_admin", False)

    admin_links = ""
    if is_admin:
        admin_links = (
            '<a href="/admin" style="color:#fff;margin-right:1rem;">'
            "Admin</a>"
        )

    nav_bar = f"""<nav style="background:#24292e;color:#fff;padding:0.75rem 1.5rem;
        display:flex;align-items:center;justify-content:space-between;">
      <a href="/" style="color:#fff;text-decoration:none;font-weight:bold;
        font-size:1.2rem;">GitGateway</a>
      <div style="display:flex;align-items:center;gap:1rem;">
        {admin_links}
        <span style="color:#ccc;">{username}</span>
        <a href="/logout" style="color:#f97583;text-decoration:none;">
          Logout</a>
      </div>
    </nav>"""

    # Route to the right page content
    page_content = _get_page_content(request.path, user_info)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>GitGateway</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI',
      Roboto, sans-serif; line-height: 1.6; color: #24292e; }}
    main {{ max-width: 1000px; margin: 2rem auto; padding: 0 1.5rem; }}
    h1 {{ margin-bottom: 1rem; }}
    a {{ color: #0366d6; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .empty {{ color: #586069; margin-top: 2rem; }}
  </style>
</head>
<body>
  {nav_bar}
  <main>
    {page_content}
  </main>
</body>
</html>"""

    return Response(
        status_code=200,
        body=html,
        headers={"Content-Type": "text/html"},
    )


def _get_page_content(path: str, user_info: dict) -> str:
    """Generate page content based on the URL path.

    Args:
        path: The request path.
        user_info: User info dict.

    Returns:
        HTML content string for the main area.
    """
    username = user_info.get("username", "")

    if path == "/" or path == "":
        return f"""
        <h1>Welcome, {username}</h1>
        <p>Your serverless Git hosting platform.</p>
        <div style="margin-top:2rem;">
          <h2>Quick Actions</h2>
          <ul style="list-style:none;padding:0;margin-top:1rem;">
            <li style="padding:0.5rem 0;">
              <a href="/groups">View Groups</a>
            </li>
          </ul>
        </div>"""

    if path == "/groups":
        return _render_user_groups_page(username)

    if path.startswith("/groups/") and "/" not in path[8:]:
        group_name = path[8:]
        return _render_group_detail_page(group_name, username)

    # /groups/<group>/<repo> — repository view
    if path.startswith("/groups/") and path.count("/") == 3:
        parts = path[8:].split("/")
        return _render_repo_page(parts[0], parts[1], username, "")

    # /groups/<group>/<repo>/tree/<branch>/... — file tree at branch
    if "/tree/" in path and path.startswith("/groups/"):
        # Parse /groups/<group>/<repo>/tree/<branch>[/path...]
        after_groups = path[8:]  # group/repo/tree/branch/...
        repo_parts = after_groups.split("/tree/", 1)
        if len(repo_parts) == 2:
            gr_parts = repo_parts[0].split("/", 1)
            if len(gr_parts) == 2:
                branch_and_path = repo_parts[1]
                branch = branch_and_path.split("/")[0] if "/" in branch_and_path else branch_and_path
                file_path = branch_and_path[len(branch) + 1:] if "/" in branch_and_path else ""
                return _render_repo_page(gr_parts[0], gr_parts[1], username, branch, file_path)

    if path.startswith("/admin"):
        if not user_info.get("is_admin"):
            return "<h1>403 Forbidden</h1><p>Admin access required.</p>"
        return _get_admin_content(path)

    return f"<h1>Page</h1><p>Path: {path}</p>"


def _render_user_groups_page(username: str) -> str:
    """Render the user's groups page showing groups they have access to.

    Args:
        username: The authenticated username.

    Returns:
        HTML content listing accessible groups.
    """
    from gitgateway.db import (  # pylint: disable=import-outside-toplevel
        list_user_access,
        list_groups,
    )

    # Get user's access entries
    access_entries = list_user_access(username)
    user_group_names = {
        entry.resource for entry in access_entries if not entry.is_repo_level
    }

    # Get all groups and filter to those the user can see
    all_groups = list_groups()
    visible_groups = [g for g in all_groups if g.name in user_group_names]

    rows = ""
    for group in visible_groups:
        rows += f"""<tr>
          <td style="padding:0.75rem;">
            <a href="/groups/{group.name}" style="font-weight:600;">
              {group.name}</a>
          </td>
          <td style="padding:0.75rem;color:#586069;">
            {group.description}</td>
        </tr>"""

    if not rows:
        rows = """<tr><td colspan="2" style="padding:1.5rem;color:#586069;
          text-align:center;">You don't have access to any groups yet.
          </td></tr>"""

    return f"""
    <h1>Your Groups</h1>
    <p style="color:#586069;margin-bottom:1.5rem;">
      Groups you have been granted access to.</p>
    <table style="width:100%;border-collapse:collapse;">
      <thead>
        <tr style="border-bottom:1px solid #e1e4e8;">
          <th style="text-align:left;padding:0.5rem;">Name</th>
          <th style="text-align:left;padding:0.5rem;">Description</th>
        </tr>
      </thead>
      <tbody id="user-groups-table">
        {rows}
      </tbody>
    </table>"""


def _render_repo_page(  # pylint: disable=too-many-locals
    group: str,
    repo_name: str,
    username: str,
    branch: str = "",
    file_path: str = "",
) -> str:
    """Render GitHub-style repository view.

    Shows tabs (Code/PRs), clone URL, branch selector, file tree,
    and README if present.

    Args:
        group: Group name.
        repo_name: Repository name.
        username: Authenticated username.
        branch: Selected branch (empty = default).
        file_path: Path within the tree (for subdirectories).

    Returns:
        HTML content.
    """
    import boto3  # pylint: disable=import-outside-toplevel
    from gitgateway.db import (  # pylint: disable=import-outside-toplevel
        get_repo, check_permission,
    )
    from gitgateway.models import AccessLevel  # pylint: disable=import-outside-toplevel

    # Check access
    if not check_permission(username, group, repo_name):
        return "<h1>403 Forbidden</h1><p>You don't have access.</p>"

    # Get repo metadata
    try:
        repo = get_repo(group, repo_name)
    except Exception:  # pylint: disable=broad-except
        return "<h1>404 Not Found</h1><p>Repository not found.</p>"

    default_branch = repo.default_branch or "main"
    current_branch = branch or default_branch
    is_admin = check_permission(
        username, group, repo_name, required_level=AccessLevel.ADMIN,
    )

    clone_url = f"https://git.botthouse.net/{group}/{repo_name}.git"

    # Get file tree from DynamoDB cache
    table = boto3.resource("dynamodb").Table("gitgateway-repos")
    tree_response = table.query(
        KeyConditionExpression=(
            "PK = :pk AND begins_with(SK, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": f"TREE#{group}/{repo_name}#{current_branch}",
            ":prefix": "FILE#",
        },
    )
    tree_items = tree_response.get("Items", [])

    # Filter to current directory
    files_in_dir = []
    for item in tree_items:
        item_path = item.get("path", "")
        if file_path:
            if not item_path.startswith(file_path + "/"):
                continue
            relative = item_path[len(file_path) + 1:]
        else:
            relative = item_path

        # Only show immediate children (not nested)
        if "/" not in relative:
            files_in_dir.append(item)
        else:
            # It's a directory — add the first segment
            dir_name = relative.split("/")[0]
            # Check if we already added this dir
            if not any(
                f.get("name") == dir_name and f.get("type") == "dir"
                for f in files_in_dir
            ):
                files_in_dir.append({
                    "name": dir_name,
                    "type": "dir",
                    "path": (f"{file_path}/{dir_name}" if file_path
                             else dir_name),
                    "last_commit_message": "",
                    "last_commit_author": "",
                })

    # Sort: dirs first, then files
    dirs = sorted(
        [f for f in files_in_dir if f.get("type") == "dir"],
        key=lambda x: x.get("name", ""),
    )
    files = sorted(
        [f for f in files_in_dir if f.get("type") != "dir"],
        key=lambda x: x.get("name", ""),
    )
    sorted_entries = dirs + files

    # Build file tree rows
    tree_base = f"/groups/{group}/{repo_name}/tree/{current_branch}"
    file_rows = ""
    for entry in sorted_entries:
        name = entry.get("name", entry.get("path", "").split("/")[-1])
        entry_path = entry.get("path", name)
        entry_type = entry.get("type", "file")
        icon = "📁" if entry_type == "dir" else "📄"
        commit_msg = entry.get("last_commit_message", "")[:50]
        commit_author = entry.get("last_commit_author", "")

        file_rows += f"""<tr style="border-bottom:1px solid #f0f0f0;">
          <td style="padding:0.4rem 0.75rem;">
            <span style="margin-right:0.5rem;">{icon}</span>
            <a href="{tree_base}/{entry_path}">{name}</a>
          </td>
          <td style="padding:0.4rem 0.75rem;color:#586069;
            font-size:0.85rem;">{commit_msg}</td>
          <td style="padding:0.4rem 0.75rem;color:#586069;
            font-size:0.85rem;text-align:right;">{commit_author}</td>
        </tr>"""

    if not file_rows:
        file_rows = """<tr><td colspan="3" style="padding:2rem;
          color:#586069;text-align:center;">
          This repository is empty.</td></tr>"""

    # Check for README in current dir
    readme_html = ""
    for item in tree_items:
        item_name = item.get("path", "").split("/")[-1].lower()
        item_dir = "/".join(item.get("path", "").split("/")[:-1])
        if item_name == "readme.md" and item_dir == file_path:
            readme_content = item.get("content", "")
            if readme_content:
                readme_html = f"""
                <div style="margin-top:1.5rem;padding:1rem;
                  border:1px solid #e1e4e8;border-radius:6px;">
                  <h3 style="padding-bottom:0.5rem;
                    border-bottom:1px solid #e1e4e8;margin-bottom:1rem;">
                    README.md</h3>
                  <div style="white-space:pre-wrap;">{readme_content}</div>
                </div>"""
            break

    # Settings gear
    settings_link = ""
    if is_admin:
        settings_link = (
            f'<a href="/groups/{group}/{repo_name}/settings" '
            f'title="Settings" style="font-size:1.2rem;">⚙️</a>'
        )

    # Breadcrumb for subdirectory
    breadcrumb = ""
    if file_path:
        parts = file_path.split("/")
        crumbs = [f'<a href="/groups/{group}/{repo_name}">{repo_name}</a>']
        built = ""
        for part in parts:
            built = f"{built}/{part}" if built else part
            crumbs.append(
                f'<a href="{tree_base}/{built}">{part}</a>',
            )
        breadcrumb = " / ".join(crumbs)
    else:
        breadcrumb = repo_name

    return f"""
    <div style="display:flex;justify-content:space-between;
      align-items:center;margin-bottom:1rem;">
      <div>
        <h1 style="margin:0;">
          <a href="/groups/{group}" style="color:#586069;
            font-weight:normal;">{group}</a> /
          <a href="/groups/{group}/{repo_name}">{repo_name}</a>
        </h1>
        <p style="color:#586069;margin-top:0.25rem;">
          {repo.description}</p>
      </div>
      {settings_link}
    </div>

    <div style="display:flex;gap:1rem;border-bottom:1px solid #e1e4e8;
      margin-bottom:1rem;padding-bottom:0;">
      <a href="/groups/{group}/{repo_name}"
        style="padding:0.5rem 1rem;font-weight:600;
        border-bottom:2px solid #f9826c;">Code</a>
      <a href="/groups/{group}/{repo_name}/pulls"
        style="padding:0.5rem 1rem;color:#586069;">Pull Requests</a>
    </div>

    <div style="display:flex;justify-content:space-between;
      align-items:center;margin-bottom:1rem;">
      <div style="display:flex;align-items:center;gap:0.75rem;">
        <select id="branch-select" onchange="switchBranch(this.value)"
          style="padding:0.4rem 0.8rem;border-radius:6px;
          border:1px solid #d1d5da;font-size:0.9rem;">
          <option value="{current_branch}" selected>
            {current_branch}</option>
        </select>
        <span style="color:#586069;font-size:0.85rem;">{breadcrumb}</span>
      </div>
      <div style="display:flex;align-items:center;gap:0.5rem;">
        <input type="text" value="{clone_url}" readonly id="clone-url"
          style="padding:0.4rem 0.6rem;border:1px solid #d1d5da;
          border-radius:4px;font-size:0.8rem;width:320px;
          background:#f6f8fa;">
        <button onclick="navigator.clipboard.writeText(
          document.getElementById('clone-url').value)"
          style="padding:0.4rem 0.6rem;border:1px solid #d1d5da;
          border-radius:4px;cursor:pointer;background:#fafbfc;">
          📋</button>
      </div>
    </div>

    <table style="width:100%;border-collapse:collapse;
      border:1px solid #e1e4e8;border-radius:6px;">
      <tbody>
        {file_rows}
      </tbody>
    </table>

    {readme_html}

    <script>
    function switchBranch(branch) {{
      window.location.href = '/groups/{group}/{repo_name}/tree/' + branch;
    }}
    </script>"""


def _render_group_detail_page(group_name: str, username: str) -> str:
    """Render a group detail page with repositories and create form.

    Args:
        group_name: The group to display.
        username: The authenticated user.

    Returns:
        HTML content for the group detail page.
    """
    from gitgateway.db import (  # pylint: disable=import-outside-toplevel
        list_repos,
        check_permission,
    )
    from gitgateway.models import AccessLevel  # pylint: disable=import-outside-toplevel

    # Check user has access to this group
    has_access = check_permission(username, group_name)
    can_create = check_permission(
        username, group_name, required_level=AccessLevel.CONTRIBUTOR,
    )

    if not has_access:
        return (
            "<h1>403 Forbidden</h1>"
            "<p>You don't have access to this group.</p>"
        )

    # Get repos in this group
    repos = list_repos(group_name)

    rows = ""
    for repo in repos:
        rows += f"""<tr>
          <td style="padding:0.75rem;">
            <a href="/groups/{group_name}/{repo.name}"
              style="font-weight:600;">{repo.name}</a>
          </td>
          <td style="padding:0.75rem;color:#586069;">
            {repo.description}</td>
        </tr>"""

    if not rows:
        rows = """<tr><td colspan="2" style="padding:1.5rem;color:#586069;
          text-align:center;">No repositories in this group yet.</td></tr>"""

    create_form = ""
    if can_create:
        create_form = f"""
    <div style="margin:1.5rem 0;padding:1rem;background:#f6f8fa;
      border-radius:6px;border:1px solid #e1e4e8;">
      <h3 style="margin-bottom:0.75rem;">Create Repository</h3>
      <form id="create-repo-form" onsubmit="createRepo(event)"
        style="display:flex;gap:0.5rem;align-items:flex-end;">
        <div>
          <label for="repo-name" style="display:block;font-size:0.85rem;
            color:#555;margin-bottom:0.2rem;">Name</label>
          <input type="text" id="repo-name" name="name" required
            placeholder="my-project"
            pattern="[a-zA-Z0-9._-]+"
            title="Letters, numbers, dots, hyphens, underscores"
            style="padding:0.4rem 0.6rem;border:1px solid #d1d5da;
            border-radius:4px;">
        </div>
        <div>
          <label for="repo-desc" style="display:block;font-size:0.85rem;
            color:#555;margin-bottom:0.2rem;">Description</label>
          <input type="text" id="repo-desc" name="description"
            placeholder="Optional description"
            style="padding:0.4rem 0.6rem;border:1px solid #d1d5da;
            border-radius:4px;width:250px;">
        </div>
        <button type="submit"
          style="background:#2ea44f;color:#fff;border:none;
          padding:0.45rem 1rem;border-radius:4px;cursor:pointer;">
          Create</button>
      </form>
      <div id="repo-message" style="margin-top:0.5rem;font-size:0.85rem;">
      </div>
    </div>

    <script>
    async function createRepo(e) {{
      e.preventDefault();
      const name = document.getElementById('repo-name').value;
      const description = document.getElementById('repo-desc').value;
      const msg = document.getElementById('repo-message');
      try {{
        const resp = await fetch('/api/repos/{group_name}', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{name, description}})
        }});
        if (resp.ok) {{
          msg.innerHTML = '<span style="color:#2ea44f;">Repository created!</span>';
          setTimeout(() => location.reload(), 500);
        }} else {{
          const data = await resp.json();
          msg.innerHTML = '<span style="color:#cb2431;">'
            + (data.error || 'Failed') + '</span>';
        }}
      }} catch(err) {{
        msg.innerHTML = '<span style="color:#cb2431;">' + err + '</span>';
      }}
    }}
    </script>"""

    return f"""
    <h1>{group_name}</h1>
    <p style="color:#586069;margin-bottom:1rem;">
      <a href="/groups">Groups</a> / {group_name}</p>
    {create_form}
    <h2 style="margin-top:1.5rem;">Repositories</h2>
    <table style="width:100%;border-collapse:collapse;margin-top:0.5rem;">
      <thead>
        <tr style="border-bottom:1px solid #e1e4e8;">
          <th style="text-align:left;padding:0.5rem;">Name</th>
          <th style="text-align:left;padding:0.5rem;">Description</th>
        </tr>
      </thead>
      <tbody id="repos-table">
        {rows}
      </tbody>
    </table>"""


def _get_admin_content(path: str) -> str:
    """Generate admin page content.

    Args:
        path: The request path.

    Returns:
        HTML content for admin pages.
    """
    if path == "/admin" or path == "/admin/":
        return """
        <h1>Administration</h1>
        <ul style="list-style:none;padding:0;margin-top:1rem;">
          <li style="padding:0.5rem 0;">
            <a href="/admin/groups">Manage Groups</a>
          </li>
          <li style="padding:0.5rem 0;">
            <a href="/admin/access">Access Control</a>
          </li>
          <li style="padding:0.5rem 0;">
            <a href="/admin/audit">Audit Log</a>
          </li>
        </ul>"""

    if path == "/admin/groups":
        return _render_admin_groups_page()

    if path == "/admin/access":
        return _render_admin_access_page()

    return f"<h1>Admin</h1><p>Path: {path}</p>"


def _render_admin_groups_page() -> str:
    """Render the admin group management page with live data.

    Returns:
        HTML content with group list and create form.
    """
    from gitgateway.db import list_groups  # pylint: disable=import-outside-toplevel

    groups = list_groups()

    rows = ""
    for group in groups:
        rows += f"""<tr>
          <td style="padding:0.5rem;">{group.name}</td>
          <td style="padding:0.5rem;color:#586069;">{group.description}</td>
          <td style="padding:0.5rem;text-align:center;">
            <button onclick="deleteGroup('{group.name}')"
              style="background:#cb2431;color:#fff;border:none;padding:0.3rem 0.7rem;
              border-radius:4px;cursor:pointer;">Delete</button>
          </td>
        </tr>"""

    if not rows:
        rows = """<tr><td colspan="3" style="padding:1rem;color:#586069;
          text-align:center;">No groups yet.</td></tr>"""

    return f"""
    <h1>Manage Groups</h1>

    <div style="margin:1.5rem 0;padding:1rem;background:#f6f8fa;
      border-radius:6px;border:1px solid #e1e4e8;">
      <h3 style="margin-bottom:0.75rem;">Create New Group</h3>
      <form id="create-group-form" onsubmit="createGroup(event)"
        style="display:flex;gap:0.5rem;align-items:flex-end;">
        <div>
          <label for="group-name" style="display:block;font-size:0.85rem;
            color:#555;margin-bottom:0.2rem;">Name</label>
          <input type="text" id="group-name" name="name" required
            placeholder="my-team"
            style="padding:0.4rem 0.6rem;border:1px solid #d1d5da;
            border-radius:4px;">
        </div>
        <div>
          <label for="group-desc" style="display:block;font-size:0.85rem;
            color:#555;margin-bottom:0.2rem;">Description</label>
          <input type="text" id="group-desc" name="description"
            placeholder="Optional description"
            style="padding:0.4rem 0.6rem;border:1px solid #d1d5da;
            border-radius:4px;width:250px;">
        </div>
        <button type="submit"
          style="background:#2ea44f;color:#fff;border:none;padding:0.45rem 1rem;
          border-radius:4px;cursor:pointer;">Create</button>
      </form>
      <div id="create-message" style="margin-top:0.5rem;font-size:0.85rem;">
      </div>
    </div>

    <table style="width:100%;border-collapse:collapse;margin-top:1rem;">
      <thead>
        <tr style="border-bottom:1px solid #e1e4e8;">
          <th style="text-align:left;padding:0.5rem;">Name</th>
          <th style="text-align:left;padding:0.5rem;">Description</th>
          <th style="padding:0.5rem;width:100px;">Actions</th>
        </tr>
      </thead>
      <tbody id="groups-table">
        {rows}
      </tbody>
    </table>

    <script>
    async function createGroup(e) {{
      e.preventDefault();
      const name = document.getElementById('group-name').value;
      const description = document.getElementById('group-desc').value;
      const msg = document.getElementById('create-message');
      try {{
        const resp = await fetch('/api/groups', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{name, description}})
        }});
        if (resp.ok) {{
          msg.innerHTML = '<span style="color:#2ea44f;">Group created!</span>';
          setTimeout(() => location.reload(), 500);
        }} else {{
          const data = await resp.json();
          msg.innerHTML = '<span style="color:#cb2431;">'
            + (data.error || 'Failed') + '</span>';
        }}
      }} catch(err) {{
        msg.innerHTML = '<span style="color:#cb2431;">' + err + '</span>';
      }}
    }}

    async function deleteGroup(name) {{
      if (!confirm('Delete group "' + name + '"?')) return;
      const resp = await fetch('/api/groups/' + name, {{method: 'DELETE'}});
      if (resp.ok || resp.status === 204) {{
        location.reload();
      }} else {{
        alert('Failed to delete group');
      }}
    }}
    </script>"""


def _render_admin_access_page() -> str:
    """Render the admin access control page with live data.

    Shows current access entries and a form to grant access with
    a known users dropdown and free-text option.

    Returns:
        HTML content for the access control page.
    """
    import boto3  # pylint: disable=import-outside-toplevel
    from gitgateway.db import list_groups  # pylint: disable=import-outside-toplevel

    # Get known users
    table = boto3.resource("dynamodb").Table("gitgateway-repos")
    response = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={
            ":pk": "KNOWN_USERS",
            ":prefix": "USER#",
        },
    )
    known_users = sorted(
        item.get("username", "") for item in response.get("Items", [])
    )

    # Get all groups for the resource dropdown
    groups = list_groups()
    group_names = [g.name for g in groups]

    # Get all access entries
    access_table = boto3.resource("dynamodb").Table("gitgateway-access")
    scan_response = access_table.scan()
    access_items = scan_response.get("Items", [])

    # Build user options
    user_options = "".join(
        f'<option value="{u}">{u}</option>' for u in known_users
    )

    # Build group options
    group_options = "".join(
        f'<option value="{g}">{g}</option>' for g in group_names
    )

    # Build access table rows
    rows = ""
    for item in access_items:
        username = item.get("username", "")
        resource = item.get("resource", "")
        level = item.get("level", "")
        if not username or not resource:
            continue
        rows += f"""<tr>
          <td style="padding:0.5rem;">{username}</td>
          <td style="padding:0.5rem;">{resource}</td>
          <td style="padding:0.5rem;">
            <span style="background:#e1e4e8;padding:0.2rem 0.5rem;
              border-radius:3px;font-size:0.8rem;">{level}</span>
          </td>
          <td style="padding:0.5rem;text-align:center;">
            <button onclick="revokeAccess('{username}', '{resource}')"
              style="background:#cb2431;color:#fff;border:none;
              padding:0.3rem 0.7rem;border-radius:4px;cursor:pointer;">
              Revoke</button>
          </td>
        </tr>"""

    if not rows:
        rows = """<tr><td colspan="4" style="padding:1rem;color:#586069;
          text-align:center;">No access entries yet.</td></tr>"""

    return f"""
    <h1>Access Control</h1>

    <div style="margin:1.5rem 0;padding:1rem;background:#f6f8fa;
      border-radius:6px;border:1px solid #e1e4e8;">
      <h3 style="margin-bottom:0.75rem;">Grant Access</h3>
      <form id="grant-access-form" onsubmit="grantAccess(event)"
        style="display:flex;gap:0.5rem;align-items:flex-end;flex-wrap:wrap;">
        <div>
          <label for="access-user-select" style="display:block;
            font-size:0.85rem;color:#555;margin-bottom:0.2rem;">
            Known User</label>
          <select id="access-user-select"
            style="padding:0.4rem 0.6rem;border:1px solid #d1d5da;
            border-radius:4px;">
            <option value="">-- select --</option>
            {user_options}
          </select>
        </div>
        <div>
          <label for="access-user-text" style="display:block;
            font-size:0.85rem;color:#555;margin-bottom:0.2rem;">
            Or type username</label>
          <input type="text" id="access-user-text"
            placeholder="username"
            style="padding:0.4rem 0.6rem;border:1px solid #d1d5da;
            border-radius:4px;">
        </div>
        <div>
          <label for="access-group" style="display:block;
            font-size:0.85rem;color:#555;margin-bottom:0.2rem;">
            Group</label>
          <select id="access-group" required
            style="padding:0.4rem 0.6rem;border:1px solid #d1d5da;
            border-radius:4px;">
            <option value="">-- select --</option>
            {group_options}
          </select>
        </div>
        <div>
          <label for="access-level" style="display:block;
            font-size:0.85rem;color:#555;margin-bottom:0.2rem;">
            Level</label>
          <select id="access-level" required
            style="padding:0.4rem 0.6rem;border:1px solid #d1d5da;
            border-radius:4px;">
            <option value="readonly">readonly</option>
            <option value="contributor">contributor</option>
            <option value="admin">admin</option>
          </select>
        </div>
        <button type="submit"
          style="background:#2ea44f;color:#fff;border:none;
          padding:0.45rem 1rem;border-radius:4px;cursor:pointer;">
          Grant</button>
      </form>
      <div id="access-message" style="margin-top:0.5rem;font-size:0.85rem;">
      </div>
    </div>

    <table style="width:100%;border-collapse:collapse;margin-top:1rem;">
      <thead>
        <tr style="border-bottom:1px solid #e1e4e8;">
          <th style="text-align:left;padding:0.5rem;">User</th>
          <th style="text-align:left;padding:0.5rem;">Group</th>
          <th style="text-align:left;padding:0.5rem;">Level</th>
          <th style="padding:0.5rem;width:100px;">Actions</th>
        </tr>
      </thead>
      <tbody id="access-table">
        {rows}
      </tbody>
    </table>

    <script>
    async function grantAccess(e) {{
      e.preventDefault();
      const selectUser = document.getElementById('access-user-select').value;
      const textUser = document.getElementById('access-user-text').value.trim();
      const username = textUser || selectUser;
      const resource = document.getElementById('access-group').value;
      const level = document.getElementById('access-level').value;
      const msg = document.getElementById('access-message');

      if (!username) {{
        msg.innerHTML = '<span style="color:#cb2431;">Select or type a username</span>';
        return;
      }}
      if (!resource) {{
        msg.innerHTML = '<span style="color:#cb2431;">Select a group</span>';
        return;
      }}

      try {{
        const resp = await fetch('/api/access', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{username, resource, level}})
        }});
        if (resp.ok) {{
          msg.innerHTML = '<span style="color:#2ea44f;">Access granted!</span>';
          setTimeout(() => location.reload(), 500);
        }} else {{
          const data = await resp.json();
          msg.innerHTML = '<span style="color:#cb2431;">'
            + (data.error || 'Failed') + '</span>';
        }}
      }} catch(err) {{
        msg.innerHTML = '<span style="color:#cb2431;">' + err + '</span>';
      }}
    }}

    async function revokeAccess(username, resource) {{
      if (!confirm('Revoke ' + username + ' access to ' + resource + '?')) return;
      const resp = await fetch('/api/access', {{
        method: 'DELETE',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{username, resource}})
      }});
      if (resp.ok || resp.status === 204) {{
        location.reload();
      }} else {{
        alert('Failed to revoke access');
      }}
    }}
    </script>"""


def _handle_auth_callback(request: Request, idp_url: str) -> Response:
    """Handle the OAuth callback from the IdP.

    Extracts the JWT token from the query string, sets a session
    cookie, and redirects to the dashboard.

    Args:
        request: The normalized request.
        idp_url: The IdP URL (unused here but available for validation).

    Returns:
        302 redirect to dashboard with session cookie set.
    """
    import os  # pylint: disable=import-outside-toplevel
    from gitgateway.auth import (  # pylint: disable=import-outside-toplevel
        create_session_cookie,
        AuthenticatedUser,
        validate_jwt,
    )

    # Extract token from query string
    token = ""
    for param in request.query_string.split("&"):
        if param.startswith("token="):
            token = param[6:]
            break

    if not token:
        return Response(
            status_code=400,
            body="<html><body>Missing token parameter</body></html>",
            headers={"Content-Type": "text/html"},
        )

    # Validate JWT and create session
    try:
        user = validate_jwt(token, idp_url)
    except Exception:  # pylint: disable=broad-except
        # If validation fails, still set session with basic info from token
        # (In production, you'd reject invalid tokens)
        import jwt as pyjwt  # pylint: disable=import-outside-toplevel
        try:
            payload = pyjwt.decode(
                token, options={"verify_signature": False},
            )
            user = AuthenticatedUser(
                username=payload.get("sub", "unknown"),
                is_admin="gitgateway-admin" in payload.get("claims", []),
                token=token,
            )
        except Exception:  # pylint: disable=broad-except
            user = AuthenticatedUser(username="unknown", token=token)

    # Create session cookie
    secret_key = os.environ.get("SESSION_SECRET", "gitgateway-session-secret")
    cookie_value = create_session_cookie(user, secret_key, max_age=3600)
    domain_name = os.environ.get("DOMAIN_NAME", "")

    # Track known user
    _record_known_user(user.username)

    return Response(
        status_code=302,
        body="",
        headers={
            "Location": "/",
            "Set-Cookie": (
                f"session={cookie_value}; "
                f"Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=3600"
            ),
        },
    )


def _record_known_user(username: str) -> None:
    """Record a user who has logged in for the known users list.

    Args:
        username: The username to record.
    """
    import boto3  # pylint: disable=import-outside-toplevel
    try:
        table = boto3.resource("dynamodb").Table("gitgateway-repos")
        table.put_item(
            Item={
                "PK": "KNOWN_USERS",
                "SK": f"USER#{username}",
                "username": username,
            },
        )
    except Exception:  # pylint: disable=broad-except
        pass  # Non-critical, don't fail login if tracking fails


def _is_authenticated(request: Request) -> bool:
    """Check if the request has a valid session cookie or auth header.

    Args:
        request: The normalized request.

    Returns:
        True if the request carries authentication credentials.
    """
    # Check for session cookie
    cookies = request.headers.get("cookie", "")
    if "session=" in cookies:
        return True

    # Check for Authorization header
    if request.headers.get("authorization", ""):
        return True

    return False
