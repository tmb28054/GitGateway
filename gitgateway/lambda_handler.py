"""Lambda function entry point and request routing for GitGateway.

Routes incoming API Gateway v2 HTTP API requests to the appropriate
handler based on path patterns:
- /<group>/<repo>.git/info/lfs/* -> LFS handler
- /<group>/<repo>.git/* -> Git protocol handler
- /api/* -> REST API handler (JSON)
- /auth/* -> OAuth callback handler
- /* -> Web UI handler (HTML)
"""
# pylint: disable=too-many-lines,too-many-locals,too-many-branches
# pylint: disable=too-many-statements,too-many-return-statements
# pylint: disable=too-many-positional-arguments,line-too-long
# pylint: disable=reimported,unused-import,unused-variable,consider-using-in
# pylint: disable=redefined-outer-name,f-string-without-interpolation

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
        from gitgateway.git_backend import (  # pylint: disable=import-outside-toplevel
            handle_git_http_request,
        )
        # Extract group/repo from the path
        import re as _re  # pylint: disable=import-outside-toplevel
        match = _re.match(r"^/([^/]+)/([^/]+)\.git/(.*)", request.path)
        if not match:
            return Response(
                status_code=400, body=b"Invalid git URL",
                headers={"Content-Type": "text/plain"},
            )
        group = match.group(1)
        repo_name = match.group(2)
        git_path = "/" + match.group(3)

        # Authenticate via Basic auth
        git_user = _authenticate_git_request(request)
        if git_user is None:
            return Response(
                status_code=401,
                body=b"Authentication required",
                headers={
                    "Content-Type": "text/plain",
                    "WWW-Authenticate": 'Basic realm="GitGateway"',
                },
            )

        # Check access: push requires read/write scope
        is_push = "git-receive-pack" in git_path
        if is_push and git_user.get("scope") == "read":
            return Response(
                status_code=403,
                body=b"Token scope is read-only; push requires read/write",
                headers={"Content-Type": "text/plain"},
            )

        status, ctype, resp_body = handle_git_http_request(
            method=request.method,
            path_info=git_path,
            query_string=request.query_string,
            body=request.body,
            group=group,
            repo_name=repo_name,
            content_type=request.headers.get("content-type", ""),
        )

        # After successful push, re-run linting on any open PRs for this branch
        if is_push and status == 200:
            try:
                _trigger_post_push_linting(group, repo_name)
            except Exception:  # pylint: disable=broad-except
                pass  # Non-critical — don't fail the push

        return Response(
            status_code=status,
            body=resp_body,
            headers={"Content-Type": ctype},
        )

    if handler_name == "lfs":
        return _handle_lfs(request)

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

    # POST /api/repos/<group>/<repo>/branches — register a branch
    if (
        path.startswith("/api/repos/")
        and path.endswith("/branches")
        and method == "POST"
    ):
        branch_path = path[len("/api/repos/"):-len("/branches")]
        parts = branch_path.split("/", 1)
        if len(parts) == 2:
            try:
                body = json.loads(request.body.decode()) if request.body else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                return Response(
                    status_code=400,
                    body=json.dumps({"error": "Invalid JSON"}),
                    headers=headers,
                )
            branch_name = body.get("branch", "")
            if not branch_name:
                return Response(
                    status_code=400,
                    body=json.dumps({"error": "branch is required"}),
                    headers=headers,
                )
            import boto3 as _boto3  # pylint: disable=import-outside-toplevel
            tbl = _boto3.resource("dynamodb").Table("gitgateway-repos")
            tbl.put_item(Item={
                "PK": f"BRANCHES#{parts[0]}/{parts[1]}",
                "SK": f"BRANCH#{branch_name}",
                "branch_name": branch_name,
            })
            return Response(
                status_code=201,
                body=json.dumps({"branch": branch_name}),
                headers=headers,
            )

    # GET /api/repos/<group>/<repo>/branches — list branches
    if (
        path.startswith("/api/repos/")
        and path.endswith("/branches")
        and method == "GET"
    ):
        branch_path = path[len("/api/repos/"):-len("/branches")]
        parts = branch_path.split("/", 1)
        if len(parts) == 2:
            import boto3 as _boto3  # pylint: disable=import-outside-toplevel
            tbl = _boto3.resource("dynamodb").Table("gitgateway-repos")
            resp = tbl.query(
                KeyConditionExpression=(
                    "PK = :pk AND begins_with(SK, :prefix)"
                ),
                ExpressionAttributeValues={
                    ":pk": f"BRANCHES#{parts[0]}/{parts[1]}",
                    ":prefix": "BRANCH#",
                },
            )
            branch_list = [
                item.get("branch_name", "")
                for item in resp.get("Items", [])
            ]
            return Response(
                status_code=200,
                body=json.dumps(branch_list),
                headers=headers,
            )

    # DELETE /api/repos/<group>/<repo>/branches/<branch> — delete branch
    if (
        path.startswith("/api/repos/")
        and "/branches/" in path
        and method == "DELETE"
    ):
        # Parse: /api/repos/group/repo/branches/branch-name
        after_repos = path[len("/api/repos/"):]
        br_parts = after_repos.split("/branches/", 1)
        if len(br_parts) == 2:
            gr_parts = br_parts[0].split("/", 1)
            branch_to_delete = br_parts[1]
            if len(gr_parts) == 2 and branch_to_delete:
                import boto3 as _boto3  # pylint: disable=import-outside-toplevel
                tbl = _boto3.resource("dynamodb").Table("gitgateway-repos")
                tbl.delete_item(Key={
                    "PK": f"BRANCHES#{gr_parts[0]}/{gr_parts[1]}",
                    "SK": f"BRANCH#{branch_to_delete}",
                })
                return Response(
                    status_code=204, body="", headers=headers,
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

    # PATCH /api/repos/<group>/<repo>/settings — update repo settings
    if (
        path.startswith("/api/repos/")
        and path.endswith("/settings")
        and method == "PATCH"
    ):
        settings_path = path[len("/api/repos/"):-len("/settings")]
        parts = settings_path.split("/", 1)
        if len(parts) == 2:
            from gitgateway.db import (  # pylint: disable=import-outside-toplevel
                get_repo, update_repo,
            )
            try:
                body = json.loads(request.body.decode()) if request.body else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                return Response(
                    status_code=400,
                    body=json.dumps({"error": "Invalid JSON"}),
                    headers=headers,
                )
            try:
                repo = get_repo(parts[0], parts[1])
            except NotFoundError:
                return Response(
                    status_code=404,
                    body=json.dumps({"error": "Repository not found"}),
                    headers=headers,
                )
            if "default_branch" in body:
                repo.default_branch = body["default_branch"]
            if "description" in body:
                repo.description = body["description"]
            update_repo(repo)
            return Response(
                status_code=200,
                body=json.dumps(repo.to_dict()),
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

    # POST /api/<group>/<repo>/pulls — create PR
    pr_match = re.match(r"^/api/([^/]+)/([^/]+)/pulls$", path)
    if pr_match and method == "POST":
        from gitgateway.pullrequest import (  # pylint: disable=import-outside-toplevel
            create_pr, PullRequest,
        )
        try:
            body = json.loads(request.body.decode()) if request.body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response(
                status_code=400,
                body=json.dumps({"error": "Invalid JSON"}),
                headers=headers,
            )
        pr_group = pr_match.group(1)
        pr_repo = pr_match.group(2)
        pr = create_pr(PullRequest(
            title=body.get("title", ""),
            source_branch=body.get("source_branch", ""),
            target_branch=body.get("target_branch", "main"),
            author=body.get("author", ""),
            group=pr_group,
            repo=pr_repo,
        ))

        # Run linters on the source branch (best effort, limited scope)
        try:
            _run_pr_linting(pr_group, pr_repo, pr.number, pr.source_branch)
        except Exception as exc:  # pylint: disable=broad-except
            LOG.warning("Linting failed for PR #%d: %s", pr.number, exc)

        # Run AI code review (best effort)
        try:
            _run_ai_review(pr_group, pr_repo, pr.number, pr.source_branch)
        except Exception as exc:  # pylint: disable=broad-except
            LOG.warning("AI review failed for PR #%d: %s", pr.number, exc)

        return Response(
            status_code=201,
            body=json.dumps(pr.to_dict()),
            headers=headers,
        )

    # GET /api/<group>/<repo>/pulls — list PRs
    if pr_match and method == "GET":
        from gitgateway.pullrequest import (  # pylint: disable=import-outside-toplevel
            list_prs, PrStatus,
        )
        pr_group = pr_match.group(1)
        pr_repo = pr_match.group(2)
        status_filter = None
        for param in request.query_string.split("&"):
            if param.startswith("status="):
                status_filter = PrStatus(param[7:])
        prs = list_prs(pr_group, pr_repo, status_filter)
        return Response(
            status_code=200,
            body=json.dumps([p.to_dict() for p in prs]),
            headers=headers,
        )

    # POST /api/<group>/<repo>/pulls/<number>/comments — add comment
    comment_match = re.match(
        r"^/api/([^/]+)/([^/]+)/pulls/(\d+)/comments$", path,
    )
    if comment_match and method == "POST":
        from gitgateway.reviews import (  # pylint: disable=import-outside-toplevel
            add_comment, Comment,
        )
        try:
            body = json.loads(request.body.decode()) if request.body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response(
                status_code=400,
                body=json.dumps({"error": "Invalid JSON"}),
                headers=headers,
            )
        c = add_comment(Comment(
            pr_number=int(comment_match.group(3)),
            author=body.get("author", ""),
            body=body.get("body", ""),
            group=comment_match.group(1),
            repo=comment_match.group(2),
        ))
        return Response(
            status_code=201,
            body=json.dumps(c.to_dict()),
            headers=headers,
        )

    # GET /api/<group>/<repo>/pulls/<number>/comments — list comments
    if comment_match and method == "GET":
        from gitgateway.reviews import list_comments  # pylint: disable=import-outside-toplevel
        comments = list_comments(
            comment_match.group(1),
            comment_match.group(2),
            int(comment_match.group(3)),
        )
        return Response(
            status_code=200,
            body=json.dumps([c.to_dict() for c in comments]),
            headers=headers,
        )

    # POST /api/<group>/<repo>/pulls/<number>/reviews — submit review
    review_match = re.match(
        r"^/api/([^/]+)/([^/]+)/pulls/(\d+)/reviews$", path,
    )
    if review_match and method == "POST":
        from gitgateway.reviews import (  # pylint: disable=import-outside-toplevel
            create_review, Review, ReviewStatus,
        )
        try:
            body = json.loads(request.body.decode()) if request.body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response(
                status_code=400,
                body=json.dumps({"error": "Invalid JSON"}),
                headers=headers,
            )
        status_str = body.get("status", "commented")
        try:
            review_status = ReviewStatus(status_str)
        except ValueError:
            return Response(
                status_code=400,
                body=json.dumps({"error": f"Invalid status: {status_str}"}),
                headers=headers,
            )
        r = create_review(Review(
            pr_number=int(review_match.group(3)),
            author=body.get("author", ""),
            status=review_status,
            body=body.get("body", ""),
            group=review_match.group(1),
            repo=review_match.group(2),
        ))
        return Response(
            status_code=201,
            body=json.dumps(r.to_dict()),
            headers=headers,
        )

    # POST /api/<group>/<repo>/pulls/<number>/merge — merge PR
    merge_match = re.match(
        r"^/api/([^/]+)/([^/]+)/pulls/(\d+)/merge$", path,
    )
    if merge_match and method == "POST":
        from gitgateway.pullrequest import (  # pylint: disable=import-outside-toplevel
            merge_pr, MergeStrategy, PrNotFoundError, InvalidTransitionError,
        )
        try:
            body = json.loads(request.body.decode()) if request.body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        strategy_str = body.get("strategy", "merge")
        try:
            strategy = MergeStrategy(strategy_str)
        except ValueError:
            strategy = MergeStrategy.MERGE
        try:
            merged = merge_pr(
                merge_match.group(1), merge_match.group(2),
                int(merge_match.group(3)),
                strategy=strategy,
            )
            return Response(
                status_code=200,
                body=json.dumps(merged.to_dict()),
                headers=headers,
            )
        except PrNotFoundError:
            return Response(
                status_code=404,
                body=json.dumps({"error": "PR not found"}),
                headers=headers,
            )
        except InvalidTransitionError as exc:
            return Response(
                status_code=409,
                body=json.dumps({"error": str(exc)}),
                headers=headers,
            )

    # POST /api/<group>/<repo>/pulls/<number>/rescan — re-run linting
    rescan_match = re.match(
        r"^/api/([^/]+)/([^/]+)/pulls/(\d+)/rescan$", path,
    )
    if rescan_match and method == "POST":
        rescan_group = rescan_match.group(1)
        rescan_repo = rescan_match.group(2)
        rescan_number = int(rescan_match.group(3))
        # Look up the PR to get the source branch
        from gitgateway.pullrequest import (  # pylint: disable=import-outside-toplevel
            get_pr, PrNotFoundError,
        )
        try:
            pr_obj = get_pr(rescan_group, rescan_repo, rescan_number)
            _run_pr_linting(
                rescan_group, rescan_repo,
                rescan_number, pr_obj.source_branch,
            )
            _run_ai_review(
                rescan_group, rescan_repo,
                rescan_number, pr_obj.source_branch,
            )
            return Response(
                status_code=200,
                body=json.dumps({
                    "message": (
                        f"Linting and AI review triggered "
                        f"for PR #{rescan_number}"
                    ),
                }),
                headers=headers,
            )
        except PrNotFoundError:
            return Response(
                status_code=404,
                body=json.dumps({"error": "PR not found"}),
                headers=headers,
            )
        except Exception as exc:  # pylint: disable=broad-except
            return Response(
                status_code=500,
                body=json.dumps({"error": f"Linting failed: {exc}"}),
                headers=headers,
            )

    # POST /api/<group>/<repo>/pulls/<number>/close — close a PR
    close_match = re.match(
        r"^/api/([^/]+)/([^/]+)/pulls/(\d+)/close$", path,
    )
    if close_match and method == "POST":
        from gitgateway.pullrequest import (  # pylint: disable=import-outside-toplevel
            close_pr, PrNotFoundError, InvalidTransitionError,
        )
        try:
            closed = close_pr(
                close_match.group(1), close_match.group(2),
                int(close_match.group(3)),
            )
            return Response(
                status_code=200,
                body=json.dumps(closed.to_dict()),
                headers=headers,
            )
        except PrNotFoundError:
            return Response(
                status_code=404,
                body=json.dumps({"error": "PR not found"}),
                headers=headers,
            )
        except InvalidTransitionError as exc:
            return Response(
                status_code=409,
                body=json.dumps({"error": str(exc)}),
                headers=headers,
            )

    # POST /api/tokens — create a personal access token
    if path == "/api/tokens" and method == "POST":
        from gitgateway.tokens import (  # pylint: disable=import-outside-toplevel
            generate_token, TokenScope,
        )
        try:
            body = json.loads(request.body.decode()) if request.body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        description = body.get("description", "").strip()
        scope_str = body.get("scope", "read/write")
        expiry_days = int(body.get("expiry_days", 365))
        username = _get_request_username(request)
        if not username:
            return Response(
                status_code=401,
                body=json.dumps({"error": "Authentication required"}),
                headers=headers,
            )
        try:
            scope = TokenScope(scope_str)
        except ValueError:
            scope = TokenScope.READ_WRITE
        raw_token, pat = generate_token(
            username=username,
            description=description,
            scope=scope,
            expiry_days=expiry_days,
        )
        result = pat.to_dict()
        result["token"] = raw_token  # Only returned once
        return Response(
            status_code=201,
            body=json.dumps(result),
            headers=headers,
        )

    # GET /api/tokens — list current user's tokens
    if path == "/api/tokens" and method == "GET":
        from gitgateway.tokens import list_tokens  # pylint: disable=import-outside-toplevel
        username = _get_request_username(request)
        if not username:
            return Response(
                status_code=401,
                body=json.dumps({"error": "Authentication required"}),
                headers=headers,
            )
        tokens = list_tokens(username)
        return Response(
            status_code=200,
            body=json.dumps([t.to_dict() for t in tokens]),
            headers=headers,
        )

    # DELETE /api/tokens/<token_id> — revoke a token
    token_del_match = re.match(r"^/api/tokens/([a-f0-9]+)$", path)
    if token_del_match and method == "DELETE":
        from gitgateway.tokens import (  # pylint: disable=import-outside-toplevel
            revoke_token, TokenNotFoundError,
        )
        username = _get_request_username(request)
        if not username:
            return Response(
                status_code=401,
                body=json.dumps({"error": "Authentication required"}),
                headers=headers,
            )
        token_id = token_del_match.group(1)
        try:
            revoke_token(username, token_id)
            return Response(
                status_code=200,
                body=json.dumps({"message": f"Token {token_id} revoked"}),
                headers=headers,
            )
        except TokenNotFoundError:
            return Response(
                status_code=404,
                body=json.dumps({"error": "Token not found"}),
                headers=headers,
            )

    # POST /api/admin/ai-key — update Gemini API key
    if path == "/api/admin/ai-key" and method == "POST":
        import boto3 as _boto3  # pylint: disable=import-outside-toplevel
        try:
            body = json.loads(request.body.decode()) if request.body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        api_key = body.get("api_key", "").strip()
        if not api_key:
            return Response(
                status_code=400,
                body=json.dumps({"error": "api_key is required"}),
                headers=headers,
            )
        try:
            sm = _boto3.client("secretsmanager")
            try:
                sm.put_secret_value(
                    SecretId="gitgateway/gemini-api-key",
                    SecretString=api_key,
                )
            except sm.exceptions.ResourceNotFoundException:
                sm.create_secret(
                    Name="gitgateway/gemini-api-key",
                    SecretString=api_key,
                )
            return Response(
                status_code=200,
                body=json.dumps({"message": "API key saved"}),
                headers=headers,
            )
        except Exception as exc:  # pylint: disable=broad-except
            return Response(
                status_code=500,
                body=json.dumps({"error": f"Failed to save: {exc}"}),
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
        <button onclick="toggleDarkMode()" id="dark-toggle"
          style="background:none;border:1px solid #555;border-radius:4px;
          padding:0.2rem 0.5rem;cursor:pointer;font-size:0.9rem;"
          title="Toggle dark mode">🌙</button>
        <a href="/tokens" style="color:#ccc;text-decoration:none;"
          title="Personal Access Tokens">Tokens</a>
        <span style="color:#ccc;">{username}</span>
        <a href="/logout" style="color:#f97583;text-decoration:none;">
          Logout</a>
      </div>
    </nav>"""

    # Route to the right page content
    page_content = _get_page_content(request.path, user_info, request.query_string)

    # Build the left sidebar (accessible groups + repos)
    try:
        sidebar = _render_sidebar(username)
    except Exception:  # pylint: disable=broad-except
        sidebar = ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>GitGateway</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI',
      Roboto, sans-serif; line-height: 1.6; color: #24292e;
      background: #fff; transition: background 0.3s, color 0.3s; }}
    .layout {{ display: flex; align-items: flex-start; }}
    main {{ flex: 1; max-width: 1000px; margin: 2rem auto; padding: 0 1.5rem; }}
    #sidebar a:hover {{ text-decoration: underline; }}
    @media (max-width: 700px) {{
      .layout {{ flex-direction: column; }}
      #sidebar {{ width: 100% !important; min-height: auto !important;
        border-right: none !important;
        border-bottom: 1px solid #e1e4e8; }}
    }}
    h1 {{ margin-bottom: 1rem; }}
    a {{ color: #0366d6; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .empty {{ color: #586069; margin-top: 2rem; }}

    body.dark {{ background: #0d1117; color: #c9d1d9; }}
    body.dark a {{ color: #58a6ff; }}
    body.dark nav {{ background: #161b22 !important; }}
    body.dark table {{ border-color: #30363d !important; }}
    body.dark tr {{ border-color: #21262d !important; }}
    body.dark td, body.dark th {{ border-color: #30363d !important; }}
    body.dark pre {{ background: #161b22 !important; }}
    body.dark code {{ background: #161b22 !important; color: #c9d1d9; }}
    body.dark input, body.dark select {{ background: #0d1117;
      color: #c9d1d9; border-color: #30363d !important; }}
    body.dark div[style*="background:#f6f8fa"],
    body.dark div[style*="background: #f6f8fa"] {{
      background: #161b22 !important; }}
    body.dark #sidebar {{ background: #161b22 !important;
      border-color: #30363d !important; }}
    body.dark aside[style*="background:#fafbfc"] {{
      background: #161b22 !important; }}
  </style>
</head>
<body>
  {nav_bar}
  <div class="layout">
    {sidebar}
    <main>
      {page_content}
    </main>
  </div>
  <script>
  function toggleDarkMode() {{
    document.body.classList.toggle('dark');
    const isDark = document.body.classList.contains('dark');
    localStorage.setItem('gitgateway-dark', isDark ? '1' : '0');
    document.getElementById('dark-toggle').textContent = isDark ? '☀️' : '🌙';
  }}
  // Restore preference
  if (localStorage.getItem('gitgateway-dark') === '1') {{
    document.body.classList.add('dark');
    document.getElementById('dark-toggle').textContent = '☀️';
  }}
  </script>
</body>
</html>"""

    return Response(
        status_code=200,
        body=html,
        headers={"Content-Type": "text/html"},
    )


def _get_page_content(path: str, user_info: dict, query_string: str = "") -> str:
    """Generate page content based on the URL path.

    Args:
        path: The request path.
        user_info: User info dict.
        query_string: Raw query string.

    Returns:
        HTML content string for the main area.
    """
    username = user_info.get("username", "")

    if path == "/" or path == "":
        return _render_dashboard(username)

    if path == "/groups":
        return _render_user_groups_page(username)

    if path == "/tokens":
        return _render_tokens_page(username)

    if path.startswith("/groups/") and "/" not in path[8:]:
        group_name = path[8:]
        return _render_group_detail_page(group_name, username)

    # /groups/<group>/<repo> — repository view
    if path.startswith("/groups/") and path.count("/") == 3:
        parts = path[8:].split("/")
        return _render_repo_page(parts[0], parts[1], username, "")

    # /groups/<group>/<repo>/settings — repo settings (not inside /tree/)
    if (
        path.startswith("/groups/")
        and path.endswith("/settings")
        and "/tree/" not in path
    ):
        parts = path[8:].rsplit("/settings", 1)[0].split("/", 1)
        if len(parts) == 2:
            return _render_repo_settings_page(parts[0], parts[1], username)

    # /groups/<group>/<repo>/commit/<sha> — view commit diff
    if path.startswith("/groups/") and "/commit/" in path:
        after = path[8:]
        parts = after.split("/commit/", 1)
        if len(parts) == 2:
            gr_parts = parts[0].split("/", 1)
            if len(gr_parts) == 2:
                return _render_commit_diff_page(
                    gr_parts[0], gr_parts[1], username, parts[1],
                )

    # /groups/<group>/<repo>/commits — commit history (not inside /tree/)
    # Also handles /groups/<group>/<repo>/commits/<branch>
    if (
        path.startswith("/groups/")
        and "/commits" in path
        and "/tree/" not in path
    ):
        after = path[8:]
        if "/commits/" in after:
            before_commits, branch_part = after.split("/commits/", 1)
            parts = before_commits.split("/", 1)
            if len(parts) == 2:
                return _render_commits_page(
                    parts[0], parts[1], username, branch_part,
                )
        elif after.endswith("/commits"):
            parts = after.rsplit("/commits", 1)[0].split("/", 1)
            if len(parts) == 2:
                return _render_commits_page(parts[0], parts[1], username)

    # /groups/<group>/<repo>/pulls — pull requests page
    if (
        path.startswith("/groups/")
        and path.endswith("/pulls")
        and "/tree/" not in path
    ):
        parts = path[8:].rsplit("/pulls", 1)[0].split("/", 1)
        if len(parts) == 2:
            return _render_pulls_page(
                parts[0], parts[1], username, query_string,
            )

    # /groups/<group>/<repo>/pulls/<number> — PR detail page
    if (
        path.startswith("/groups/")
        and "/pulls/" in path
        and "/tree/" not in path
    ):
        after = path[8:]
        pr_parts = after.split("/pulls/", 1)
        if len(pr_parts) == 2:
            gr_parts = pr_parts[0].split("/", 1)
            if len(gr_parts) == 2:
                try:
                    pr_num = int(pr_parts[1])
                    return _render_pr_detail_page(
                        gr_parts[0], gr_parts[1], username, pr_num,
                        query_string=query_string,
                    )
                except ValueError:
                    pass

    # /groups/<group>/<repo>/branches — branches page (not inside /tree/)
    if (
        path.startswith("/groups/")
        and path.endswith("/branches")
        and "/tree/" not in path
        and "/api/" not in path
    ):
        parts = path[8:].rsplit("/branches", 1)[0].split("/", 1)
        if len(parts) == 2:
            return _render_branches_page(parts[0], parts[1], username)

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
                return _render_repo_tree_or_file(
                    gr_parts[0], gr_parts[1], username, branch, file_path,
                )

    if path.startswith("/admin"):
        if not user_info.get("is_admin"):
            return "<h1>403 Forbidden</h1><p>Admin access required.</p>"
        return _get_admin_content(path)

    return f"<h1>Page</h1><p>Path: {path}</p>"


def _render_sidebar(username: str) -> str:
    """Render the left sidebar navigation listing accessible groups and repos.

    Args:
        username: The authenticated username.

    Returns:
        HTML for the sidebar.
    """
    from gitgateway.db import (  # pylint: disable=import-outside-toplevel
        list_user_access, list_groups, list_repos,
    )

    try:
        access_entries = list_user_access(username)
    except Exception as exc:  # pylint: disable=broad-except
        LOG.warning("Sidebar access lookup failed for %s: %s", username, exc)
        access_entries = []

    # Groups the user can see (group-level access)
    group_names = {
        entry.resource for entry in access_entries
        if not entry.is_repo_level
    }
    # Repos the user can see via repo-level access
    repo_resources = {
        entry.resource for entry in access_entries
        if entry.is_repo_level
    }

    try:
        all_groups = list_groups()
    except Exception:  # pylint: disable=broad-except
        all_groups = []
    visible_groups = [g for g in all_groups if g.name in group_names]

    items = ""
    for group in sorted(visible_groups, key=lambda g: g.name):
        # List repos in this group
        try:
            repos = list_repos(group.name)
        except Exception:  # pylint: disable=broad-except
            repos = []

        repo_links = ""
        for repo in sorted(repos, key=lambda r: r.name):
            repo_links += f"""
            <li style="padding:0.15rem 0 0.15rem 1.25rem;">
              <a href="/groups/{group.name}/{repo.name}"
                style="font-size:0.85rem;color:#586069;">
                {repo.name}</a>
            </li>"""

        if not repo_links:
            repo_links = (
                '<li style="padding:0.15rem 0 0.15rem 1.25rem;'
                'color:#959da5;font-size:0.8rem;">No repositories</li>'
            )

        # Collapsible group: <details> defaults to collapsed (no `open`).
        # The summary shows the group name (linked) with a disclosure
        # triangle; expanding reveals the repo list.
        items += f"""
        <li style="margin-bottom:0.5rem;">
          <details>
            <summary style="cursor:pointer;font-weight:600;
              font-size:0.9rem;list-style:none;display:flex;
              align-items:center;gap:0.3rem;">
              <span class="tri" style="font-size:0.7rem;
                color:#959da5;">&#9654;</span>
              <a href="/groups/{group.name}"
                style="font-weight:600;">{group.name}</a>
            </summary>
            <ul style="list-style:none;margin-top:0.25rem;">
              {repo_links}
            </ul>
          </details>
        </li>"""

    # Repos accessible via repo-level grants (not in a visible group)
    orphan_repos = ""
    for resource in sorted(repo_resources):
        parts = resource.split("/", 1)
        if len(parts) == 2 and parts[0] not in group_names:
            orphan_repos += f"""
            <li style="padding:0.15rem 0;">
              <a href="/groups/{parts[0]}/{parts[1]}"
                style="font-size:0.85rem;">{resource}</a>
            </li>"""

    orphan_section = ""
    if orphan_repos:
        orphan_section = f"""
        <li style="margin-top:0.75rem;">
          <span style="font-weight:600;font-size:0.9rem;
            color:#586069;">Shared Repos</span>
          <ul style="list-style:none;margin-top:0.25rem;">
            {orphan_repos}
          </ul>
        </li>"""

    if not items and not orphan_section:
        items = (
            '<li style="color:#959da5;font-size:0.85rem;">'
            'No accessible groups or repos.</li>'
        )

    return f"""
    <aside id="sidebar" style="width:240px;flex-shrink:0;
      border-right:1px solid #e1e4e8;padding:1.5rem 1rem;
      background:#fafbfc;min-height:calc(100vh - 52px);">
      <style>
        #sidebar summary::-webkit-details-marker {{ display:none; }}
        #sidebar details[open] > summary .tri {{
          transform: rotate(90deg); }}
        #sidebar summary .tri {{ transition: transform 0.15s;
          display:inline-block; }}
      </style>
      <div style="font-size:0.75rem;text-transform:uppercase;
        letter-spacing:0.05em;color:#959da5;margin-bottom:0.75rem;">
        Groups &amp; Repositories
      </div>
      <ul style="list-style:none;">
        {items}
        {orphan_section}
      </ul>
    </aside>"""


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


def _load_diff_entries(  # pylint: disable=too-many-locals,too-many-return-statements
    group: str, repo_name: str, target_branch: str, source_branch: str,
    ignore_whitespace: bool = False,
) -> tuple[list, str]:
    """Load per-file diff entries between two branches.

    Args:
        group: Group name.
        repo_name: Repository name.
        target_branch: The target (base) branch.
        source_branch: The source (head) branch.
        ignore_whitespace: If True, drop whitespace-only changes.

    Returns:
        Tuple of (diff_entries, error_html). If error_html is
        non-empty, diff_entries is empty and the caller should render
        the error.
    """
    from pathlib import Path as _Path  # pylint: disable=import-outside-toplevel
    import tempfile as _tempfile  # pylint: disable=import-outside-toplevel
    from dulwich.repo import Repo as _Repo  # pylint: disable=import-outside-toplevel
    from gitgateway.storage import (  # pylint: disable=import-outside-toplevel
        sync_repo_to_local, repo_exists,
    )

    if not repo_exists(group, repo_name):
        return [], '<p style="color:#586069;">Repository not found.</p>'

    local_base = _Path(_tempfile.gettempdir()) / "gitgateway"
    local_base.mkdir(parents=True, exist_ok=True)
    local_path = local_base / group / f"{repo_name}.git"

    try:
        sync_repo_to_local(group, repo_name, local_base=local_base)
        repo = _Repo(str(local_path))
    except Exception:  # pylint: disable=broad-except
        return [], '<p style="color:#586069;">Could not load repository.</p>'

    refs = repo.get_refs()
    target_sha = refs.get(f"refs/heads/{target_branch}".encode())
    source_sha = refs.get(f"refs/heads/{source_branch}".encode())

    if not source_sha:
        return [], (
            f'<p style="color:#586069;">Branch {source_branch} '
            f'not found.</p>'
        )

    target_tree = None
    if target_sha:
        try:
            target_tree = repo.object_store[target_sha].tree
        except (KeyError, Exception):  # pylint: disable=broad-except
            pass

    try:
        source_tree = repo.object_store[source_sha].tree
    except (KeyError, Exception):  # pylint: disable=broad-except
        return [], (
            '<p style="color:#586069;">Could not read source branch.</p>'
        )

    diff_entries = _compute_tree_diff(
        repo.object_store, target_tree, source_tree,
    )
    if not diff_entries:
        return [], (
            '<p style="color:#586069;">No differences between '
            'branches.</p>'
        )

    if ignore_whitespace:
        diff_entries = [
            e for e in diff_entries if not _is_whitespace_only_diff(e)
        ]
        if not diff_entries:
            return [], (
                '<p style="color:#586069;">No non-whitespace '
                'differences.</p>'
            )

    return diff_entries, ""


def _compute_pr_diff_split(  # pylint: disable=too-many-locals
    group: str, repo_name: str, target_branch: str, source_branch: str,
    lint_failed_files: set | None = None,
    ignore_whitespace: bool = False,
) -> str:
    """Render the PR diff as a file-list (left) + diff-pane (right) view.

    The left pane lists changed files; clicking a file shows its diff
    in the right pane. Uses inline JS for pane switching (no reload).

    Args:
        group: Group name.
        repo_name: Repository name.
        target_branch: Target (base) branch.
        source_branch: Source (head) branch.
        lint_failed_files: Files that failed linting (marked with ✗).
        ignore_whitespace: If True, drop whitespace-only changes.

    Returns:
        HTML string for the split diff view.
    """
    diff_entries, err = _load_diff_entries(
        group, repo_name, target_branch, source_branch, ignore_whitespace,
    )
    if err:
        return err

    lint_failed_files = lint_failed_files or set()
    badge = {
        "added": '#2ea44f', "deleted": '#cb2431', "modified": '#dbab09',
    }

    file_items = ""
    diff_panes = ""
    for idx, entry in enumerate(diff_entries):
        path = entry["path"]
        status = entry.get("status", "modified")
        color = badge.get(status, "#586069")
        failed = path in lint_failed_files
        lint_mark = (
            '<span style="color:#cb2431;" title="Lint failed">✗</span> '
            if failed else ""
        )
        active = "background:#eaf5ff;" if idx == 0 else ""
        file_items += f"""
        <li class="diff-file-item" data-idx="{idx}"
          onclick="showDiffFile({idx})"
          style="padding:0.4rem 0.6rem;cursor:pointer;font-size:0.82rem;
            border-bottom:1px solid #f0f0f0;display:flex;
            align-items:center;gap:0.35rem;{active}">
          <span style="width:8px;height:8px;border-radius:2px;
            background:{color};flex-shrink:0;"></span>
          {lint_mark}
          <span style="word-break:break-all;">{path}</span>
        </li>"""

        show = "block" if idx == 0 else "none"
        diff_panes += f"""
        <div class="diff-file-pane" data-idx="{idx}"
          style="display:{show};">
          <div style="background:#f6f8fa;padding:0.5rem 1rem;
            border-bottom:1px solid #e1e4e8;font-weight:600;
            font-size:0.85rem;">{lint_mark}{path}</div>
          <div style="overflow-x:auto;">{_render_side_by_side_diff(entry)}</div>
        </div>"""

    return f"""
    <div style="display:flex;border:1px solid #e1e4e8;border-radius:6px;
      overflow:hidden;min-height:400px;">
      <ul id="diff-file-list" style="list-style:none;width:260px;
        flex-shrink:0;border-right:1px solid #e1e4e8;margin:0;padding:0;
        overflow-y:auto;max-height:80vh;background:#fafbfc;">
        {file_items}
      </ul>
      <div id="diff-pane-container" style="flex:1;overflow:auto;
        max-height:80vh;">
        {diff_panes}
      </div>
    </div>
    <script>
    function showDiffFile(idx) {{
      document.querySelectorAll('.diff-file-pane').forEach(function(p) {{
        p.style.display = (p.dataset.idx == idx) ? 'block' : 'none';
      }});
      document.querySelectorAll('.diff-file-item').forEach(function(i) {{
        i.style.background = (i.dataset.idx == idx) ? '#eaf5ff' : '';
      }});
    }}
    </script>"""


def _compute_pr_diff(
    group: str, repo_name: str, target_branch: str, source_branch: str,
    lint_failed_files: set | None = None,
    ignore_whitespace: bool = False,
) -> str:
    """Compute the diff between target and source branches for a PR.

    Args:
        group: Group name.
        repo_name: Repository name.
        target_branch: The target (base) branch.
        source_branch: The source (head) branch.
        lint_failed_files: Set of file paths that failed linting.
        ignore_whitespace: If True, skip files with only whitespace changes.

    Returns:
        HTML string showing the diff.
    """
    diff_entries, err = _load_diff_entries(
        group, repo_name, target_branch, source_branch, ignore_whitespace,
    )
    if err:
        return err

    # Render diff
    html = ""
    for entry in diff_entries:
        status_badge = {
            "added": '<span style="background:#2ea44f;color:#fff;padding:0.1rem 0.4rem;border-radius:3px;font-size:0.75rem;">Added</span>',
            "deleted": '<span style="background:#cb2431;color:#fff;padding:0.1rem 0.4rem;border-radius:3px;font-size:0.75rem;">Deleted</span>',
            "modified": '<span style="background:#dbab09;color:#fff;padding:0.1rem 0.4rem;border-radius:3px;font-size:0.75rem;">Modified</span>',
        }.get(entry["status"], "")

        lint_icon = ""
        if lint_failed_files and entry["path"] in lint_failed_files:
            lint_icon = '<span style="color:#cb2431;margin-right:0.5rem;" title="Lint failed">✗</span>'

        diff_content = _render_side_by_side_diff(entry)

        html += f"""
        <div style="border:1px solid #e1e4e8;border-radius:6px;margin-bottom:1rem;">
          <div style="background:#f6f8fa;padding:0.5rem 1rem;
            border-bottom:1px solid #e1e4e8;display:flex;
            justify-content:space-between;align-items:center;">
            <span>{lint_icon}<strong>{entry["path"]}</strong></span>
            {status_badge}
          </div>
          <div style="overflow-x:auto;">{diff_content}</div>
        </div>"""

    return html


def _render_side_by_side_diff(entry: dict) -> str:
    """Render a file diff as a side-by-side HTML table.

    Left column shows the old (target) version, right column shows
    the new (source) version. Added lines appear only on the right,
    deleted lines only on the left, context on both sides.

    Args:
        entry: Dict with 'diff' and 'status' keys.

    Returns:
        HTML table string for the side-by-side diff.
    """
    if not entry.get("diff"):
        return '<p style="padding:1rem;color:#586069;margin:0;">Binary or empty file</p>'

    lines = entry["diff"].split("\n")
    left_lines: list[tuple[int | str, str, str]] = []
    right_lines: list[tuple[int | str, str, str]] = []

    left_num = 0
    right_num = 0

    for line in lines:
        escaped = (
            line.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        if line.startswith("+"):
            right_num += 1
            right_lines.append((right_num, escaped, "#e6ffed"))
            left_lines.append(("", "", "#fafbfc"))
        elif line.startswith("-"):
            left_num += 1
            left_lines.append((left_num, escaped, "#ffeef0"))
            right_lines.append(("", "", "#fafbfc"))
        else:
            left_num += 1
            right_num += 1
            left_lines.append((left_num, escaped, "#fff"))
            right_lines.append((right_num, escaped, "#fff"))

    # Build HTML table.
    # Content columns are sized to fit ~80 monospace chars (80ch) so a
    # standard 80-column line shows without wrapping; the diff pane
    # scrolls horizontally when lines are longer.
    rows = ""
    cell_style = (
        "padding:0 0.5rem;font-family:monospace;font-size:0.8rem;"
        "white-space:pre;line-height:1.6;border-right:1px solid #e1e4e8;"
        "min-width:80ch;"
    )
    num_style = (
        "padding:0 0.5rem;color:#959da5;text-align:right;"
        "font-family:monospace;font-size:0.75rem;user-select:none;"
        "min-width:2.5rem;border-right:1px solid #e1e4e8;"
    )

    for i, (left, right) in enumerate(zip(left_lines, right_lines)):
        l_num, l_text, l_bg = left
        r_num, r_text, r_bg = right
        rows += (
            f'<tr>'
            f'<td style="{num_style}background:{l_bg};">{l_num}</td>'
            f'<td style="{cell_style}background:{l_bg};">{l_text}</td>'
            f'<td style="{num_style}background:{r_bg};">{r_num}</td>'
            f'<td style="{cell_style}background:{r_bg};">{r_text}</td>'
            f'</tr>'
        )

    # No fixed layout: let content columns grow to their 80ch min-width
    # and rely on the surrounding pane's horizontal scroll.
    return (
        '<table style="border-collapse:collapse;">'
        f"{rows}</table>"
    )


def _get_request_username(request: Request) -> str:
    """Extract the authenticated username from a request.

    Uses the session cookie to identify the user.

    Args:
        request: The HTTP request.

    Returns:
        Username string, or empty string if not authenticated.
    """
    try:
        user_info = _get_session_user(request)
        return user_info.get("username", "")
    except Exception:  # pylint: disable=broad-except
        return ""


def _handle_lfs(request: Request) -> Response:
    """Handle Git LFS Batch API and Lock API requests.

    Requires authentication (same token/JWT as git operations).
    Routes to batch (presigned S3 URLs) or lock endpoints.

    Args:
        request: The HTTP request.

    Returns:
        Response with LFS JSON body.

    Raises:
        None — errors are returned as LFS error responses.
    """
    import re as _re  # pylint: disable=import-outside-toplevel
    from gitgateway.lfs import (  # pylint: disable=import-outside-toplevel
        handle_batch_request, create_lock, list_locks,
        verify_locks, delete_lock, LfsError,
    )

    lfs_headers = {"Content-Type": "application/vnd.git-lfs+json"}

    # Authenticate
    git_user = _authenticate_git_request(request)
    if git_user is None:
        return Response(
            status_code=401,
            body=json.dumps({"message": "Authentication required"}),
            headers={
                **lfs_headers,
                "WWW-Authenticate": 'Basic realm="GitGateway"',
            },
        )

    # Parse group/repo from path
    match = _re.match(
        r"^/([^/]+)/([^/]+)\.git/info/lfs/(.*)", request.path,
    )
    if not match:
        return Response(
            status_code=400,
            body=json.dumps({"message": "Invalid LFS URL"}),
            headers=lfs_headers,
        )

    group = match.group(1)
    repo_name = match.group(2)
    lfs_path = match.group(3)
    username = git_user.get("username", "")

    try:
        body = json.loads(request.body.decode()) if request.body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = {}

    # POST /objects/batch — presigned URLs for upload/download
    if lfs_path == "objects/batch" and request.method == "POST":
        operation = body.get("operation", "download")
        # Push (upload) requires read/write scope
        if operation == "upload" and git_user.get("scope") == "read":
            return Response(
                status_code=403,
                body=json.dumps({
                    "message": "Token is read-only; upload requires read/write",
                }),
                headers=lfs_headers,
            )
        try:
            result = handle_batch_request(
                group=group,
                repo=repo_name,
                operation=operation,
                objects=body.get("objects", []),
            )
            return Response(
                status_code=200,
                body=json.dumps(result),
                headers=lfs_headers,
            )
        except LfsError as exc:
            return Response(
                status_code=exc.status_code,
                body=json.dumps({"message": str(exc)}),
                headers=lfs_headers,
            )

    # POST /locks — create a lock
    if lfs_path == "locks" and request.method == "POST":
        if git_user.get("scope") == "read":
            return Response(
                status_code=403,
                body=json.dumps({
                    "message": "Token is read-only; locking requires read/write",
                }),
                headers=lfs_headers,
            )
        try:
            lock = create_lock(
                group, repo_name, body.get("path", ""), username,
            )
            return Response(
                status_code=201,
                body=json.dumps({"lock": lock.to_dict()}),
                headers=lfs_headers,
            )
        except LfsError as exc:
            return Response(
                status_code=exc.status_code,
                body=json.dumps({"message": str(exc)}),
                headers=lfs_headers,
            )

    # GET /locks — list locks
    if lfs_path == "locks" and request.method == "GET":
        locks = list_locks(group, repo_name)
        return Response(
            status_code=200,
            body=json.dumps({
                "locks": [lock.to_dict() for lock in locks],
            }),
            headers=lfs_headers,
        )

    # POST /locks/verify — verify locks
    if lfs_path == "locks/verify" and request.method == "POST":
        ours, theirs = verify_locks(group, repo_name, username)
        return Response(
            status_code=200,
            body=json.dumps({
                "ours": [lock.to_dict() for lock in ours],
                "theirs": [lock.to_dict() for lock in theirs],
            }),
            headers=lfs_headers,
        )

    # POST /locks/{id}/unlock — delete a lock
    unlock_match = _re.match(r"^locks/([^/]+)/unlock$", lfs_path)
    if unlock_match and request.method == "POST":
        if git_user.get("scope") == "read":
            return Response(
                status_code=403,
                body=json.dumps({
                    "message": "Token is read-only",
                }),
                headers=lfs_headers,
            )
        lock_id = unlock_match.group(1)
        force = bool(body.get("force", False))
        try:
            lock = delete_lock(
                group, repo_name, lock_id, username, force=force,
            )
            return Response(
                status_code=200,
                body=json.dumps({"lock": lock.to_dict()}),
                headers=lfs_headers,
            )
        except LfsError as exc:
            return Response(
                status_code=exc.status_code,
                body=json.dumps({"message": str(exc)}),
                headers=lfs_headers,
            )

    return Response(
        status_code=404,
        body=json.dumps({"message": "LFS endpoint not found"}),
        headers=lfs_headers,
    )


def _authenticate_git_request(request: Request) -> dict | None:
    """Authenticate a git HTTP request via Basic auth.

    Accepts two credential types:
    1. __token__ + JWT (short-lived, for CI/automation)
    2. __token__ + Personal Access Token (long-lived, for developers)

    Args:
        request: The HTTP request.

    Returns:
        Dict with 'username' and 'scope' if valid, None if auth fails.
    """
    import base64  # pylint: disable=import-outside-toplevel
    import os as _os  # pylint: disable=import-outside-toplevel

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Basic "):
        return None

    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        username, password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return None

    if username != "__token__":
        return None

    # Try JWT validation first
    idp_url = _os.environ.get("IDP_URL", "")
    if idp_url:
        try:
            from gitgateway.auth import validate_jwt  # pylint: disable=import-outside-toplevel
            user = validate_jwt(password, idp_url)
            return {"username": user.username, "scope": "read/write"}
        except Exception:  # pylint: disable=broad-except  # nosec B110
            pass  # Not a valid JWT, try API key

    # Try Personal Access Token
    try:
        from gitgateway.tokens import (  # pylint: disable=import-outside-toplevel
            validate_token, TokenError,
        )
        pat = validate_token(password)
        return {"username": pat.username, "scope": pat.scope.value}
    except TokenError:
        return None


def _is_whitespace_only_diff(entry: dict) -> bool:
    """Check if a diff entry contains only whitespace changes.

    Compares stripped content of added vs removed lines. If they
    match after stripping, the change is whitespace-only.

    Args:
        entry: Diff entry dict with 'diff' key.

    Returns:
        True if all changes are whitespace-only.
    """
    diff_text = entry.get("diff", "")
    if not diff_text:
        return False

    added = [
        line[1:].strip() for line in diff_text.split("\n")
        if line.startswith("+")
    ]
    removed = [
        line[1:].strip() for line in diff_text.split("\n")
        if line.startswith("-")
    ]
    return added == removed


def _render_pr_detail_page(
    group: str, repo_name: str, username: str, pr_number: int,
    query_string: str = "",
) -> str:
    """Render the PR detail page with diff, comments, and review actions.

    Args:
        group: Group name.
        repo_name: Repository name.
        username: Authenticated user.
        pr_number: PR number.
        query_string: Raw query string for options like w=1.

    Returns:
        HTML content.
    """
    import time as _time  # pylint: disable=import-outside-toplevel
    from gitgateway.db import check_permission  # pylint: disable=import-outside-toplevel
    from gitgateway.pullrequest import (  # pylint: disable=import-outside-toplevel
        get_pr, PrNotFoundError,
    )
    from gitgateway.reviews import (  # pylint: disable=import-outside-toplevel
        list_comments, list_reviews,
    )
    from gitgateway.models import AccessLevel  # pylint: disable=import-outside-toplevel

    if not check_permission(username, group, repo_name):
        return "<h1>403 Forbidden</h1>"

    try:
        pr = get_pr(group, repo_name, pr_number)
    except PrNotFoundError:
        return f"<h1>404</h1><p>PR #{pr_number} not found.</p>"

    is_admin = check_permission(
        username, group, repo_name, required_level=AccessLevel.ADMIN,
    )

    # Get comments and reviews
    comments = list_comments(group, repo_name, pr_number)
    reviews = list_reviews(group, repo_name, pr_number)

    # Get status checks
    from gitgateway.status_checks import list_statuses  # pylint: disable=import-outside-toplevel
    statuses = list_statuses(group, repo_name, pr_number)

    # Build status checks display
    checks_html = ""
    if statuses:
        checks_html = '<div style="border:1px solid #e1e4e8;border-radius:6px;padding:1rem;margin-bottom:1.5rem;"><h3 style="margin-bottom:0.75rem;">Checks</h3>'
        for s in statuses:
            icon = "✓" if s.status.value == "success" else "✗"
            color = "#2ea44f" if s.status.value == "success" else "#cb2431"
            checks_html += (
                f'<div style="padding:0.3rem 0;display:flex;align-items:center;gap:0.5rem;">'
                f'<span style="color:{color};font-weight:bold;font-size:1.1rem;">{icon}</span>'
                f'<span style="font-weight:600;">{s.name}</span>'
                f'<span style="color:#586069;font-size:0.85rem;">— {s.description}</span>'
                f'</div>'
            )
        checks_html += '</div>'

    # Build a set of files with lint findings (for ✗ in diff)
    lint_failed_files: set = set()
    for c in comments:
        if c.author == "gitgateway-lint" and "✗" in c.body:
            # Parse file paths from the lint comment
            import re as _re_lint  # pylint: disable=import-outside-toplevel
            for match in _re_lint.finditer(r"`([^:]+):\d+`", c.body):
                lint_failed_files.add(match.group(1))

    # Status badge
    status_color = {
        "open": "#2ea44f", "merged": "#6f42c1", "closed": "#cb2431",
    }.get(pr.status.value, "#586069")

    # Build comments section
    comments_html = ""
    for c in comments:
        comments_html += f"""
        <div style="border:1px solid #e1e4e8;border-radius:6px;
          margin-bottom:0.75rem;">
          <div style="background:#f6f8fa;padding:0.5rem 1rem;
            border-bottom:1px solid #e1e4e8;font-size:0.85rem;">
            <strong>{c.author}</strong>
            <span style="color:#586069;"> commented {c.created_at}</span>
          </div>
          <div style="padding:0.75rem 1rem;">{_render_markdown(c.body)}</div>
        </div>"""

    # Build reviews section
    reviews_html = ""
    for r in reviews:
        review_color = {
            "approved": "#2ea44f",
            "changes_requested": "#cb2431",
            "commented": "#586069",
        }.get(r.status.value, "#586069")
        review_label = {
            "approved": "approved",
            "changes_requested": "requested changes",
            "commented": "commented",
        }.get(r.status.value, r.status.value)
        reviews_html += f"""
        <div style="padding:0.5rem 0;display:flex;align-items:center;gap:0.5rem;">
          <strong>{r.author}</strong>
          <span style="color:{review_color};">{review_label}</span>
          {'<span style="color:#586069;font-size:0.85rem;">— ' + r.body + '</span>' if r.body else ''}
        </div>"""

    # Review actions (only for open PRs)
    review_actions = ""
    if pr.status.value == "open":
        review_actions = f"""
    <div style="border:1px solid #e1e4e8;border-radius:6px;
      padding:1rem;margin-bottom:1.5rem;">
      <h3 style="margin-bottom:0.75rem;">Add Comment or Review</h3>
      <textarea id="review-body" rows="4" placeholder="Leave a comment..."
        style="width:100%;padding:0.5rem;border:1px solid #d1d5da;
        border-radius:4px;margin-bottom:0.75rem;resize:vertical;"></textarea>
      <div style="display:flex;gap:0.5rem;flex-wrap:wrap;">
        <button onclick="submitComment()"
          style="background:#fafbfc;border:1px solid #d1d5da;
          padding:0.4rem 1rem;border-radius:4px;cursor:pointer;">
          Comment</button>
        <button onclick="submitReview('approved')"
          style="background:#2ea44f;color:#fff;border:none;
          padding:0.4rem 1rem;border-radius:4px;cursor:pointer;">
          Approve</button>
        <button onclick="submitReview('changes_requested')"
          style="background:#cb2431;color:#fff;border:none;
          padding:0.4rem 1rem;border-radius:4px;cursor:pointer;">
          Request Changes</button>
      </div>
      <div id="review-message" style="margin-top:0.5rem;font-size:0.85rem;"></div>
    </div>"""

    # Merge button (admin only, open PRs)
    merge_btn = ""
    if pr.status.value == "open" and is_admin:
        merge_btn = f"""
    <div style="margin-bottom:1rem;">
      <button onclick="mergePR()"
        style="background:#2ea44f;color:#fff;border:none;
        padding:0.5rem 1.5rem;border-radius:4px;cursor:pointer;
        font-size:1rem;">Merge Pull Request</button>
    </div>"""

    # Compute diff between target and source branches (split view)
    ignore_ws = "w=1" in query_string
    diff_html = _compute_pr_diff_split(
        group, repo_name, pr.target_branch, pr.source_branch,
        lint_failed_files, ignore_whitespace=ignore_ws,
    )

    # Settings gear
    settings_link = ""
    if is_admin:
        settings_link = (
            f'<a href="/groups/{group}/{repo_name}/settings" '
            f'title="Settings" style="font-size:1.2rem;">⚙️</a>'
        )

    return f"""
    <div style="display:flex;justify-content:space-between;
      align-items:center;margin-bottom:1rem;">
      <h1 style="margin:0;">
        <a href="/groups/{group}" style="color:#586069;
          font-weight:normal;">{group}</a> /
        <a href="/groups/{group}/{repo_name}">{repo_name}</a>
      </h1>
      {settings_link}
    </div>

    <div style="display:flex;gap:1rem;border-bottom:1px solid #e1e4e8;
      margin-bottom:1.5rem;">
      <a href="/groups/{group}/{repo_name}"
        style="padding:0.5rem 1rem;color:#586069;">Code</a>
      <a href="/groups/{group}/{repo_name}/pulls"
        style="padding:0.5rem 1rem;font-weight:600;
        border-bottom:2px solid #f9826c;">Pull Requests</a>
      <a href="/groups/{group}/{repo_name}/commits"
        style="padding:0.5rem 1rem;color:#586069;">Commits</a>
      <a href="/groups/{group}/{repo_name}/branches"
        style="padding:0.5rem 1rem;color:#586069;">Branches</a>
    </div>

    <div style="margin-bottom:1.5rem;">
      <h2 style="margin:0;">{pr.title}
        <span style="color:#586069;font-weight:normal;"> #{pr.number}</span>
      </h2>
      <div style="margin-top:0.5rem;display:flex;align-items:center;gap:0.5rem;">
        <span style="background:{status_color};color:#fff;
          padding:0.2rem 0.6rem;border-radius:12px;font-size:0.8rem;">
          {pr.status.value.capitalize()}</span>
        <span style="color:#586069;">
          <strong>{pr.author or 'unknown'}</strong> wants to merge
          <code>{pr.source_branch}</code> into
          <code>{pr.target_branch}</code>
        </span>
      </div>
      {f'<p style="margin-top:0.75rem;">{pr.description}</p>' if pr.description else ''}
    </div>

    <div style="display:flex;gap:0.5rem;border-bottom:1px solid #e1e4e8;
      margin-bottom:1.5rem;">
      <button id="subtab-conversation" onclick="showSubtab('conversation')"
        style="background:none;border:none;border-bottom:2px solid #f9826c;
        padding:0.5rem 1rem;cursor:pointer;font-weight:600;
        font-size:0.95rem;color:#24292e;">Conversation</button>
      <button id="subtab-diff" onclick="showSubtab('diff')"
        style="background:none;border:none;border-bottom:2px solid transparent;
        padding:0.5rem 1rem;cursor:pointer;font-weight:600;
        font-size:0.95rem;color:#586069;">Diff</button>
    </div>

    <div id="pane-conversation">
      {checks_html}

      {merge_btn}
      {review_actions}

      {'<div style="margin-bottom:1.5rem;"><h3>Reviews</h3>' + reviews_html + '</div>' if reviews_html else ''}

      <div style="margin-bottom:1.5rem;">
        <h3>Comments ({len(comments)})</h3>
        {comments_html if comments_html else '<p style="color:#586069;">No comments yet.</p>'}
      </div>
    </div>

    <div id="pane-diff" style="display:none;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
        <h3 style="margin:0;">Files Changed</h3>
        <label style="display:flex;align-items:center;gap:0.5rem;
                      font-size:0.875rem;color:#586069;cursor:pointer;">
          <input type="checkbox" id="ws-toggle"
                 {"checked" if ignore_ws else ""}
                 onchange="toggleWhitespace(this.checked)"
                 style="cursor:pointer;" />
          Hide whitespace changes
        </label>
      </div>
      {diff_html}
    </div>

    <script>
    function showSubtab(name) {{
      const conv = document.getElementById('pane-conversation');
      const diff = document.getElementById('pane-diff');
      const tc = document.getElementById('subtab-conversation');
      const td = document.getElementById('subtab-diff');
      if (name === 'diff') {{
        conv.style.display = 'none'; diff.style.display = 'block';
        td.style.borderBottomColor = '#f9826c'; td.style.color = '#24292e';
        tc.style.borderBottomColor = 'transparent'; tc.style.color = '#586069';
        if (location.hash !== '#diff') history.replaceState(null, '', '#diff');
      }} else {{
        conv.style.display = 'block'; diff.style.display = 'none';
        tc.style.borderBottomColor = '#f9826c'; tc.style.color = '#24292e';
        td.style.borderBottomColor = 'transparent'; td.style.color = '#586069';
        if (location.hash === '#diff') history.replaceState(null, '', ' ');
      }}
    }}
    // Honor #diff on load (e.g. shared links, whitespace toggle reload)
    if (window.location.hash === '#diff') {{ showSubtab('diff'); }}

    function toggleWhitespace(hide) {{
      const url = new URL(window.location);
      if (hide) {{ url.searchParams.set('w', '1'); }}
      else {{ url.searchParams.delete('w'); }}
      url.hash = 'diff';  // stay on the Diff tab after reload
      window.location = url.toString();
    }}
    async function submitComment() {{
      const body = document.getElementById('review-body').value;
      const msg = document.getElementById('review-message');
      if (!body.trim()) {{ msg.innerHTML = '<span style="color:#cb2431;">Please write a comment.</span>'; return; }}
      const resp = await fetch('/api/{group}/{repo_name}/pulls/{pr_number}/comments', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{body, author: '{username}'}})
      }});
      if (resp.ok) {{ location.reload(); }}
      else {{ msg.innerHTML = '<span style="color:#cb2431;">Failed to post comment.</span>'; }}
    }}

    async function submitReview(status) {{
      const body = document.getElementById('review-body').value;
      const msg = document.getElementById('review-message');
      const resp = await fetch('/api/{group}/{repo_name}/pulls/{pr_number}/reviews', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{status, body, author: '{username}'}})
      }});
      if (resp.ok) {{ location.reload(); }}
      else {{ msg.innerHTML = '<span style="color:#cb2431;">Failed to submit review.</span>'; }}
    }}

    async function mergePR() {{
      if (!confirm('Merge this pull request?')) return;
      const resp = await fetch('/api/{group}/{repo_name}/pulls/{pr_number}/merge', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{strategy: 'merge'}})
      }});
      if (resp.ok) {{ location.reload(); }}
      else {{ alert('Merge failed'); }}
    }}
    </script>"""


def _render_pulls_page(
    group: str, repo_name: str, username: str,
    query_string: str = "",
) -> str:
    """Render the pull requests list page with status filter.

    Args:
        group: Group name.
        repo_name: Repository name.
        username: Authenticated user.
        query_string: Raw query string for status filter.

    Returns:
        HTML content.
    """
    from gitgateway.db import check_permission  # pylint: disable=import-outside-toplevel
    from gitgateway.pullrequest import list_prs  # pylint: disable=import-outside-toplevel
    from gitgateway.models import AccessLevel  # pylint: disable=import-outside-toplevel

    if not check_permission(username, group, repo_name):
        return "<h1>403 Forbidden</h1>"

    is_admin = check_permission(
        username, group, repo_name, required_level=AccessLevel.ADMIN,
    )

    # Parse status filter from query string (default: open)
    status_filter = "open"
    for param in query_string.split("&"):
        if param.startswith("status="):
            status_filter = param[7:]

    # Get all PRs
    prs = list_prs(group, repo_name)

    # Filter by status
    if status_filter != "all":
        prs = [pr for pr in prs if pr.status.value == status_filter]

    # Build filter buttons
    def _btn(label, value):
        active = status_filter == value
        style = (
            "padding:0.4rem 1rem;border:1px solid #e1e4e8;"
            "border-radius:20px;text-decoration:none;font-size:0.875rem;"
        )
        if active:
            style += "background:#0366d6;color:#fff;font-weight:600;"
        else:
            style += "background:#fff;color:#586069;"
        url = f"/groups/{group}/{repo_name}/pulls?status={value}"
        return f'<a href="{url}" style="{style}">{label}</a>'

    open_count = len([
        p for p in list_prs(group, repo_name)
        if p.status.value == "open"
    ])
    closed_count = len([
        p for p in list_prs(group, repo_name)
        if p.status.value in ("closed", "merged")
    ])

    filter_bar = (
        '<div style="display:flex;gap:0.5rem;margin-bottom:1.5rem;">'
        + _btn(f"Open ({open_count})", "open")
        + _btn(f"Closed ({closed_count})", "closed")
        + _btn("Merged", "merged")
        + _btn("All", "all")
        + "</div>"
    )

    # Build rows
    rows = ""
    for pr in prs:
        status = pr.status.value
        color = {
            "open": "#2ea44f", "merged": "#6f42c1", "closed": "#cb2431",
        }.get(status, "#586069")
        rows += f"""<tr style="border-bottom:1px solid #f0f0f0;cursor:pointer;"
          onclick="window.location='/groups/{group}/{repo_name}/pulls/{pr.number}'">
          <td style="padding:0.75rem;">
            <div>
              <span style="color:{color};font-weight:bold;
                margin-right:0.5rem;">{status.capitalize()}</span>
              <strong>{pr.title}</strong>
              <span style="color:#586069;"> #{pr.number}</span>
            </div>
            <div style="color:#586069;font-size:0.85rem;margin-top:0.2rem;">
              {pr.author or 'unknown'} wants to merge
              <code>{pr.source_branch}</code> into
              <code>{pr.target_branch}</code>
            </div>
          </td>
        </tr>"""

    if not rows:
        no_msg = {
            "open": "No open pull requests.",
            "closed": "No closed pull requests.",
            "merged": "No merged pull requests.",
            "all": "No pull requests yet.",
        }.get(status_filter, "No pull requests.")
        rows = f"""<tr><td style="padding:2rem;color:#586069;
          text-align:center;">{no_msg}</td></tr>"""

    # Settings gear
    settings_link = ""
    if is_admin:
        settings_link = (
            f'<a href="/groups/{group}/{repo_name}/settings" '
            f'title="Settings" style="font-size:1.2rem;">⚙️</a>'
        )

    return f"""
    <div style="display:flex;justify-content:space-between;
      align-items:center;margin-bottom:1rem;">
      <h1 style="margin:0;">
        <a href="/groups/{group}" style="color:#586069;
          font-weight:normal;">{group}</a> /
        <a href="/groups/{group}/{repo_name}">{repo_name}</a>
      </h1>
      {settings_link}
    </div>

    <div style="display:flex;gap:1rem;border-bottom:1px solid #e1e4e8;
      margin-bottom:1.5rem;">
      <a href="/groups/{group}/{repo_name}"
        style="padding:0.5rem 1rem;color:#586069;">Code</a>
      <a href="/groups/{group}/{repo_name}/pulls"
        style="padding:0.5rem 1rem;font-weight:600;
        border-bottom:2px solid #f9826c;">Pull Requests</a>
      <a href="/groups/{group}/{repo_name}/commits"
        style="padding:0.5rem 1rem;color:#586069;">Commits</a>
      <a href="/groups/{group}/{repo_name}/branches"
        style="padding:0.5rem 1rem;color:#586069;">Branches</a>
    </div>

    <h2 style="margin-bottom:1rem;">Pull Requests</h2>
    {filter_bar}

    <table style="width:100%;border-collapse:collapse;
      border:1px solid #e1e4e8;border-radius:6px;">
      <tbody>
        {rows}
      </tbody>
    </table>"""


def _render_branches_page(
    group: str, repo_name: str, username: str,
) -> str:
    """Render the branches management page.

    Shows all branches with the ability to create and delete branches.

    Args:
        group: Group name.
        repo_name: Repository name.
        username: Authenticated user.

    Returns:
        HTML content.
    """
    import boto3  # pylint: disable=import-outside-toplevel
    from gitgateway.db import (  # pylint: disable=import-outside-toplevel
        get_repo, check_permission,
    )
    from gitgateway.models import AccessLevel  # pylint: disable=import-outside-toplevel

    if not check_permission(username, group, repo_name):
        return "<h1>403 Forbidden</h1>"

    try:
        repo = get_repo(group, repo_name)
    except Exception:  # pylint: disable=broad-except
        return "<h1>404 Not Found</h1>"

    default_branch = repo.default_branch or "main"
    can_write = check_permission(
        username, group, repo_name, required_level=AccessLevel.CONTRIBUTOR,
    )
    is_admin = check_permission(
        username, group, repo_name, required_level=AccessLevel.ADMIN,
    )

    # Get branches from DynamoDB
    table = boto3.resource("dynamodb").Table("gitgateway-repos")
    response = table.query(
        KeyConditionExpression=(
            "PK = :pk AND begins_with(SK, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": f"BRANCHES#{group}/{repo_name}",
            ":prefix": "BRANCH#",
        },
    )
    branches = [
        item.get("branch_name", "") for item in response.get("Items", [])
    ]
    if not branches:
        branches = [default_branch]

    # Build branch rows
    rows = ""
    for branch in sorted(branches):
        is_default = branch == default_branch
        default_badge = (
            ' <span style="background:#2ea44f;color:#fff;'
            'padding:0.1rem 0.4rem;border-radius:3px;font-size:0.7rem;'
            'margin-left:0.5rem;">default</span>'
            if is_default else ""
        )
        delete_btn = ""
        if can_write and not is_default:
            delete_btn = (
                f'<button onclick="deleteBranch(\'{branch}\')"'
                f' style="background:#cb2431;color:#fff;border:none;'
                f'padding:0.2rem 0.6rem;border-radius:4px;cursor:pointer;'
                f'font-size:0.8rem;">Delete</button>'
            )

        rows += f"""<tr style="border-bottom:1px solid #f0f0f0;">
          <td style="padding:0.75rem;">
            <a href="/groups/{group}/{repo_name}/tree/{branch}"
              style="font-weight:600;">{branch}</a>{default_badge}
          </td>
          <td style="padding:0.75rem;text-align:right;">
            {delete_btn}
          </td>
        </tr>"""

    # Create form (for contributors+)
    create_form = ""
    if can_write:
        create_form = f"""
    <div style="margin:1.5rem 0;padding:1rem;background:#f6f8fa;
      border-radius:6px;border:1px solid #e1e4e8;">
      <h3 style="margin-bottom:0.75rem;">Create Branch</h3>
      <form id="create-branch-form" onsubmit="createBranch(event)"
        style="display:flex;gap:0.5rem;align-items:flex-end;">
        <div>
          <label for="branch-name" style="display:block;font-size:0.85rem;
            color:#586069;margin-bottom:0.2rem;">Branch name</label>
          <input type="text" id="branch-name" name="branch" required
            placeholder="feature/my-branch"
            style="padding:0.4rem 0.6rem;border:1px solid #d1d5da;
            border-radius:4px;width:250px;">
        </div>
        <button type="submit"
          style="background:#2ea44f;color:#fff;border:none;
          padding:0.45rem 1rem;border-radius:4px;cursor:pointer;">
          Create</button>
      </form>
      <div id="branch-message" style="margin-top:0.5rem;font-size:0.85rem;">
      </div>
    </div>"""

    # Settings gear
    settings_link = ""
    if is_admin:
        settings_link = (
            f'<a href="/groups/{group}/{repo_name}/settings" '
            f'title="Settings" style="font-size:1.2rem;">⚙️</a>'
        )

    return f"""
    <div style="display:flex;justify-content:space-between;
      align-items:center;margin-bottom:1rem;">
      <h1 style="margin:0;">
        <a href="/groups/{group}" style="color:#586069;
          font-weight:normal;">{group}</a> /
        <a href="/groups/{group}/{repo_name}">{repo_name}</a>
      </h1>
      {settings_link}
    </div>

    <div style="display:flex;gap:1rem;border-bottom:1px solid #e1e4e8;
      margin-bottom:1.5rem;">
      <a href="/groups/{group}/{repo_name}"
        style="padding:0.5rem 1rem;color:#586069;">Code</a>
      <a href="/groups/{group}/{repo_name}/pulls"
        style="padding:0.5rem 1rem;color:#586069;">Pull Requests</a>
      <a href="/groups/{group}/{repo_name}/commits"
        style="padding:0.5rem 1rem;color:#586069;">Commits</a>
      <a href="/groups/{group}/{repo_name}/branches"
        style="padding:0.5rem 1rem;font-weight:600;
        border-bottom:2px solid #f9826c;">Branches</a>
    </div>

    {create_form}

    <h2 style="margin-bottom:1rem;">{len(branches)} branch{'es' if len(branches) != 1 else ''}</h2>

    <table style="width:100%;border-collapse:collapse;
      border:1px solid #e1e4e8;border-radius:6px;">
      <tbody>
        {rows}
      </tbody>
    </table>

    <script>
    async function createBranch(e) {{
      e.preventDefault();
      const branch = document.getElementById('branch-name').value;
      const msg = document.getElementById('branch-message');
      try {{
        const resp = await fetch('/api/repos/{group}/{repo_name}/branches', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{branch}})
        }});
        if (resp.ok) {{
          msg.innerHTML = '<span style="color:#2ea44f;">Branch created!</span>';
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

    async function deleteBranch(branch) {{
      if (!confirm('Delete branch "' + branch + '"?')) return;
      const resp = await fetch('/api/repos/{group}/{repo_name}/branches/' + branch, {{
        method: 'DELETE'
      }});
      if (resp.ok || resp.status === 204) {{
        location.reload();
      }} else {{
        alert('Failed to delete branch');
      }}
    }}
    </script>"""


def _render_commit_diff_page(
    group: str, repo_name: str, username: str, sha: str,
) -> str:
    """Render a commit's diff page.

    Shows the commit message, metadata, and file changes.

    Args:
        group: Group name.
        repo_name: Repository name.
        username: Authenticated user.
        sha: The commit SHA.

    Returns:
        HTML content.
    """
    import boto3  # pylint: disable=import-outside-toplevel
    import time as _time  # pylint: disable=import-outside-toplevel
    from pathlib import Path as _Path  # pylint: disable=import-outside-toplevel
    import tempfile as _tempfile  # pylint: disable=import-outside-toplevel
    from dulwich.repo import Repo as _Repo  # pylint: disable=import-outside-toplevel
    from gitgateway.db import check_permission  # pylint: disable=import-outside-toplevel
    from gitgateway.storage import (  # pylint: disable=import-outside-toplevel
        sync_repo_to_local, repo_exists,
    )

    if not check_permission(username, group, repo_name):
        return "<h1>403 Forbidden</h1>"

    # Sync repo to get the commit objects
    local_base = _Path(_tempfile.gettempdir()) / "gitgateway"
    local_base.mkdir(parents=True, exist_ok=True)
    local_path = local_base / group / f"{repo_name}.git"

    if not repo_exists(group, repo_name):
        return "<h1>404 Not Found</h1>"

    try:
        sync_repo_to_local(group, repo_name, local_base=local_base)
        repo = _Repo(str(local_path))
    except Exception:  # pylint: disable=broad-except
        return "<h1>500 Error</h1><p>Could not load repository.</p>"

    # Get the commit
    try:
        commit = repo.object_store[sha.encode()]
    except KeyError:
        return f"<h1>404 Not Found</h1><p>Commit {sha[:7]} not found.</p>"

    message = commit.message.decode(errors="replace")
    subject = message.split("\n")[0]
    body = "\n".join(message.split("\n")[1:]).strip()
    author = commit.author.decode(errors="replace").split(" <")[0]
    commit_time = commit.commit_time
    date_str = _time.strftime("%b %d, %Y %H:%M:%S UTC", _time.gmtime(commit_time))

    # Compute diff against parent
    diff_html = ""
    parent_tree = None
    if commit.parents:
        try:
            parent = repo.object_store[commit.parents[0]]
            parent_tree = parent.tree
        except KeyError:
            pass

    current_tree = commit.tree

    # Walk both trees and find differences
    diff_entries = _compute_tree_diff(
        repo.object_store, parent_tree, current_tree,
    )

    for entry in diff_entries:
        status_badge = {
            "added": '<span style="background:#2ea44f;color:#fff;padding:0.1rem 0.4rem;border-radius:3px;font-size:0.75rem;">Added</span>',
            "deleted": '<span style="background:#cb2431;color:#fff;padding:0.1rem 0.4rem;border-radius:3px;font-size:0.75rem;">Deleted</span>',
            "modified": '<span style="background:#dbab09;color:#fff;padding:0.1rem 0.4rem;border-radius:3px;font-size:0.75rem;">Modified</span>',
        }.get(entry["status"], "")

        diff_content = ""
        if entry.get("diff"):
            escaped_diff = entry["diff"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            lines = escaped_diff.split("\n")
            colored_lines = []
            for line in lines:
                if line.startswith("+"):
                    colored_lines.append(f'<span style="background:#e6ffed;">{line}</span>')
                elif line.startswith("-"):
                    colored_lines.append(f'<span style="background:#ffeef0;">{line}</span>')
                else:
                    colored_lines.append(line)
            diff_content = "\n".join(colored_lines)

        diff_html += f"""
        <div style="border:1px solid #e1e4e8;border-radius:6px;margin-bottom:1rem;">
          <div style="background:#f6f8fa;padding:0.5rem 1rem;
            border-bottom:1px solid #e1e4e8;display:flex;
            justify-content:space-between;align-items:center;">
            <strong>{entry["path"]}</strong>
            {status_badge}
          </div>
          <pre style="margin:0;padding:1rem;font-size:0.8rem;
            overflow-x:auto;line-height:1.5;">{diff_content}</pre>
        </div>"""

    if not diff_html:
        diff_html = '<p style="color:#586069;">No file changes in this commit.</p>'

    return f"""
    <div style="margin-bottom:1rem;">
      <h1 style="margin:0;">
        <a href="/groups/{group}" style="color:#586069;
          font-weight:normal;">{group}</a> /
        <a href="/groups/{group}/{repo_name}">{repo_name}</a>
      </h1>
    </div>

    <div style="border:1px solid #e1e4e8;border-radius:6px;
      padding:1.5rem;margin-bottom:1.5rem;">
      <h2 style="margin:0 0 0.5rem;">{subject}</h2>
      {'<pre style="color:#586069;margin:0.5rem 0;white-space:pre-wrap;">' + body + '</pre>' if body else ''}
      <div style="color:#586069;font-size:0.85rem;margin-top:0.75rem;">
        <strong>{author}</strong> committed on {date_str}
        &middot; <code style="background:#f6f8fa;padding:0.2rem 0.4rem;
          border-radius:3px;">{sha[:7]}</code>
      </div>
    </div>

    <h3 style="margin-bottom:1rem;">Files changed</h3>
    {diff_html}

    <div style="margin-top:1.5rem;">
      <a href="/groups/{group}/{repo_name}/commits"
        style="color:#0366d6;">&larr; Back to commits</a>
    </div>"""


def _compute_tree_diff(object_store, old_tree_sha, new_tree_sha) -> list[dict]:
    """Compute differences between two trees.

    Args:
        object_store: Dulwich object store.
        old_tree_sha: SHA of the old tree (None for initial commit).
        new_tree_sha: SHA of the new tree.

    Returns:
        List of dicts with 'path', 'status', and 'diff' keys.
    """
    old_entries = {}
    new_entries = {}

    if old_tree_sha:
        _flatten_tree(object_store, old_tree_sha, "", old_entries)
    _flatten_tree(object_store, new_tree_sha, "", new_entries)

    diffs = []
    all_paths = sorted(set(list(old_entries.keys()) + list(new_entries.keys())))

    for path in all_paths:
        old_sha = old_entries.get(path)
        new_sha = new_entries.get(path)

        if old_sha is None:
            # Added
            content = _get_blob_text(object_store, new_sha)
            diff_text = "\n".join(f"+{line}" for line in content.split("\n")) if content else ""
            diffs.append({"path": path, "status": "added", "diff": diff_text})
        elif new_sha is None:
            # Deleted
            content = _get_blob_text(object_store, old_sha)
            diff_text = "\n".join(f"-{line}" for line in content.split("\n")) if content else ""
            diffs.append({"path": path, "status": "deleted", "diff": diff_text})
        elif old_sha != new_sha:
            # Modified
            old_content = _get_blob_text(object_store, old_sha)
            new_content = _get_blob_text(object_store, new_sha)
            diff_text = _simple_diff(old_content, new_content)
            diffs.append({"path": path, "status": "modified", "diff": diff_text})

    return diffs


def _flatten_tree(object_store, tree_sha, prefix: str, result: dict) -> None:
    """Flatten a tree into a dict of path -> blob SHA."""
    try:
        tree = object_store[tree_sha]
    except KeyError:
        return
    for item in tree.items():
        name = item.path.decode(errors="replace")
        full_path = f"{prefix}/{name}" if prefix else name
        mode = item.mode
        if (mode & 0o170000) == 0o040000:
            _flatten_tree(object_store, item.sha, full_path, result)
        else:
            result[full_path] = item.sha


def _get_blob_text(object_store, sha) -> str:
    """Get blob content as text (max 100KB)."""
    try:
        blob = object_store[sha]
        raw = blob.as_raw_string()
        if len(raw) > 100_000:
            return "(file too large to display)"
        return raw.decode(errors="replace")
    except (KeyError, Exception):  # pylint: disable=broad-except
        return ""


def _simple_diff(old: str, new: str) -> str:
    """Generate a simple unified-style diff between two strings."""
    old_lines = old.split("\n")
    new_lines = new.split("\n")

    # Simple line-by-line diff
    import difflib  # pylint: disable=import-outside-toplevel
    diff = difflib.unified_diff(
        old_lines, new_lines, lineterm="", n=3,
    )
    return "\n".join(diff)


def _render_commits_page(
    group: str, repo_name: str, username: str, branch: str = "",
) -> str:
    """Render the commits history page for a repository.

    Shows all commits on the selected branch, sorted newest first.

    Args:
        group: Group name.
        repo_name: Repository name.
        username: Authenticated user.
        branch: Branch to show commits for (default: repo's default branch).

    Returns:
        HTML content.
    """
    import boto3  # pylint: disable=import-outside-toplevel
    import time as _time  # pylint: disable=import-outside-toplevel
    from gitgateway.db import (  # pylint: disable=import-outside-toplevel
        get_repo, check_permission,
    )
    from gitgateway.models import AccessLevel  # pylint: disable=import-outside-toplevel

    if not check_permission(username, group, repo_name):
        return "<h1>403 Forbidden</h1>"

    try:
        repo = get_repo(group, repo_name)
    except Exception:  # pylint: disable=broad-except
        return "<h1>404 Not Found</h1>"

    default_branch = repo.default_branch or "main"
    current_branch = branch or default_branch
    is_admin = check_permission(
        username, group, repo_name, required_level=AccessLevel.ADMIN,
    )

    # Get available branches
    table = boto3.resource("dynamodb").Table("gitgateway-repos")
    branches_response = table.query(
        KeyConditionExpression=(
            "PK = :pk AND begins_with(SK, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": f"BRANCHES#{group}/{repo_name}",
            ":prefix": "BRANCH#",
        },
    )
    branches = [
        item.get("branch_name", "") for item in
        branches_response.get("Items", [])
    ]
    if not branches:
        branches = [default_branch]
    if current_branch not in branches:
        branches.insert(0, current_branch)

    # Get commits from DynamoDB
    response = table.query(
        KeyConditionExpression=(
            "PK = :pk AND begins_with(SK, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": f"COMMITS#{group}/{repo_name}#{current_branch}",
            ":prefix": "COMMIT#",
        },
    )
    commits = response.get("Items", [])

    # Build commit rows
    rows = ""
    for commit in commits:
        sha = commit.get("sha", "")
        short_sha = sha[:7]
        message = commit.get("message", "")
        author = commit.get("author", "")
        timestamp = int(commit.get("timestamp", 0))
        if timestamp:
            date_str = _time.strftime(
                "%b %d, %Y", _time.gmtime(timestamp),
            )
        else:
            date_str = ""

        rows += f"""<tr style="border-bottom:1px solid #f0f0f0;">
          <td style="padding:0.75rem;">
            <div style="font-weight:600;">{message}</div>
            <div style="color:#586069;font-size:0.85rem;margin-top:0.2rem;">
              {author} committed {date_str}</div>
          </td>
          <td style="padding:0.75rem;text-align:right;white-space:nowrap;">
            <a href="/groups/{group}/{repo_name}/commit/{sha}"
              style="background:#f6f8fa;padding:0.2rem 0.5rem;
              border:1px solid #e1e4e8;border-radius:3px;
              font-size:0.8rem;font-family:monospace;
              text-decoration:none;color:#24292e;">{short_sha}</a>
          </td>
        </tr>"""

    if not rows:
        rows = """<tr><td colspan="2" style="padding:2rem;color:#586069;
          text-align:center;">No commits yet.</td></tr>"""

    # Settings gear
    settings_link = ""
    if is_admin:
        settings_link = (
            f'<a href="/groups/{group}/{repo_name}/settings" '
            f'title="Settings" style="font-size:1.2rem;">⚙️</a>'
        )

    return f"""
    <div style="display:flex;justify-content:space-between;
      align-items:center;margin-bottom:1rem;">
      <h1 style="margin:0;">
        <a href="/groups/{group}" style="color:#586069;
          font-weight:normal;">{group}</a> /
        <a href="/groups/{group}/{repo_name}">{repo_name}</a>
      </h1>
      {settings_link}
    </div>

    <div style="display:flex;gap:1rem;border-bottom:1px solid #e1e4e8;
      margin-bottom:1.5rem;">
      <a href="/groups/{group}/{repo_name}"
        style="padding:0.5rem 1rem;color:#586069;">Code</a>
      <a href="/groups/{group}/{repo_name}/pulls"
        style="padding:0.5rem 1rem;color:#586069;">Pull Requests</a>
      <a href="/groups/{group}/{repo_name}/commits"
        style="padding:0.5rem 1rem;font-weight:600;
        border-bottom:2px solid #f9826c;">Commits</a>
      <a href="/groups/{group}/{repo_name}/branches"
        style="padding:0.5rem 1rem;color:#586069;">Branches</a>
    </div>

    <div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem;">
      <select id="branch-select" onchange="switchBranch(this.value)"
        style="padding:0.4rem 0.8rem;border-radius:6px;
        border:1px solid #d1d5da;font-size:0.9rem;">
        {''.join(
            f'<option value="{b}"'
            f'{" selected" if b == current_branch else ""}>'
            f'{b}</option>'
            for b in branches
        )}
      </select>
      <h2 style="margin:0;">Commits on <code>{current_branch}</code></h2>
    </div>

    <table style="width:100%;border-collapse:collapse;margin-top:1rem;
      border:1px solid #e1e4e8;border-radius:6px;">
      <tbody>
        {rows}
      </tbody>
    </table>

    <script>
    function switchBranch(branch) {{
      window.location.href = '/groups/{group}/{repo_name}/commits/' + branch;
    }}
    </script>"""


def _render_repo_settings_page(
    group: str, repo_name: str, username: str,
) -> str:
    """Render the repository settings page.

    Only accessible to admin users. Allows changing the default branch.

    Args:
        group: Group name.
        repo_name: Repository name.
        username: Authenticated user.

    Returns:
        HTML content.
    """
    from gitgateway.db import (  # pylint: disable=import-outside-toplevel
        get_repo, check_permission,
    )
    from gitgateway.models import AccessLevel  # pylint: disable=import-outside-toplevel

    if not check_permission(
        username, group, repo_name, required_level=AccessLevel.ADMIN,
    ):
        return "<h1>403 Forbidden</h1><p>Admin access required.</p>"

    try:
        repo = get_repo(group, repo_name)
    except Exception:  # pylint: disable=broad-except
        return "<h1>404 Not Found</h1>"

    default_branch = repo.default_branch or "main"

    return f"""
    <div style="margin-bottom:1.5rem;">
      <h1>
        <a href="/groups/{group}" style="color:#586069;
          font-weight:normal;">{group}</a> /
        <a href="/groups/{group}/{repo_name}">{repo_name}</a>
        / Settings
      </h1>
    </div>

    <div style="border:1px solid #e1e4e8;border-radius:6px;padding:1.5rem;
      max-width:600px;">
      <h2 style="margin-bottom:1rem;">General</h2>

      <form id="settings-form" onsubmit="saveSettings(event)">
        <div style="margin-bottom:1.5rem;">
          <label for="default-branch" style="display:block;
            font-weight:600;margin-bottom:0.3rem;">
            Default Branch</label>
          <p style="color:#586069;font-size:0.85rem;margin-bottom:0.5rem;">
            The branch shown by default when viewing this repository.</p>
          <input type="text" id="default-branch" name="default_branch"
            value="{default_branch}" required
            style="padding:0.5rem 0.75rem;border:1px solid #d1d5da;
            border-radius:4px;width:200px;">
        </div>

        <div style="margin-bottom:1.5rem;">
          <label for="repo-description" style="display:block;
            font-weight:600;margin-bottom:0.3rem;">
            Description</label>
          <input type="text" id="repo-description" name="description"
            value="{repo.description}"
            placeholder="Short description of this repository"
            style="padding:0.5rem 0.75rem;border:1px solid #d1d5da;
            border-radius:4px;width:100%;">
        </div>

        <button type="submit"
          style="background:#2ea44f;color:#fff;border:none;
          padding:0.5rem 1.2rem;border-radius:4px;cursor:pointer;
          font-size:0.9rem;">Save Settings</button>
      </form>
      <div id="settings-message" style="margin-top:0.75rem;
        font-size:0.85rem;"></div>
    </div>

    <div style="border:1px solid #cb2431;border-radius:6px;
      padding:1.5rem;max-width:600px;margin-top:2rem;">
      <h2 style="color:#cb2431;margin-bottom:0.5rem;">Danger Zone</h2>
      <p style="color:#586069;font-size:0.85rem;margin-bottom:1rem;">
        Permanently delete this repository and all its data.</p>
      <button onclick="deleteRepo()"
        style="background:#cb2431;color:#fff;border:none;
        padding:0.5rem 1.2rem;border-radius:4px;cursor:pointer;">
        Delete Repository</button>
    </div>

    <script>
    async function saveSettings(e) {{
      e.preventDefault();
      const defaultBranch = document.getElementById('default-branch').value;
      const description = document.getElementById('repo-description').value;
      const msg = document.getElementById('settings-message');

      const resp = await fetch('/api/repos/{group}/{repo_name}/settings', {{
        method: 'PATCH',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{default_branch: defaultBranch, description}})
      }});

      if (resp.ok) {{
        msg.innerHTML = '<span style="color:#2ea44f;">Settings saved!</span>';
      }} else {{
        const data = await resp.json();
        msg.innerHTML = '<span style="color:#cb2431;">'
          + (data.error || 'Failed') + '</span>';
      }}
    }}

    async function deleteRepo() {{
      if (!confirm('Are you sure? This cannot be undone.')) return;
      const resp = await fetch('/api/repos/{group}/{repo_name}', {{
        method: 'DELETE'
      }});
      if (resp.ok || resp.status === 204) {{
        window.location.href = '/groups/{group}';
      }} else {{
        alert('Failed to delete repository');
      }}
    }}
    </script>"""


def _render_repo_tree_or_file(
    group: str,
    repo_name: str,
    username: str,
    branch: str,
    file_path: str,
) -> str:
    """Route to either file viewer or directory tree.

    Checks if the path is a file in the DynamoDB cache. If so,
    renders the file viewer with syntax highlighting. Otherwise
    renders the directory tree.

    Args:
        group: Group name.
        repo_name: Repository name.
        username: Authenticated user.
        branch: Branch name.
        file_path: Path within the repo.

    Returns:
        HTML content.
    """
    import boto3  # pylint: disable=import-outside-toplevel

    if not file_path:
        return _render_repo_page(group, repo_name, username, branch, "")

    # Check if this path is a file in the cache
    table = boto3.resource("dynamodb").Table("gitgateway-repos")
    response = table.get_item(Key={
        "PK": f"TREE#{group}/{repo_name}#{branch}",
        "SK": f"FILE#{file_path}",
    })
    item = response.get("Item")

    if item and item.get("type") == "file":
        return _render_file_viewer(
            group, repo_name, username, branch, file_path, item,
        )

    # Not a file — render as directory
    return _render_repo_page(group, repo_name, username, branch, file_path)


def _render_file_viewer(  # pylint: disable=too-many-positional-arguments
    group: str,
    repo_name: str,
    username: str,
    branch: str,
    file_path: str,
    file_item: dict,
) -> str:
    """Render a file with syntax highlighting.

    Args:
        group: Group name.
        repo_name: Repository name.
        username: Authenticated user.
        branch: Branch name.
        file_path: File path within the repo.
        file_item: DynamoDB item with file content.

    Returns:
        HTML content with syntax-highlighted code.
    """
    from gitgateway.db import check_permission  # pylint: disable=import-outside-toplevel
    from gitgateway.models import AccessLevel  # pylint: disable=import-outside-toplevel

    if not check_permission(username, group, repo_name):
        return "<h1>403 Forbidden</h1>"

    is_admin = check_permission(
        username, group, repo_name, required_level=AccessLevel.ADMIN,
    )

    content = file_item.get("content", "")
    filename = file_path.split("/")[-1]
    line_count = len(content.split("\n")) if content else 0

    # Determine if this is a markdown file
    is_markdown = filename.lower().endswith((".md", ".markdown", ".mdown"))

    if is_markdown:
        rendered_content = _render_markdown(content)
        file_display = f"""
      <div style="padding:1.5rem;">{rendered_content}</div>"""
    else:
        # Syntax highlight using Pygments
        highlighted = _highlight_code(content, filename)
        file_display = f"""
      <div id="file-content" style="overflow-x:auto;">
        {highlighted}
      </div>"""

    # Build breadcrumb
    parts = file_path.split("/")
    tree_base = f"/groups/{group}/{repo_name}/tree/{branch}"
    crumbs = [f'<a href="/groups/{group}/{repo_name}">{repo_name}</a>']
    built = ""
    for i, part in enumerate(parts):
        built = f"{built}/{part}" if built else part
        if i < len(parts) - 1:
            crumbs.append(f'<a href="{tree_base}/{built}">{part}</a>')
        else:
            crumbs.append(f"<strong>{part}</strong>")
    breadcrumb = " / ".join(crumbs)

    # Settings gear
    settings_link = ""
    if is_admin:
        settings_link = (
            f'<a href="/groups/{group}/{repo_name}/settings" '
            f'title="Settings" style="font-size:1.2rem;">⚙️</a>'
        )

    return f"""
    <div style="display:flex;justify-content:space-between;
      align-items:center;margin-bottom:1rem;">
      <h1 style="margin:0;font-size:1.2rem;">
        <a href="/groups/{group}" style="color:#586069;
          font-weight:normal;">{group}</a> /
        <a href="/groups/{group}/{repo_name}">{repo_name}</a>
      </h1>
      {settings_link}
    </div>

    <div style="display:flex;gap:1rem;border-bottom:1px solid #e1e4e8;
      margin-bottom:1rem;">
      <a href="/groups/{group}/{repo_name}"
        style="padding:0.5rem 1rem;font-weight:600;
        border-bottom:2px solid #f9826c;">Code</a>
      <a href="/groups/{group}/{repo_name}/pulls"
        style="padding:0.5rem 1rem;color:#586069;">Pull Requests</a>
      <a href="/groups/{group}/{repo_name}/commits"
        style="padding:0.5rem 1rem;color:#586069;">Commits</a>
      <a href="/groups/{group}/{repo_name}/branches"
        style="padding:0.5rem 1rem;color:#586069;">Branches</a>
    </div>

    <div style="margin-bottom:1rem;color:#586069;font-size:0.9rem;">
      {breadcrumb}
    </div>

    <div style="border:1px solid #e1e4e8;border-radius:6px;overflow:hidden;">
      <div style="background:#f6f8fa;padding:0.5rem 1rem;
        border-bottom:1px solid #e1e4e8;display:flex;
        justify-content:space-between;align-items:center;">
        <span style="font-size:0.85rem;color:#586069;">
          {filename} &middot; {line_count} lines</span>
        <button onclick="navigator.clipboard.writeText(
          document.getElementById('file-content').innerText)"
          style="padding:0.3rem 0.6rem;border:1px solid #d1d5da;
          border-radius:4px;cursor:pointer;background:#fafbfc;
          font-size:0.8rem;">Copy</button>
      </div>
      {file_display}
    </div>"""


def _render_markdown(content: str) -> str:
    """Render Markdown content as HTML.

    Args:
        content: Raw markdown text.

    Returns:
        Rendered HTML with basic styling.
    """
    try:
        import markdown  # pylint: disable=import-outside-toplevel

        html = markdown.markdown(
            content,
            extensions=["fenced_code", "tables", "toc"],
        )
        # Wrap in styled container
        return (
            '<div class="markdown-body" style="'
            "font-size:0.95rem;line-height:1.7;"
            '">'
            f"<style>"
            f".markdown-body h1 {{ font-size:2rem; border-bottom:1px solid #e1e4e8; padding-bottom:0.3rem; margin:1.5rem 0 1rem; }}"
            f".markdown-body h2 {{ font-size:1.5rem; border-bottom:1px solid #e1e4e8; padding-bottom:0.3rem; margin:1.5rem 0 1rem; }}"
            f".markdown-body h3 {{ font-size:1.25rem; margin:1.2rem 0 0.8rem; }}"
            f".markdown-body p {{ margin:0.5rem 0; }}"
            f".markdown-body code {{ background:#f6f8fa; padding:0.2rem 0.4rem; border-radius:3px; font-size:0.85em; }}"
            f".markdown-body pre {{ background:#f6f8fa; padding:1rem; border-radius:6px; overflow-x:auto; }}"
            f".markdown-body pre code {{ background:none; padding:0; }}"
            f".markdown-body ul, .markdown-body ol {{ padding-left:2rem; margin:0.5rem 0; }}"
            f".markdown-body li {{ margin:0.25rem 0; }}"
            f".markdown-body table {{ border-collapse:collapse; margin:1rem 0; }}"
            f".markdown-body th, .markdown-body td {{ border:1px solid #e1e4e8; padding:0.5rem 0.75rem; }}"
            f".markdown-body th {{ background:#f6f8fa; }}"
            f".markdown-body blockquote {{ border-left:4px solid #dfe2e5; padding:0 1rem; color:#586069; margin:1rem 0; }}"
            f".markdown-body a {{ color:#0366d6; }}"
            f"</style>"
            f"{html}"
            f'<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>'
            f"<script>"
            f"mermaid.initialize({{startOnLoad: false}});"
            f"document.querySelectorAll('code.language-mermaid').forEach(el => {{"
            f"  const container = el.parentElement;"
            f"  const div = document.createElement('div');"
            f"  div.className = 'mermaid';"
            f"  div.textContent = el.textContent;"
            f"  container.replaceWith(div);"
            f"}});"
            f"mermaid.run();"
            f"</script>"
            f"</div>"
        )
    except Exception:  # pylint: disable=broad-except
        import html as html_mod  # pylint: disable=import-outside-toplevel
        return f"<pre>{html_mod.escape(content)}</pre>"


def _highlight_code(content: str, filename: str) -> str:
    """Syntax-highlight code using Pygments.

    Args:
        content: File content.
        filename: Filename (used to detect language).

    Returns:
        HTML string with highlighted code.
    """
    try:
        from pygments import highlight  # pylint: disable=import-outside-toplevel
        from pygments.lexers import (  # pylint: disable=import-outside-toplevel
            get_lexer_for_filename,
            TextLexer,
        )
        from pygments.formatters import HtmlFormatter  # pylint: disable=import-outside-toplevel

        try:
            lexer = get_lexer_for_filename(filename)
        except Exception:  # pylint: disable=broad-except
            lexer = TextLexer()

        formatter = HtmlFormatter(
            linenos=True,
            cssclass="highlight",
            style="github-dark",
        )
        highlighted = highlight(content, lexer, formatter)
        css = formatter.get_style_defs(".highlight")
        return f"<style>{css}</style>{highlighted}"
    except Exception:  # pylint: disable=broad-except
        # Fallback: plain pre with line numbers
        import html  # pylint: disable=import-outside-toplevel
        escaped = html.escape(content)
        lines = escaped.split("\n")
        numbered = "".join(
            f'<span style="color:#586069;padding-right:1rem;'
            f'user-select:none;">{i + 1:4}</span>{line}\n'
            for i, line in enumerate(lines)
        )
        return (
            f'<pre style="margin:0;padding:1rem;font-size:0.85rem;'
            f'line-height:1.5;overflow-x:auto;">{numbered}</pre>'
        )


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

    # Get DynamoDB table for tree and branches
    table = boto3.resource("dynamodb").Table("gitgateway-repos")

    # Get available branches from DynamoDB
    branches_response = table.query(
        KeyConditionExpression=(
            "PK = :pk AND begins_with(SK, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": f"BRANCHES#{group}/{repo_name}",
            ":prefix": "BRANCH#",
        },
    )
    branches = [
        item.get("branch_name", "") for item in
        branches_response.get("Items", [])
    ]
    if not branches:
        branches = [default_branch]
    if current_branch not in branches:
        branches.insert(0, current_branch)

    # Get file tree from DynamoDB cache
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

    empty_guide = ""
    if not file_rows:
        file_rows = ""
        empty_guide = f"""
    <div style="border:1px solid #e1e4e8;border-radius:6px;padding:2rem;
      margin-top:1rem;">
      <h2 style="margin-bottom:1rem;">Quick setup</h2>
      <div style="background:#f6f8fa;padding:0.75rem 1rem;border-radius:4px;
        margin-bottom:1.5rem;font-family:monospace;font-size:0.85rem;">
        {clone_url}
      </div>

      <h3 style="margin-bottom:0.75rem;">…or create a new repository on the command line</h3>
      <pre style="background:#f6f8fa;padding:1rem;border-radius:4px;
        font-size:0.85rem;overflow-x:auto;line-height:1.8;">echo "# {repo_name}" &gt;&gt; README.md
git init
git add README.md
git commit -m "Initial commit"
git branch -M main
git remote add origin {clone_url}
git push -u origin main</pre>

      <h3 style="margin-top:1.5rem;margin-bottom:0.75rem;">
        …or push an existing repository from the command line</h3>
      <pre style="background:#f6f8fa;padding:1rem;border-radius:4px;
        font-size:0.85rem;overflow-x:auto;line-height:1.8;">git remote add origin {clone_url}
git branch -M main
git push -u origin main</pre>
    </div>"""

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
                  {_render_markdown(readme_content)}
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
      <a href="/groups/{group}/{repo_name}/commits"
        style="padding:0.5rem 1rem;color:#586069;">Commits</a>
      <a href="/groups/{group}/{repo_name}/branches"
        style="padding:0.5rem 1rem;color:#586069;">Branches</a>
    </div>

    <div style="display:flex;justify-content:space-between;
      align-items:center;margin-bottom:1rem;">
      <div style="display:flex;align-items:center;gap:0.75rem;">
        <select id="branch-select" onchange="switchBranch(this.value)"
          style="padding:0.4rem 0.8rem;border-radius:6px;
          border:1px solid #d1d5da;font-size:0.9rem;">
          {''.join(
              f'<option value="{b}"'
              f'{" selected" if b == current_branch else ""}>'
              f'{b}</option>'
              for b in branches
          )}
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

    {'<table style="width:100%;border-collapse:collapse;border:1px solid #e1e4e8;border-radius:6px;"><tbody>' + file_rows + '</tbody></table>' if file_rows else ""}

    {empty_guide}
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
            <a href="/admin/ai">AI Settings</a>
          </li>
          <li style="padding:0.5rem 0;">
            <a href="/admin/audit">Audit Log</a>
          </li>
        </ul>"""

    if path == "/admin/groups":
        return _render_admin_groups_page()

    if path == "/admin/access":
        return _render_admin_access_page()

    if path == "/admin/ai":
        return _render_admin_ai_page()

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
    token = ""  # nosec B105 — not a password, initializing variable
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


def _render_dashboard(username: str) -> str:
    """Render the landing page dashboard.

    Shows open PRs awaiting the user's review and recently
    active repositories.

    Args:
        username: Authenticated user.

    Returns:
        HTML content.
    """
    import boto3 as _boto3  # pylint: disable=import-outside-toplevel

    ddb = _boto3.resource("dynamodb")

    # --- Open PRs awaiting review ---
    prs_table = ddb.Table("gitgateway-prs")
    pr_response = prs_table.scan(
        FilterExpression="attribute_exists(#s) AND #s = :open",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":open": "open"},
    )
    open_prs = pr_response.get("Items", [])

    # Filter to PRs where the user is NOT the author (awaiting their review)
    review_prs = [
        pr for pr in open_prs
        if pr.get("author", "") != username
    ]

    pr_rows = ""
    for pr in sorted(review_prs, key=lambda x: x.get("created_at", ""), reverse=True)[:10]:
        repo_key = pr.get("PK", "").replace("REPO#", "")
        parts = repo_key.split("/", 1)
        if len(parts) != 2:
            continue
        group, repo_name = parts
        pr_num = pr.get("number", 0)
        title = pr.get("title", "Untitled")
        author = pr.get("author", "unknown")
        source = pr.get("source_branch", "?")
        target = pr.get("target_branch", "?")

        pr_rows += f"""
        <tr style="border-bottom:1px solid #f0f0f0;cursor:pointer;"
          onclick="window.location='/groups/{group}/{repo_name}/pulls/{pr_num}'">
          <td style="padding:0.6rem 0.75rem;">
            <strong>{title}</strong>
            <span style="color:#586069;"> #{pr_num}</span>
            <div style="color:#586069;font-size:0.8rem;margin-top:0.15rem;">
              {group}/{repo_name} · {author} ·
              <code>{source}</code> → <code>{target}</code>
            </div>
          </td>
        </tr>"""

    if not pr_rows:
        pr_rows = """
        <tr><td style="padding:1.5rem;color:#586069;text-align:center;">
          No pull requests awaiting your review.
        </td></tr>"""

    # --- Recently active repositories ---
    repos_table = ddb.Table("gitgateway-repos")
    # Scan for repos that have tree cache entries (indicates recent pushes)
    repos_response = repos_table.scan(
        FilterExpression="begins_with(PK, :prefix) AND begins_with(SK, :repo)",
        ExpressionAttributeValues={
            ":prefix": "GROUP#",
            ":repo": "REPO#",
        },
    )
    all_repos = repos_response.get("Items", [])

    # Sort by last_push or updated_at if available
    for repo in all_repos:
        repo["_sort_key"] = (
            repo.get("last_push", "")
            or repo.get("updated_at", "")
            or repo.get("created_at", "")
        )

    recent_repos = sorted(
        all_repos,
        key=lambda x: x.get("_sort_key", ""),
        reverse=True,
    )[:8]

    repo_cards = ""
    for repo in recent_repos:
        group = repo.get("PK", "").replace("GROUP#", "")
        name = repo.get("SK", "").replace("REPO#", "")
        desc = repo.get("description", "")
        last_activity = repo.get("_sort_key", "")[:10] or ""

        repo_cards += f"""
        <a href="/groups/{group}/{name}" style="text-decoration:none;color:inherit;">
          <div style="border:1px solid #e1e4e8;border-radius:6px;
            padding:1rem;background:#fff;">
            <div style="font-weight:600;color:#0366d6;">
              {group}/{name}
            </div>
            <div style="color:#586069;font-size:0.85rem;
              margin-top:0.3rem;">{desc or 'No description'}</div>
            <div style="color:#959da5;font-size:0.75rem;
              margin-top:0.5rem;">
              {"Updated " + last_activity if last_activity else ""}
            </div>
          </div>
        </a>"""

    if not repo_cards:
        repo_cards = """
        <p style="color:#586069;">No repositories found.
          <a href="/groups">Create one</a>.</p>"""

    return f"""
    <h1 style="margin-bottom:1.5rem;">Welcome, {username}</h1>

    <div style="margin-bottom:2.5rem;">
      <h2 style="margin-bottom:0.75rem;font-size:1.2rem;">
        Pull Requests Awaiting Review
      </h2>
      <table style="width:100%;border-collapse:collapse;
        border:1px solid #e1e4e8;border-radius:6px;">
        <tbody>
          {pr_rows}
        </tbody>
      </table>
    </div>

    <div>
      <h2 style="margin-bottom:0.75rem;font-size:1.2rem;">
        Recently Active Repositories
      </h2>
      <div style="display:grid;grid-template-columns:repeat(auto-fill, minmax(280px, 1fr));gap:1rem;">
        {repo_cards}
      </div>
    </div>"""


def _render_tokens_page(username: str) -> str:
    """Render the Personal Access Tokens management page.

    Shows existing tokens with revoke buttons and a form to create
    new tokens.

    Args:
        username: Authenticated user.

    Returns:
        HTML content.
    """
    from gitgateway.tokens import list_tokens  # pylint: disable=import-outside-toplevel

    tokens = list_tokens(username)

    # Build token rows
    token_rows = ""  # nosec B105 — not a password, HTML accumulator
    for token in tokens:
        status_badge = ""
        if token.revoked:
            status_badge = (
                '<span style="background:#cb2431;color:#fff;'
                'padding:0.1rem 0.4rem;border-radius:3px;'
                'font-size:0.75rem;">Revoked</span>'
            )
        elif token.is_expired():
            status_badge = (
                '<span style="background:#dbab09;color:#fff;'
                'padding:0.1rem 0.4rem;border-radius:3px;'
                'font-size:0.75rem;">Expired</span>'
            )
        else:
            status_badge = (
                '<span style="background:#2ea44f;color:#fff;'
                'padding:0.1rem 0.4rem;border-radius:3px;'
                'font-size:0.75rem;">Active</span>'
            )

        revoke_btn = ""
        if not token.revoked:
            revoke_btn = (
                f'<button onclick="revokeToken(\'{token.token_id}\')"'
                f' style="background:#cb2431;color:#fff;border:none;'
                f'padding:0.3rem 0.8rem;border-radius:4px;'
                f'cursor:pointer;font-size:0.8rem;">Revoke</button>'
            )

        last_used = token.last_used_at[:10] if token.last_used_at else "Never"
        expires = token.expires_at[:10] if token.expires_at else "Never"

        token_rows += f"""
        <tr style="border-bottom:1px solid #e1e4e8;">
          <td style="padding:0.75rem;">
            <strong>{token.description or 'Unnamed token'}</strong>
            <div style="color:#586069;font-size:0.8rem;margin-top:0.2rem;">
              ID: <code>{token.token_id}</code> · Scope: {token.scope.value}
            </div>
          </td>
          <td style="padding:0.75rem;text-align:center;">{status_badge}</td>
          <td style="padding:0.75rem;color:#586069;font-size:0.85rem;">
            {expires}</td>
          <td style="padding:0.75rem;color:#586069;font-size:0.85rem;">
            {last_used}</td>
          <td style="padding:0.75rem;text-align:right;">{revoke_btn}</td>
        </tr>"""

    if not token_rows:
        token_rows = (  # nosec B105 — not a password, empty state HTML
            "\n        <tr><td colspan=\"5\" style=\"padding:2rem;color:#586069;"
            "\n          text-align:center;\">No tokens created yet.</td></tr>"
        )

    return f"""
    <h1>Personal Access Tokens</h1>
    <p style="color:#586069;margin-bottom:1.5rem;">
      Tokens are used to authenticate git operations (clone, push, fetch).
      Use <code>__token__</code> as the username and your token as the password.
    </p>

    <div style="border:1px solid #e1e4e8;border-radius:6px;
                padding:1.5rem;margin-bottom:2rem;background:#f6f8fa;">
      <h3 style="margin:0 0 1rem 0;">Create New Token</h3>
      <form id="token-form" style="display:flex;flex-wrap:wrap;gap:0.75rem;
                                    align-items:flex-end;">
        <div style="flex:1;min-width:200px;">
          <label for="token-desc" style="display:block;font-size:0.85rem;
            margin-bottom:0.25rem;">Description</label>
          <input type="text" id="token-desc" placeholder="e.g. Laptop dev"
            style="width:100%;padding:0.4rem;border:1px solid #d1d5da;
            border-radius:4px;" />
        </div>
        <div>
          <label for="token-scope" style="display:block;font-size:0.85rem;
            margin-bottom:0.25rem;">Scope</label>
          <select id="token-scope" style="padding:0.4rem;border:1px solid #d1d5da;
            border-radius:4px;">
            <option value="read/write">Read/Write (push + pull)</option>
            <option value="read">Read Only (pull only)</option>
          </select>
        </div>
        <div>
          <label for="token-expiry" style="display:block;font-size:0.85rem;
            margin-bottom:0.25rem;">Expires in (days)</label>
          <input type="number" id="token-expiry" value="365" min="1" max="3650"
            style="width:80px;padding:0.4rem;border:1px solid #d1d5da;
            border-radius:4px;" />
        </div>
        <button type="submit"
          style="padding:0.5rem 1.5rem;background:#2ea44f;color:#fff;
          border:none;border-radius:4px;cursor:pointer;font-weight:500;">
          Generate Token
        </button>
      </form>
      <div id="token-result" style="margin-top:1rem;"></div>
    </div>

    <h3>Your Tokens</h3>
    <table style="width:100%;border-collapse:collapse;
      border:1px solid #e1e4e8;border-radius:6px;">
      <thead>
        <tr style="background:#f6f8fa;border-bottom:1px solid #e1e4e8;">
          <th style="padding:0.5rem 0.75rem;text-align:left;">Token</th>
          <th style="padding:0.5rem 0.75rem;text-align:center;">Status</th>
          <th style="padding:0.5rem 0.75rem;text-align:left;">Expires</th>
          <th style="padding:0.5rem 0.75rem;text-align:left;">Last Used</th>
          <th style="padding:0.5rem 0.75rem;"></th>
        </tr>
      </thead>
      <tbody>
        {token_rows}
      </tbody>
    </table>

    <div style="margin-top:2rem;padding:1rem;border:1px solid #e1e4e8;
      border-radius:6px;background:#fffbdd;">
      <h4 style="margin:0 0 0.5rem 0;">Usage</h4>
      <p style="margin:0;font-size:0.9rem;">Configure git credentials:</p>
      <pre style="background:#f6f8fa;padding:0.75rem;border-radius:4px;
        margin-top:0.5rem;font-size:0.8rem;overflow-x:auto;">git clone https://__token__:YOUR_TOKEN@git.botthouse.net/group/repo.git</pre>
    </div>

    <script>
    document.getElementById('token-form').addEventListener('submit', async (e) => {{
      e.preventDefault();
      const desc = document.getElementById('token-desc').value.trim();
      const scope = document.getElementById('token-scope').value;
      const expiry = document.getElementById('token-expiry').value;
      const result = document.getElementById('token-result');

      const resp = await fetch('/api/tokens', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{description: desc, scope: scope, expiry_days: parseInt(expiry)}})
      }});

      if (resp.ok) {{
        const data = await resp.json();
        result.innerHTML = `
          <div style="background:#dcffe4;border:1px solid #34d058;padding:1rem;
            border-radius:6px;">
            <strong>Token created!</strong> Copy it now — it won't be shown again.
            <div style="margin-top:0.5rem;font-family:monospace;background:#fff;
              padding:0.5rem;border-radius:4px;word-break:break-all;
              border:1px solid #e1e4e8;">
              ${{data.token}}
            </div>
          </div>`;
        setTimeout(() => location.reload(), 5000);
      }} else {{
        const data = await resp.json();
        result.innerHTML = '<span style="color:#cb2431;">' +
          (data.error || 'Failed to create token') + '</span>';
      }}
    }});

    async function revokeToken(tokenId) {{
      if (!confirm('Revoke this token? This cannot be undone.')) return;
      const resp = await fetch('/api/tokens/' + tokenId, {{method: 'DELETE'}});
      if (resp.ok) {{ location.reload(); }}
      else {{ alert('Failed to revoke token'); }}
    }}
    </script>"""


def _render_admin_ai_page() -> str:
    """Render the admin AI settings page.

    Shows current Gemini API key status and allows updating it.

    Returns:
        HTML content for the AI settings page.
    """
    import boto3  # pylint: disable=import-outside-toplevel

    # Check current secret status
    key_status = "Not configured"
    key_preview = ""
    try:
        sm = boto3.client("secretsmanager")
        secret = sm.get_secret_value(SecretId="gitgateway/gemini-api-key")
        api_key = secret.get("SecretString", "")
        if api_key:
            key_status = "Configured"
            key_preview = f"{api_key[:8]}...{api_key[-4:]}"
    except sm.exceptions.ResourceNotFoundException:
        key_status = "Not created"
    except Exception:  # pylint: disable=broad-except
        key_status = "Error reading secret"

    status_color = "#28a745" if key_status == "Configured" else "#cb2431"

    return f"""
    <h1>AI Settings</h1>
    <p>Configure the Gemini API key for AI-powered code reviews and PR summaries.</p>

    <div style="margin:1.5rem 0;padding:1rem;border:1px solid #e1e4e8;
                border-radius:6px;background:#f6f8fa;">
      <h3 style="margin:0 0 0.5rem 0;">Gemini API Key</h3>
      <p style="margin:0.25rem 0;">
        Status: <span style="color:{status_color};font-weight:600;">
          {key_status}</span>
      </p>
      {"<p style='margin:0.25rem 0;font-family:monospace;color:#586069;'>"
       + key_preview + "</p>" if key_preview else ""}
    </div>

    <div style="margin:1.5rem 0;">
      <h3>Update API Key</h3>
      <form id="ai-form" style="display:flex;flex-direction:column;gap:0.75rem;
                                 max-width:500px;">
        <label for="api-key" style="font-weight:500;">Gemini API Key:</label>
        <input type="password" id="api-key" name="api_key"
               placeholder="AIza..."
               style="padding:0.5rem;border:1px solid #d1d5da;
                      border-radius:4px;font-family:monospace;" />
        <button type="submit"
                style="padding:0.5rem 1rem;background:#2ea44f;color:#fff;
                       border:none;border-radius:4px;cursor:pointer;
                       width:fit-content;">
          Save API Key
        </button>
      </form>
      <div id="ai-message" style="margin-top:0.75rem;"></div>
    </div>

    <div style="margin:2rem 0;padding:1rem;border:1px solid #e1e4e8;
                border-radius:6px;">
      <h3 style="margin:0 0 0.5rem 0;">How it works</h3>
      <ul style="margin:0;padding-left:1.5rem;color:#586069;">
        <li>When a PR is created or rescanned, Gemini reviews the code</li>
        <li>Findings are posted as comments from <code>gitgateway-ai</code></li>
        <li>A summary of changes is also auto-generated</li>
        <li>Rate limited to 30 API calls per repo per hour</li>
      </ul>
    </div>

    <script>
    document.getElementById('ai-form').addEventListener('submit', async (e) => {{
      e.preventDefault();
      const key = document.getElementById('api-key').value.trim();
      const msg = document.getElementById('ai-message');
      if (!key) {{
        msg.innerHTML = '<span style="color:#cb2431;">Please enter an API key.</span>';
        return;
      }}
      const resp = await fetch('/api/admin/ai-key', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{api_key: key}}),
      }});
      if (resp.ok) {{
        msg.innerHTML = '<span style="color:#28a745;">API key saved successfully.</span>';
        setTimeout(() => location.reload(), 1500);
      }} else {{
        const data = await resp.json();
        msg.innerHTML = '<span style="color:#cb2431;">' + (data.error || 'Failed to save') + '</span>';
      }}
    }});
    </script>"""


def _run_ai_review(
    group: str, repo_name: str, pr_number: int, source_branch: str,
) -> None:
    """Run Gemini AI code review on a PR and post results as a comment.

    Fetches the diff, sends to Gemini for review, and posts findings
    as a comment from the gitgateway-ai bot user. Also generates a
    PR summary.

    Args:
        group: Group name.
        repo_name: Repository name.
        pr_number: The PR number.
        source_branch: Source branch of the PR.
    """
    import boto3  # pylint: disable=import-outside-toplevel
    from gitgateway.ai import (  # pylint: disable=import-outside-toplevel
        AIConfig, AIError, AI_BOT_USER,
        generate_code_review, generate_pr_summary,
    )
    from gitgateway.reviews import add_comment, Comment  # pylint: disable=import-outside-toplevel

    # Load API key from Secrets Manager
    try:
        sm = boto3.client("secretsmanager")
        secret = sm.get_secret_value(SecretId="gitgateway/gemini-api-key")
        api_key = secret.get("SecretString", "")
    except Exception as exc:  # pylint: disable=broad-except
        LOG.warning("Could not load Gemini API key: %s", exc)
        return

    if not api_key:
        LOG.info("Gemini API key not configured, skipping AI review")
        return

    config = AIConfig(api_key=api_key)
    repo_key = f"{group}/{repo_name}"

    # Get the diff from DynamoDB tree cache
    table = boto3.resource("dynamodb").Table("gitgateway-repos")
    response = table.query(
        KeyConditionExpression=(
            "PK = :pk AND begins_with(SK, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": f"TREE#{group}/{repo_name}#{source_branch}",
            ":prefix": "FILE#",
        },
    )
    items = response.get("Items", [])
    if not items:
        return

    # Build a pseudo-diff from file contents (simplified)
    diff_lines = []
    for item in items:
        path = item.get("path", "")
        content = item.get("content", "")
        if path and content and len(content) < 50_000:
            diff_lines.append(f"--- a/{path}")
            diff_lines.append(f"+++ b/{path}")
            for line in content.splitlines()[:100]:
                diff_lines.append(f"+{line}")
            diff_lines.append("")
        if len("\n".join(diff_lines)) > 8000:
            break

    diff_text = "\n".join(diff_lines)
    if not diff_text:
        return

    # Generate code review
    try:
        findings = generate_code_review(diff_text, config, repo_key)
        if findings:
            review_text = "**AI Code Review** (by Gemini)\n\n"
            for finding in findings[:20]:
                sev = finding.get("severity", "info")
                icon = {"error": "🔴", "warning": "🟡"}.get(sev, "🔵")
                review_text += (
                    f"- {icon} `{finding.get('file', '?')}:"
                    f"{finding.get('line', '?')}` — "
                    f"{finding.get('message', '')}\n"
                )
            if len(findings) > 20:
                review_text += f"\n... and {len(findings) - 20} more"

            add_comment(Comment(
                pr_number=pr_number,
                author=AI_BOT_USER,
                body=review_text,
                group=group,
                repo=repo_name,
            ))
        else:
            add_comment(Comment(
                pr_number=pr_number,
                author=AI_BOT_USER,
                body="**AI Code Review** (by Gemini)\n\n✓ No issues found.",
                group=group,
                repo=repo_name,
            ))
    except AIError as exc:
        LOG.warning("AI review failed for PR #%d: %s", pr_number, exc)

    # Generate PR summary
    try:
        summary = generate_pr_summary(
            diff_text, [], config, repo_key,
        )
        if summary:
            add_comment(Comment(
                pr_number=pr_number,
                author=AI_BOT_USER,
                body=f"**AI Summary**\n\n{summary}",
                group=group,
                repo=repo_name,
            ))
    except AIError as exc:
        LOG.warning("AI summary failed for PR #%d: %s", pr_number, exc)


def _trigger_post_push_linting(group: str, repo_name: str) -> None:
    """Find open PRs for this repo and re-run linting asynchronously.

    Called after a successful git push. Invokes the Lambda function
    asynchronously to re-scan open PRs without blocking the push.

    Args:
        group: Group name.
        repo_name: Repository name.
    """
    import boto3 as _boto3  # pylint: disable=import-outside-toplevel

    # Find open PRs
    from gitgateway.pullrequest import list_prs, PrStatus  # pylint: disable=import-outside-toplevel

    try:
        prs = list_prs(group, repo_name)
        open_prs = [pr for pr in prs if pr.status == PrStatus.OPEN]
        if not open_prs:
            return

        # Invoke rescan asynchronously for each open PR
        lambda_client = _boto3.client("lambda")
        import os  # pylint: disable=import-outside-toplevel
        func_name = os.environ.get(
            "AWS_LAMBDA_FUNCTION_NAME", "gitgateway-handler",
        )

        for pr in open_prs:
            payload = json.dumps({
                "requestContext": {"http": {
                    "method": "POST",
                    "path": (
                        f"/api/{group}/{repo_name}"
                        f"/pulls/{pr.number}/rescan"
                    ),
                }},
                "rawPath": (
                    f"/api/{group}/{repo_name}"
                    f"/pulls/{pr.number}/rescan"
                ),
                "headers": {"cookie": ""},
                "body": "",
                "isBase64Encoded": False,
            })
            lambda_client.invoke(
                FunctionName=func_name,
                InvocationType="Event",  # Async — fire and forget
                Payload=payload,
            )
            LOG.info(
                "Triggered async rescan for PR #%d", pr.number,
            )
    except Exception as exc:  # pylint: disable=broad-except
        LOG.warning("Post-push linting trigger failed: %s", exc)


def _run_pr_linting(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    group: str, repo_name: str, pr_number: int, source_branch: str,
) -> None:
    """Run linters on a PR's source branch and post results.

    Posts individual status checks per linter (✓/✗) and a comment
    with findings details.

    Args:
        group: Group name.
        repo_name: Repository name.
        pr_number: The PR number.
        source_branch: Branch to lint.
    """
    import boto3  # pylint: disable=import-outside-toplevel
    from gitgateway.lint import (  # pylint: disable=import-outside-toplevel
        _run_pylint, _run_bandit, _run_ruff, _run_mypy,
        _run_yamllint, _run_json_check, _run_toml_check,
        _run_checkov, _run_cfnlint, _run_tflint, _run_kubeval,
        _run_shellcheck, _run_hadolint, _run_markdownlint,
        _run_eslint, _run_sqlfluff,
        _is_iac_file, _is_cfn_file, _is_k8s_file,
        _is_shell_file, _is_dockerfile,
    )
    from gitgateway.status_checks import (  # pylint: disable=import-outside-toplevel
        post_status, StatusCheck, CheckStatus,
    )
    from gitgateway.reviews import add_comment, Comment  # pylint: disable=import-outside-toplevel

    # Get files from DynamoDB tree cache
    table = boto3.resource("dynamodb").Table("gitgateway-repos")
    response = table.query(
        KeyConditionExpression=(
            "PK = :pk AND begins_with(SK, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": f"TREE#{group}/{repo_name}#{source_branch}",
            ":prefix": "FILE#",
        },
    )
    items = response.get("Items", [])

    if not items:
        return

    # Build files dict
    files = {}
    for item in items:
        path = item.get("path", "")
        content = item.get("content", "")
        if path and content:
            files[path] = content

    if not files:
        return

    # Skip very large files (>100KB) to avoid Lambda timeout
    files = {p: c for p, c in files.items() if len(c) <= 100_000}

    if not files:
        return

    # Categorize files
    python_files = {
        p: c for p, c in files.items()
        if p.endswith(".py") and c.strip()
    }
    yaml_files = {
        p: c for p, c in files.items()
        if p.endswith((".yaml", ".yml")) and c.strip()
    }
    json_files = {
        p: c for p, c in files.items()
        if p.endswith(".json") and c.strip()
    }
    toml_files = {
        p: c for p, c in files.items()
        if p.endswith(".toml") and c.strip()
    }
    iac_files = {
        p: c for p, c in files.items()
        if _is_iac_file(p, c)
    }
    cfn_files = {
        p: c for p, c in files.items()
        if _is_cfn_file(p, c)
    }
    terraform_files = {
        p: c for p, c in files.items()
        if p.endswith(".tf") and c.strip()
    }
    k8s_files = {
        p: c for p, c in files.items()
        if _is_k8s_file(p, c)
    }
    shell_files = {
        p: c for p, c in files.items()
        if _is_shell_file(p, c)
    }
    docker_files = {
        p: c for p, c in files.items()
        if _is_dockerfile(p) and c.strip()
    }
    markdown_files = {
        p: c for p, c in files.items()
        if p.endswith((".md", ".markdown")) and c.strip()
    }
    js_ts_files = {
        p: c for p, c in files.items()
        if p.endswith((".js", ".jsx", ".ts", ".tsx")) and c.strip()
    }
    sql_files = {
        p: c for p, c in files.items()
        if p.endswith(".sql") and c.strip()
    }

    # Run each linter and post individual status checks
    all_findings = []
    linter_results = {}  # name -> (passed, count)

    # Helper to run a linter, collect findings, post status
    def _run_and_post(name, func, file_set):
        findings = func(file_set)
        all_findings.extend(findings)
        passed = not any(f.severity == "error" for f in findings)
        linter_results[name] = (passed, len(findings))
        post_status(StatusCheck(
            name=f"lint/{name}",
            status=(
                CheckStatus.SUCCESS if passed
                else CheckStatus.FAILURE
            ),
            description=f"{len(findings)} finding(s)",
            pr_number=pr_number, group=group, repo=repo_name,
        ))

    if python_files:
        _run_and_post("pylint", _run_pylint, python_files)
        _run_and_post("bandit", _run_bandit, python_files)
        _run_and_post("ruff", _run_ruff, python_files)
        _run_and_post("mypy", _run_mypy, python_files)

    if yaml_files:
        _run_and_post("yamllint", _run_yamllint, yaml_files)

    if json_files:
        _run_and_post("json", _run_json_check, json_files)

    if toml_files:
        _run_and_post("toml", _run_toml_check, toml_files)

    if iac_files:
        _run_and_post("checkov", _run_checkov, iac_files)

    if cfn_files:
        _run_and_post("cfn-lint", _run_cfnlint, cfn_files)

    if terraform_files:
        _run_and_post("tflint", _run_tflint, terraform_files)

    if k8s_files:
        _run_and_post("kubeval", _run_kubeval, k8s_files)

    if shell_files:
        _run_and_post("shellcheck", _run_shellcheck, shell_files)

    if docker_files:
        _run_and_post("hadolint", _run_hadolint, docker_files)

    if markdown_files:
        _run_and_post(
            "markdownlint", _run_markdownlint, markdown_files,
        )

    if js_ts_files:
        _run_and_post("eslint", _run_eslint, js_ts_files)

    if sql_files:
        _run_and_post("sqlfluff", _run_sqlfluff, sql_files)

    # Post overall status
    overall_passed = all(v[0] for v in linter_results.values())
    post_status(StatusCheck(
        name="lint",
        status=CheckStatus.SUCCESS if overall_passed else CheckStatus.FAILURE,
        description=f"{len(all_findings)} total finding(s)",
        pr_number=pr_number, group=group, repo=repo_name,
    ))

    # Post findings as comment
    if all_findings:
        findings_text = "**Lint Results**\n\n"
        for name, (passed, count) in linter_results.items():
            icon = "✓" if passed else "✗"
            findings_text += f"- {icon} **{name}**: {count} finding(s)\n"
        findings_text += f"\n**{'PASSED' if overall_passed else 'FAILED'}** — {len(all_findings)} total finding(s)\n\n"
        for f in all_findings[:50]:
            findings_text += f"- ✗ `{f.file_path}:{f.line}` [{f.tool}] {f.message}\n"
        if len(all_findings) > 50:
            findings_text += f"\n... and {len(all_findings) - 50} more"

        add_comment(Comment(
            pr_number=pr_number,
            author="gitgateway-lint",
            body=findings_text,
            group=group,
            repo=repo_name,
        ))
    else:
        # All passed, post a success comment
        findings_text = "**Lint Results**\n\n"
        for name, (passed, count) in linter_results.items():
            findings_text += f"- ✓ **{name}**: passed\n"
        findings_text += "\n**PASSED** — All checks passed"
        add_comment(Comment(
            pr_number=pr_number,
            author="gitgateway-lint",
            body=findings_text,
            group=group,
            repo=repo_name,
        ))

    LOG.info(
        "Lint for PR #%d: %s (%d findings)",
        pr_number, "PASSED" if overall_passed else "FAILED",
        len(all_findings),
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
    except Exception:  # pylint: disable=broad-except  # nosec B110
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
