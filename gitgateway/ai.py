"""AI integration for GitGateway - PR summaries and code review.

Provides Gemini-powered PR auto-summarization and advisory code
review. All AI features are opt-in per repository and post as
the 'gitgateway-ai' bot user.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import requests

LOG = logging.getLogger(__name__)

AI_BOT_USER = "gitgateway-ai"
DEFAULT_MODEL = "gemini-3.6-flash"
DEFAULT_MAX_CALLS_PER_HOUR = 30


class AIError(Exception):
    """Raised when an AI operation fails."""


@dataclass
class AIConfig:
    """AI integration configuration.

    Attributes:
        api_key: Gemini API key.
        model: Model name to use.
        max_calls_per_hour: Rate limit per repo.
        enabled: Whether AI features are globally enabled.
    """

    api_key: str = ""
    model: str = DEFAULT_MODEL
    max_calls_per_hour: int = DEFAULT_MAX_CALLS_PER_HOUR
    enabled: bool = True


# Rate limit tracking (in production, use DynamoDB)
_CALL_LOG: dict[str, list[float]] = {}


def _check_rate_limit(repo_key: str, max_calls: int) -> bool:
    """Check if rate limit allows another call.

    Args:
        repo_key: The repo identifier for rate limiting.
        max_calls: Maximum calls per hour.

    Returns:
        True if under the limit.
    """
    now = time.time()
    one_hour_ago = now - 3600

    if repo_key not in _CALL_LOG:
        _CALL_LOG[repo_key] = []

    # Clean old entries
    _CALL_LOG[repo_key] = [
        t for t in _CALL_LOG[repo_key] if t > one_hour_ago
    ]

    return len(_CALL_LOG[repo_key]) < max_calls


def _record_call(repo_key: str) -> None:
    """Record an API call for rate limiting.

    Args:
        repo_key: The repo identifier.
    """
    if repo_key not in _CALL_LOG:
        _CALL_LOG[repo_key] = []
    _CALL_LOG[repo_key].append(time.time())


def clear_rate_limits() -> None:
    """Clear rate limit tracking. Used in testing."""
    _CALL_LOG.clear()


def call_gemini(
    prompt: str,
    config: AIConfig,
    repo_key: str = "",
) -> str:
    """Call the Gemini API with a prompt.

    Args:
        prompt: The prompt text to send.
        config: AI configuration with API key and model.
        repo_key: Repo identifier for rate limiting.

    Returns:
        The generated text response.

    Raises:
        AIError: If the API call fails or rate limit exceeded.
    """
    if not config.enabled:
        raise AIError("AI features are disabled")

    if not config.api_key:
        raise AIError("Gemini API key not configured")

    if repo_key and not _check_rate_limit(
        repo_key, config.max_calls_per_hour,
    ):
        raise AIError(
            f"Rate limit exceeded for {repo_key}: "
            f"max {config.max_calls_per_hour} calls/hour",
        )

    # Send the API key in a request header rather than the URL query
    # string so it cannot leak into access logs, proxies, or traces.
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{config.model}:generateContent"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
    }

    try:
        response = requests.post(
            url,
            json=payload,
            headers={"x-goog-api-key": config.api_key},
            timeout=60,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise AIError(f"Gemini API call failed: {exc}") from exc

    if repo_key:
        _record_call(repo_key)

    data = response.json()
    candidates = data.get("candidates", [])
    if not candidates:
        return ""

    content = candidates[0].get("content", {})
    parts = content.get("parts", [])
    if not parts:
        return ""

    return parts[0].get("text", "")


def generate_pr_summary(
    diff_text: str,
    commit_messages: list[str],
    config: AIConfig,
    repo_key: str = "",
) -> str:
    """Generate an AI summary for a pull request.

    Args:
        diff_text: The unified diff of the PR.
        commit_messages: List of commit messages in the PR.
        config: AI configuration.
        repo_key: Repo identifier for rate limiting.

    Returns:
        Generated summary text.

    Raises:
        AIError: If the API call fails.
    """
    commits_text = "\n".join(f"- {msg}" for msg in commit_messages)

    prompt = (
        "You are a senior software engineer reviewing a pull request. "
        "Your job is to provide actionable feedback that improves the "
        "code quality, maintainability, and correctness.\n\n"
        "Commit messages:\n"
        f"{commits_text}\n\n"
        "Diff:\n"
        f"{diff_text[:8000]}\n\n"
        "Write a summary that covers:\n"
        "1. What this PR accomplishes (1-2 sentences)\n"
        "2. Potential risks or concerns with the approach (if any)\n"
        "3. One concrete suggestion for improvement\n\n"
        "Be direct and specific. Don't just describe what changed — "
        "explain whether the approach is sound and what could be better."
    )

    return call_gemini(prompt, config, repo_key)


def generate_code_review(
    diff_text: str,
    config: AIConfig,
    repo_key: str = "",
) -> list[dict]:
    """Generate AI code review comments for a diff.

    Args:
        diff_text: The unified diff to review.
        config: AI configuration.
        repo_key: Repo identifier for rate limiting.

    Returns:
        List of review comment dicts with 'file', 'line',
        'message', and 'severity' keys.

    Raises:
        AIError: If the API call fails.
    """
    prompt = (
        "You are a senior software engineer performing a code review. "
        "Focus on providing actionable suggestions that improve the code. "
        "Don't just point out what exists — suggest HOW to make it better.\n\n"
        "Review priorities (in order):\n"
        "1. Bugs or logic errors that would cause incorrect behavior\n"
        "2. Security vulnerabilities (injection, auth bypass, data exposure)\n"
        "3. Performance issues (N+1 queries, unnecessary allocations, "
        "missing caching)\n"
        "4. Maintainability (unclear naming, missing error handling, "
        "tight coupling)\n"
        "5. Improvements (better algorithms, simpler approaches, "
        "missing edge cases)\n\n"
        "For each finding, explain:\n"
        "- WHAT is wrong or could be better\n"
        "- WHY it matters\n"
        "- HOW to fix it (concrete suggestion)\n\n"
        "Diff:\n"
        f"{diff_text[:8000]}\n\n"
        "Return your findings as a JSON array where each item has:\n"
        '- "file": the filename\n'
        '- "line": the approximate line number\n'
        '- "message": what to improve and how (be specific and actionable)\n'
        '- "severity": "error" for bugs/security, "warning" for performance/'
        'maintainability, "info" for style/improvements\n\n'
        "If the code looks good, still suggest at least one improvement. "
        "Good code can always be made better.\n"
        "Return ONLY the JSON array, no other text."
    )

    response_text = call_gemini(prompt, config, repo_key)

    try:
        findings = json.loads(response_text)
        if isinstance(findings, list):
            return findings
    except json.JSONDecodeError:
        LOG.warning("AI review response was not valid JSON: %s", response_text[:100])

    return []
