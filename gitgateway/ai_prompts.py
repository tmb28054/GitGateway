"""Prompt templates for GitGateway AI features.

Centralized prompt construction for PR summaries, code review,
and SAST triage.
"""

from __future__ import annotations


def pr_summary_prompt(
    diff_text: str,
    commit_messages: list[str],
    max_diff_chars: int = 8000,
) -> str:
    """Build prompt for PR summary generation.

    Args:
        diff_text: The unified diff.
        commit_messages: List of commit messages.
        max_diff_chars: Maximum diff characters to include.

    Returns:
        The formatted prompt string.
    """
    commits_text = "\n".join(f"- {msg}" for msg in commit_messages)
    truncated_diff = diff_text[:max_diff_chars]

    return (
        "You are a code review assistant. Generate a concise summary "
        "of this pull request based on the diff and commit messages.\n\n"
        "Commit messages:\n"
        f"{commits_text}\n\n"
        "Diff:\n"
        f"{truncated_diff}\n\n"
        "Provide a brief summary of what changed and why, "
        "in 2-4 sentences."
    )


def code_review_prompt(
    diff_text: str,
    max_diff_chars: int = 8000,
) -> str:
    """Build prompt for code review.

    Args:
        diff_text: The unified diff.
        max_diff_chars: Maximum diff characters to include.

    Returns:
        The formatted prompt string.
    """
    truncated_diff = diff_text[:max_diff_chars]

    return (
        "You are a code review assistant. Review this diff for "
        "potential bugs, security issues, and style problems.\n\n"
        "Diff:\n"
        f"{truncated_diff}\n\n"
        "Return your findings as a JSON array where each item has:\n"
        '- "file": the filename\n'
        '- "line": the approximate line number\n'
        '- "message": the issue description\n'
        '- "severity": one of "info", "warning", "error"\n\n'
        "If no issues found, return an empty array: []\n"
        "Return ONLY the JSON array, no other text."
    )


def sast_triage_prompt(
    finding_message: str,
    code_context: str,
    tool_name: str,
) -> str:
    """Build prompt for SAST finding triage.

    Args:
        finding_message: The SAST tool's finding message.
        code_context: Surrounding code context.
        tool_name: Name of the SAST tool.

    Returns:
        The formatted prompt string.
    """
    return (
        "You are a security analysis assistant. A SAST tool has "
        "reported a finding. Analyze whether this is likely a "
        "true positive or false positive.\n\n"
        f"Tool: {tool_name}\n"
        f"Finding: {finding_message}\n\n"
        "Code context:\n"
        f"{code_context}\n\n"
        "Respond with a JSON object containing:\n"
        '- "classification": "true_positive" or "false_positive"\n'
        '- "confidence": a number from 0.0 to 1.0\n'
        '- "reasoning": brief explanation\n\n'
        "Return ONLY the JSON object, no other text."
    )
