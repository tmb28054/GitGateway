"""AI-powered SAST triage for GitGateway.

Uses Gemini to classify SAST findings as true or false positives,
adding confidence annotations to reduce noise.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from gitgateway.ai import AIConfig, AIError, call_gemini
from gitgateway.ai_prompts import sast_triage_prompt

LOG = logging.getLogger(__name__)


@dataclass
class TriageResult:
    """Result of AI triage for a SAST finding.

    Attributes:
        classification: 'true_positive' or 'false_positive'.
        confidence: Confidence score 0.0-1.0.
        reasoning: Brief explanation.
    """

    classification: str = "true_positive"
    confidence: float = 0.5
    reasoning: str = ""

    def to_dict(self) -> dict:
        """Serialize to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "classification": self.classification,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TriageResult:
        """Deserialize from dictionary.

        Args:
            data: Dictionary.

        Returns:
            A TriageResult instance.
        """
        return cls(
            classification=data.get("classification", "true_positive"),
            confidence=float(data.get("confidence", 0.5)),
            reasoning=data.get("reasoning", ""),
        )


def triage_finding(
    finding_message: str,
    code_context: str,
    tool_name: str,
    config: AIConfig,
    repo_key: str = "",
) -> TriageResult:
    """Triage a SAST finding using AI.

    Args:
        finding_message: The SAST tool's finding description.
        code_context: Surrounding code for context.
        tool_name: Name of the SAST tool.
        config: AI configuration.
        repo_key: Repo identifier for rate limiting.

    Returns:
        A TriageResult with classification and confidence.
    """
    prompt = sast_triage_prompt(finding_message, code_context, tool_name)

    try:
        response_text = call_gemini(prompt, config, repo_key)
    except AIError as exc:
        LOG.warning("AI triage failed: %s", exc)
        return TriageResult(
            classification="true_positive",
            confidence=0.0,
            reasoning=f"AI unavailable: {exc}",
        )

    return _parse_triage_response(response_text)


def _parse_triage_response(response_text: str) -> TriageResult:
    """Parse the AI response into a TriageResult.

    Args:
        response_text: Raw response from Gemini.

    Returns:
        A parsed TriageResult, with defaults on parse failure.
    """
    try:
        data = json.loads(response_text)
        if isinstance(data, dict):
            return TriageResult.from_dict(data)
    except json.JSONDecodeError:
        LOG.warning("AI triage response not valid JSON")

    return TriageResult(
        classification="true_positive",
        confidence=0.0,
        reasoning="Could not parse AI response",
    )


def format_triage_annotation(result: TriageResult) -> str:
    """Format a triage result as a human-readable annotation.

    Args:
        result: The triage result.

    Returns:
        Formatted annotation string.
    """
    confidence_pct = int(result.confidence * 100)
    label = (
        "Likely false positive"
        if result.classification == "false_positive"
        else "Likely true positive"
    )
    return (
        f"🤖 AI Triage: {label} "
        f"({confidence_pct}% confidence)\n"
        f"Reasoning: {result.reasoning}"
    )
