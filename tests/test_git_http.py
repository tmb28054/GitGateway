"""Tests for gitgateway.git_http module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from gitgateway.git_http import (
    GitHttpError,
    GitHttpRequest,
    GitHttpResponse,
    LARGE_TRANSFER_THRESHOLD,
    _parse_cgi_response,
    build_cgi_environ,
    check_large_transfer,
    execute_git_http_backend,
    get_required_access_level,
    handle_git_request,
    parse_git_request,
)
from gitgateway.models import AccessLevel


class TestParseGitRequest:
    """Tests for parse_git_request function."""

    def test_basic_info_refs(self) -> None:
        """Parses a basic info/refs request."""
        req = parse_git_request(
            method="GET",
            path="/mygroup/myrepo.git/info/refs",
            query_string="service=git-upload-pack",
        )
        assert req.group == "mygroup"
        assert req.repo == "myrepo"
        assert req.method == "GET"
        assert "info/refs" in req.path_info
        assert req.query_string == "service=git-upload-pack"

    def test_upload_pack(self) -> None:
        """Parses a git-upload-pack POST request."""
        req = parse_git_request(
            method="POST",
            path="/team/app.git/git-upload-pack",
            content_type="application/x-git-upload-pack-request",
            body=b"pack data",
            remote_user="alice",
        )
        assert req.group == "team"
        assert req.repo == "app"
        assert req.method == "POST"
        assert req.body == b"pack data"
        assert req.remote_user == "alice"

    def test_receive_pack(self) -> None:
        """Parses a git-receive-pack POST request."""
        req = parse_git_request(
            method="POST",
            path="/team/app.git/git-receive-pack",
            content_type="application/x-git-receive-pack-request",
        )
        assert req.group == "team"
        assert req.repo == "app"
        assert "git-receive-pack" in req.path_info

    def test_invalid_path_no_git(self) -> None:
        """Raises on path without .git/."""
        with pytest.raises(GitHttpError, match="Invalid git URL"):
            parse_git_request(method="GET", path="/team/app/info/refs")

    def test_invalid_path_no_repo(self) -> None:
        """Raises on path with less than 2 path components."""
        with pytest.raises(GitHttpError, match="Invalid repository path"):
            parse_git_request(method="GET", path="/repo.git/info/refs")

    def test_nested_group(self) -> None:
        """Handles nested group paths (not typical but valid)."""
        # With current implementation, multi-level paths before .git
        # only the last segment is repo
        req = parse_git_request(
            method="GET",
            path="/org/sub/repo.git/info/refs",
        )
        assert req.group == "org/sub"
        assert req.repo == "repo"


class TestGetRequiredAccessLevel:
    """Tests for get_required_access_level function."""

    def test_upload_pack_is_readonly(self) -> None:
        """Upload-pack (fetch/clone) requires readonly."""
        req = GitHttpRequest(
            method="GET",
            path_info="/team/app.git/info/refs",
            query_string="service=git-upload-pack",
        )
        assert get_required_access_level(req) == AccessLevel.READONLY

    def test_receive_pack_query_is_contributor(self) -> None:
        """Receive-pack in query string requires contributor."""
        req = GitHttpRequest(
            method="GET",
            path_info="/team/app.git/info/refs",
            query_string="service=git-receive-pack",
        )
        assert get_required_access_level(req) == AccessLevel.CONTRIBUTOR

    def test_receive_pack_in_path_is_contributor(self) -> None:
        """Receive-pack in path requires contributor."""
        req = GitHttpRequest(
            method="POST",
            path_info="/team/app.git/git-receive-pack",
        )
        assert get_required_access_level(req) == AccessLevel.CONTRIBUTOR

    def test_no_service_is_readonly(self) -> None:
        """Missing service parameter defaults to readonly."""
        req = GitHttpRequest(
            method="GET",
            path_info="/team/app.git/info/refs",
        )
        assert get_required_access_level(req) == AccessLevel.READONLY

    def test_empty_query_string(self) -> None:
        """Empty query string defaults to readonly."""
        req = GitHttpRequest(
            method="GET",
            path_info="/team/app.git/info/refs",
            query_string="",
        )
        assert get_required_access_level(req) == AccessLevel.READONLY

    def test_query_string_without_service(self) -> None:
        """Query string without service param defaults to readonly."""
        req = GitHttpRequest(
            method="GET",
            path_info="/team/app.git/info/refs",
            query_string="other=value&foo=bar",
        )
        assert get_required_access_level(req) == AccessLevel.READONLY


class TestBuildCgiEnviron:
    """Tests for build_cgi_environ function."""

    def test_basic_environ(self, tmp_path: Path) -> None:
        """Builds correct CGI environment variables."""
        req = GitHttpRequest(
            method="GET",
            path_info="/team/app.git/info/refs",
            query_string="service=git-upload-pack",
            remote_user="alice",
        )
        environ = build_cgi_environ(req, tmp_path)

        assert environ["GIT_PROJECT_ROOT"] == str(tmp_path)
        assert environ["GIT_HTTP_EXPORT_ALL"] == "1"
        assert environ["PATH_INFO"] == "/team/app.git/info/refs"
        assert environ["QUERY_STRING"] == "service=git-upload-pack"
        assert environ["REQUEST_METHOD"] == "GET"
        assert environ["REMOTE_USER"] == "alice"
        assert environ["SERVER_PROTOCOL"] == "HTTP/1.1"
        assert "PATH" in environ

    def test_content_length_for_post(self, tmp_path: Path) -> None:
        """Includes CONTENT_LENGTH for POST with body."""
        req = GitHttpRequest(
            method="POST",
            path_info="/team/app.git/git-upload-pack",
            content_type="application/x-git-upload-pack-request",
            body=b"x" * 100,
        )
        environ = build_cgi_environ(req, tmp_path)
        assert environ["CONTENT_LENGTH"] == "100"
        assert environ["CONTENT_TYPE"] == (
            "application/x-git-upload-pack-request"
        )

    def test_no_content_length_without_body(self, tmp_path: Path) -> None:
        """Omits CONTENT_LENGTH when body is empty."""
        req = GitHttpRequest(method="GET", path_info="/x.git/info/refs")
        environ = build_cgi_environ(req, tmp_path)
        assert "CONTENT_LENGTH" not in environ


class TestExecuteGitHttpBackend:
    """Tests for execute_git_http_backend function."""

    def test_successful_execution(self, tmp_path: Path) -> None:
        """Handles successful git-http-backend execution."""
        request = GitHttpRequest(
            method="GET",
            path_info="/team/app.git/info/refs",
            query_string="service=git-upload-pack",
        )
        fake_output = (
            b"Content-Type: application/x-git-upload-pack-advertisement\r\n"
            b"\r\n"
            b"001e# service=git-upload-pack\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = fake_output
        mock_result.stderr = b""

        with patch("gitgateway.git_http.subprocess.run", return_value=mock_result):
            response = execute_git_http_backend(
                request, tmp_path, "/usr/bin/git-http-backend",
            )

        assert response.status_code == 200
        assert response.content_type == (
            "application/x-git-upload-pack-advertisement"
        )
        assert b"service=git-upload-pack" in response.body

    def test_backend_not_found(self, tmp_path: Path) -> None:
        """Raises GitHttpError when backend binary is missing."""
        request = GitHttpRequest(
            method="GET",
            path_info="/team/app.git/info/refs",
        )
        with patch(
            "gitgateway.git_http.subprocess.run",
            side_effect=FileNotFoundError("not found"),
        ):
            with pytest.raises(GitHttpError, match="not found"):
                execute_git_http_backend(
                    request, tmp_path, "/nonexistent/git-http-backend",
                )

    def test_backend_timeout(self, tmp_path: Path) -> None:
        """Raises GitHttpError on timeout."""
        request = GitHttpRequest(
            method="POST",
            path_info="/team/app.git/git-upload-pack",
        )
        with patch(
            "gitgateway.git_http.subprocess.run",
            side_effect=subprocess.TimeoutExpired("cmd", 300),
        ):
            with pytest.raises(GitHttpError, match="timed out"):
                execute_git_http_backend(request, tmp_path, "/usr/bin/git")

    def test_backend_failure_with_stderr(self, tmp_path: Path) -> None:
        """Raises GitHttpError on non-zero exit with no stdout."""
        request = GitHttpRequest(
            method="GET",
            path_info="/team/app.git/info/refs",
        )
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = b""
        mock_result.stderr = b"fatal: not a git repository"

        with patch("gitgateway.git_http.subprocess.run", return_value=mock_result):
            with pytest.raises(GitHttpError, match="not a git repository"):
                execute_git_http_backend(request, tmp_path, "/usr/bin/git")

    def test_nonzero_return_with_stdout_still_works(
        self, tmp_path: Path,
    ) -> None:
        """Processes output even on non-zero return if stdout exists."""
        request = GitHttpRequest(
            method="GET",
            path_info="/team/app.git/info/refs",
        )
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = (
            b"Status: 404 Not Found\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"Repository not found"
        )
        mock_result.stderr = b""

        with patch("gitgateway.git_http.subprocess.run", return_value=mock_result):
            response = execute_git_http_backend(request, tmp_path, "/usr/bin/git")

        assert response.status_code == 404


class TestParseCgiResponse:
    """Tests for _parse_cgi_response function."""

    def test_standard_response(self) -> None:
        """Parses standard CGI response with CRLF."""
        raw = (
            b"Content-Type: application/x-git-upload-pack-result\r\n"
            b"\r\n"
            b"PACK DATA"
        )
        response = _parse_cgi_response(raw)
        assert response.content_type == (
            "application/x-git-upload-pack-result"
        )
        assert response.body == b"PACK DATA"
        assert response.status_code == 200

    def test_response_with_status(self) -> None:
        """Parses CGI response with Status header."""
        raw = (
            b"Status: 403 Forbidden\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"Access denied"
        )
        response = _parse_cgi_response(raw)
        assert response.status_code == 403
        assert response.content_type == "text/plain"
        assert response.body == b"Access denied"

    def test_response_with_lf_separator(self) -> None:
        """Parses CGI response with LF only (no CR)."""
        raw = (
            b"Content-Type: text/html\n"
            b"\n"
            b"<html>hi</html>"
        )
        response = _parse_cgi_response(raw)
        assert response.content_type == "text/html"
        assert response.body == b"<html>hi</html>"

    def test_response_no_headers(self) -> None:
        """Treats output as body when no header separator found."""
        raw = b"raw output without headers"
        response = _parse_cgi_response(raw)
        assert response.body == raw
        assert response.status_code == 200

    def test_additional_headers(self) -> None:
        """Captures additional CGI headers."""
        raw = (
            b"Content-Type: text/plain\r\n"
            b"X-Custom: value\r\n"
            b"\r\n"
            b"body"
        )
        response = _parse_cgi_response(raw)
        assert response.headers.get("x-custom") == "value"

    def test_invalid_status_header(self) -> None:
        """Handles malformed Status header gracefully."""
        raw = (
            b"Status: not-a-number\r\n"
            b"\r\n"
            b"body"
        )
        response = _parse_cgi_response(raw)
        assert response.status_code == 200  # Falls back to default

    def test_header_line_without_colon(self) -> None:
        """Skips header lines that don't contain a colon."""
        raw = (
            b"Content-Type: text/plain\r\n"
            b"malformed-line\r\n"
            b"\r\n"
            b"body"
        )
        response = _parse_cgi_response(raw)
        assert response.content_type == "text/plain"


class TestCheckLargeTransfer:
    """Tests for check_large_transfer function."""

    def test_small_response_unchanged(self) -> None:
        """Small responses pass through without modification."""
        response = GitHttpResponse(body=b"small")
        result = check_large_transfer(response)
        assert "X-GitGateway-LargeTransfer" not in result.headers

    def test_large_response_gets_header(self) -> None:
        """Large responses get the large transfer header."""
        response = GitHttpResponse(
            body=b"x" * (LARGE_TRANSFER_THRESHOLD + 1),
        )
        result = check_large_transfer(response)
        assert result.headers["X-GitGateway-LargeTransfer"] == "true"


class TestHandleGitRequest:
    """Tests for handle_git_request function."""

    def test_delegates_to_backend_and_checks_size(
        self, tmp_path: Path,
    ) -> None:
        """Calls backend and runs large transfer check."""
        request = GitHttpRequest(
            method="GET",
            path_info="/team/app.git/info/refs",
            query_string="service=git-upload-pack",
        )
        fake_output = (
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"refs"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = fake_output
        mock_result.stderr = b""

        with patch("gitgateway.git_http.subprocess.run", return_value=mock_result):
            response = handle_git_request(
                request, tmp_path, "/usr/bin/git-http-backend",
            )

        assert response.status_code == 200
        assert response.body == b"refs"


class TestGitHttpError:
    """Tests for GitHttpError exception."""

    def test_default_status_code(self) -> None:
        """Default status code is 500."""
        err = GitHttpError("something failed")
        assert err.status_code == 500
        assert str(err) == "something failed"

    def test_custom_status_code(self) -> None:
        """Can set custom status code."""
        err = GitHttpError("not found", status_code=404)
        assert err.status_code == 404
