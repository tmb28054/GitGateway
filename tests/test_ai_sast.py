"""Tests for gitgateway.ai_sast module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from gitgateway.ai import AIConfig, AIError
from gitgateway.ai_sast import (
    TriageResult,
    _parse_triage_response,
    format_triage_annotation,
    triage_finding,
)


class TestTriageResult:
    """Tests for TriageResult dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dictionary."""
        result = TriageResult(
            classification="false_positive",
            confidence=0.85,
            reasoning="Variable is sanitized",
        )
        d = result.to_dict()
        assert d["classification"] == "false_positive"
        assert d["confidence"] == 0.85

    def test_from_dict(self) -> None:
        """Deserializes from dictionary."""
        data = {
            "classification": "true_positive",
            "confidence": 0.9,
            "reasoning": "Unsanitized input",
        }
        result = TriageResult.from_dict(data)
        assert result.classification == "true_positive"
        assert result.confidence == 0.9

    def test_from_dict_defaults(self) -> None:
        """Handles missing keys."""
        result = TriageResult.from_dict({})
        assert result.classification == "true_positive"
        assert result.confidence == 0.5


class TestTriageFinding:
    """Tests for triage_finding function."""

    def test_successful_triage(self) -> None:
        """Triages a finding successfully."""
        config = AIConfig(api_key="key")
        response_json = json.dumps({
            "classification": "false_positive",
            "confidence": 0.9,
            "reasoning": "Input is validated upstream",
        })

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": response_json}]}},
            ],
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("gitgateway.ai.requests.post", return_value=mock_resp):
            result = triage_finding(
                "SQL injection", "query = 'SELECT ...'",
                "semgrep", config, "team/app",
            )

        assert result.classification == "false_positive"
        assert result.confidence == 0.9

    def test_ai_failure_returns_default(self) -> None:
        """Returns default result when AI fails."""
        config = AIConfig(api_key="key")

        with patch(
            "gitgateway.ai_sast.call_gemini",
            side_effect=AIError("rate limited"),
        ):
            result = triage_finding(
                "issue", "code", "tool", config, "repo",
            )

        assert result.classification == "true_positive"
        assert result.confidence == 0.0
        assert "unavailable" in result.reasoning


class TestParseTriageResponse:
    """Tests for _parse_triage_response function."""

    def test_valid_json(self) -> None:
        """Parses valid JSON response."""
        text = json.dumps({
            "classification": "false_positive",
            "confidence": 0.8,
            "reasoning": "safe",
        })
        result = _parse_triage_response(text)
        assert result.classification == "false_positive"

    def test_invalid_json(self) -> None:
        """Returns default on invalid JSON."""
        result = _parse_triage_response("not json")
        assert result.classification == "true_positive"
        assert result.confidence == 0.0

    def test_non_dict_json(self) -> None:
        """Returns default when JSON is not a dict."""
        result = _parse_triage_response("[1, 2, 3]")
        assert result.classification == "true_positive"


class TestFormatTriageAnnotation:
    """Tests for format_triage_annotation function."""

    def test_false_positive(self) -> None:
        """Formats false positive annotation."""
        result = TriageResult(
            classification="false_positive",
            confidence=0.85,
            reasoning="Variable is sanitized",
        )
        annotation = format_triage_annotation(result)
        assert "false positive" in annotation
        assert "85%" in annotation
        assert "sanitized" in annotation

    def test_true_positive(self) -> None:
        """Formats true positive annotation."""
        result = TriageResult(
            classification="true_positive",
            confidence=0.95,
            reasoning="Unsanitized user input",
        )
        annotation = format_triage_annotation(result)
        assert "true positive" in annotation
        assert "95%" in annotation
