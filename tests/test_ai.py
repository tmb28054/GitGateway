"""Tests for gitgateway.ai module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gitgateway.ai import (
    DEFAULT_MAX_CALLS_PER_HOUR,
    DEFAULT_MODEL,
    AIConfig,
    AIError,
    _check_rate_limit,
    _record_call,
    call_gemini,
    clear_rate_limits,
    generate_code_review,
    generate_pr_summary,
)


@pytest.fixture(autouse=True)
def _clear_limits():
    """Clear rate limits between tests."""
    clear_rate_limits()
    yield
    clear_rate_limits()


class TestAIConfig:
    """Tests for AIConfig dataclass."""

    def test_defaults(self) -> None:
        """Default values are sensible."""
        config = AIConfig()
        assert config.model == DEFAULT_MODEL
        assert config.max_calls_per_hour == DEFAULT_MAX_CALLS_PER_HOUR
        assert config.enabled is True


class TestRateLimit:
    """Tests for rate limiting functions."""

    def test_under_limit(self) -> None:
        """Returns True when under the limit."""
        assert _check_rate_limit("repo/x", 5) is True

    def test_at_limit(self) -> None:
        """Returns False when at the limit."""
        for _ in range(5):
            _record_call("repo/x")
        assert _check_rate_limit("repo/x", 5) is False

    def test_old_entries_expire(self) -> None:
        """Old entries expire after 1 hour."""
        import time  # pylint: disable=import-outside-toplevel
        from gitgateway.ai import _CALL_LOG  # pylint: disable=import-outside-toplevel

        _CALL_LOG["repo/x"] = [time.time() - 7200]  # 2 hours ago
        assert _check_rate_limit("repo/x", 1) is True

    def test_record_call_initializes(self) -> None:
        """record_call creates entry if repo not tracked."""
        _record_call("new/repo")
        assert not _check_rate_limit("new/repo", 1)


class TestCallGemini:
    """Tests for call_gemini function."""

    def test_successful_call(self) -> None:
        """Makes API call and returns response text."""
        config = AIConfig(api_key="test-key")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": "Generated summary"}]}},
            ],
        }
        mock_response.raise_for_status = MagicMock()

        with patch(
            "gitgateway.ai.requests.post", return_value=mock_response,
        ) as post:
            result = call_gemini("test prompt", config, "team/app")

        assert result == "Generated summary"
        # The API key must travel in a header, never in the URL/query string.
        _pos_args, kwargs = post.call_args
        url = post.call_args[0][0] if _pos_args else kwargs.get("url", "")
        assert "test-key" not in url
        assert "key=" not in url
        assert kwargs["headers"]["x-goog-api-key"] == "test-key"

    def test_disabled_raises(self) -> None:
        """Raises when AI is disabled."""
        config = AIConfig(api_key="key", enabled=False)
        with pytest.raises(AIError, match="disabled"):
            call_gemini("prompt", config)

    def test_no_api_key_raises(self) -> None:
        """Raises when API key is empty."""
        config = AIConfig(api_key="")
        with pytest.raises(AIError, match="not configured"):
            call_gemini("prompt", config)

    def test_rate_limit_exceeded(self) -> None:
        """Raises when rate limit is exceeded."""
        config = AIConfig(api_key="key", max_calls_per_hour=2)
        _record_call("team/app")
        _record_call("team/app")
        with pytest.raises(AIError, match="Rate limit"):
            call_gemini("prompt", config, "team/app")

    def test_request_failure_raises(self) -> None:
        """Raises on HTTP error."""
        config = AIConfig(api_key="key")
        import requests as req  # pylint: disable=import-outside-toplevel

        with patch(
            "gitgateway.ai.requests.post",
            side_effect=req.RequestException("timeout"),
        ):
            with pytest.raises(AIError, match="API call failed"):
                call_gemini("prompt", config, "team/app")

    def test_empty_candidates(self) -> None:
        """Returns empty string when no candidates."""
        config = AIConfig(api_key="key")
        mock_response = MagicMock()
        mock_response.json.return_value = {"candidates": []}
        mock_response.raise_for_status = MagicMock()

        with patch("gitgateway.ai.requests.post", return_value=mock_response):
            result = call_gemini("prompt", config)

        assert result == ""

    def test_empty_parts(self) -> None:
        """Returns empty string when no parts in content."""
        config = AIConfig(api_key="key")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": []}}],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("gitgateway.ai.requests.post", return_value=mock_response):
            result = call_gemini("prompt", config)

        assert result == ""

    def test_no_repo_key_skips_rate_limit(self) -> None:
        """No rate limit check when repo_key is empty."""
        config = AIConfig(api_key="key", max_calls_per_hour=0)
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("gitgateway.ai.requests.post", return_value=mock_response):
            result = call_gemini("prompt", config, "")

        assert result == "ok"


class TestGeneratePrSummary:
    """Tests for generate_pr_summary function."""

    def test_generates_summary(self) -> None:
        """Calls Gemini and returns summary."""
        config = AIConfig(api_key="key")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": "This PR fixes a bug."}]}},
            ],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("gitgateway.ai.requests.post", return_value=mock_response):
            result = generate_pr_summary(
                "diff content", ["fix: bug"], config, "team/app",
            )

        assert "bug" in result


class TestGenerateCodeReview:
    """Tests for generate_code_review function."""

    def test_parses_json_response(self) -> None:
        """Parses valid JSON review findings."""
        config = AIConfig(api_key="key")
        findings_json = json.dumps([
            {"file": "app.py", "line": 10, "message": "Issue", "severity": "warning"},
        ])
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": findings_json}]}},
            ],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("gitgateway.ai.requests.post", return_value=mock_response):
            result = generate_code_review("diff", config, "team/app")

        assert len(result) == 1
        assert result[0]["file"] == "app.py"

    def test_invalid_json_returns_empty(self) -> None:
        """Returns empty list when response is not JSON."""
        config = AIConfig(api_key="key")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": "not json at all"}]}},
            ],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("gitgateway.ai.requests.post", return_value=mock_response):
            result = generate_code_review("diff", config, "team/app")

        assert result == []

    def test_non_list_json_returns_empty(self) -> None:
        """Returns empty list when JSON is not a list."""
        config = AIConfig(api_key="key")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": '{"not": "a list"}'}]}},
            ],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("gitgateway.ai.requests.post", return_value=mock_response):
            result = generate_code_review("diff", config, "team/app")

        assert result == []


# Need json import for test
import json  # noqa: E402  pylint: disable=wrong-import-position
