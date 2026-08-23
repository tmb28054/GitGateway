"""Tests for gitgateway.remote_helper module."""

from __future__ import annotations

from gitgateway.remote_helper import (
    RemoteHelperConfig,
    build_auth_header,
    is_large_transfer_response,
    main,
    parse_remote_url,
)


class TestParseRemoteUrl:
    """Tests for parse_remote_url function."""

    def test_strips_prefix(self) -> None:
        """Strips gitgateway:: prefix."""
        url = parse_remote_url("gitgateway::https://git.example.com/team/app.git")
        assert url == "https://git.example.com/team/app.git"

    def test_no_prefix(self) -> None:
        """Returns URL unchanged if no prefix."""
        url = parse_remote_url("https://git.example.com/repo.git")
        assert url == "https://git.example.com/repo.git"


class TestIsLargeTransferResponse:
    """Tests for is_large_transfer_response function."""

    def test_true_when_header_present(self) -> None:
        """Returns True when large transfer header is set."""
        headers = {"X-GitGateway-LargeTransfer": "true"}
        assert is_large_transfer_response(headers) is True

    def test_false_when_missing(self) -> None:
        """Returns False when header is missing."""
        assert is_large_transfer_response({}) is False

    def test_case_insensitive_value(self) -> None:
        """Handles case-insensitive header value."""
        headers = {"X-GitGateway-LargeTransfer": "True"}
        assert is_large_transfer_response(headers) is True

    def test_false_for_other_value(self) -> None:
        """Returns False for non-true value."""
        headers = {"X-GitGateway-LargeTransfer": "false"}
        assert is_large_transfer_response(headers) is False


class TestBuildAuthHeader:
    """Tests for build_auth_header function."""

    def test_with_token(self) -> None:
        """Returns Bearer auth header."""
        result = build_auth_header("my-jwt-token")
        assert result["Authorization"] == "Bearer my-jwt-token"

    def test_without_token(self) -> None:
        """Returns empty dict when no token."""
        result = build_auth_header("")
        assert result == {}


class TestRemoteHelperConfig:
    """Tests for RemoteHelperConfig dataclass."""

    def test_defaults(self) -> None:
        """Default values."""
        config = RemoteHelperConfig()
        assert config.api_url == ""
        assert config.token == ""
        assert config.s3_bucket == ""


class TestMain:
    """Tests for main entry point."""

    def test_no_args_returns_1(self, capsys) -> None:
        """Returns 1 with usage when insufficient args."""
        result = main([])
        assert result == 1
        captured = capsys.readouterr()
        assert "Usage" in captured.err

    def test_one_arg_returns_1(self, capsys) -> None:
        """Returns 1 when only remote name given."""
        result = main(["origin"])
        assert result == 1

    def test_valid_args_outputs_capabilities(self, capsys) -> None:
        """Outputs capabilities with valid args."""
        result = main(["origin", "gitgateway::https://example.com/repo.git"])
        assert result == 0
        captured = capsys.readouterr()
        assert "capabilities" in captured.out
        assert "connect" in captured.out
