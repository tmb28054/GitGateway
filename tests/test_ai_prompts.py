"""Tests for gitgateway.ai_prompts module."""

from __future__ import annotations

from gitgateway.ai_prompts import (
    code_review_prompt,
    pr_summary_prompt,
    sast_triage_prompt,
)


class TestPrSummaryPrompt:
    """Tests for pr_summary_prompt function."""

    def test_includes_commits(self) -> None:
        """Includes commit messages in prompt."""
        prompt = pr_summary_prompt(
            "diff text", ["fix: bug", "feat: new feature"],
        )
        assert "fix: bug" in prompt
        assert "feat: new feature" in prompt

    def test_includes_diff(self) -> None:
        """Includes diff text in prompt."""
        prompt = pr_summary_prompt("my diff content", [])
        assert "my diff content" in prompt

    def test_truncates_long_diff(self) -> None:
        """Truncates diff to max_diff_chars."""
        long_diff = "x" * 20000
        prompt = pr_summary_prompt(long_diff, [], max_diff_chars=100)
        assert len(prompt) < 20000
        assert "x" * 100 in prompt


class TestCodeReviewPrompt:
    """Tests for code_review_prompt function."""

    def test_includes_diff(self) -> None:
        """Includes diff in prompt."""
        prompt = code_review_prompt("diff content here")
        assert "diff content here" in prompt

    def test_requests_json(self) -> None:
        """Requests JSON array output."""
        prompt = code_review_prompt("diff")
        assert "JSON array" in prompt

    def test_truncates_long_diff(self) -> None:
        """Truncates diff to max chars."""
        prompt = code_review_prompt("y" * 50000, max_diff_chars=200)
        assert "y" * 200 in prompt
        assert "y" * 201 not in prompt


class TestSastTriagePrompt:
    """Tests for sast_triage_prompt function."""

    def test_includes_finding(self) -> None:
        """Includes finding message."""
        prompt = sast_triage_prompt(
            "Hardcoded password", "password = 'secret'", "bandit",
        )
        assert "Hardcoded password" in prompt
        assert "bandit" in prompt

    def test_includes_code_context(self) -> None:
        """Includes code context."""
        prompt = sast_triage_prompt(
            "SQL injection", "query = f'SELECT * FROM {table}'", "semgrep",
        )
        assert "SELECT * FROM" in prompt

    def test_requests_json_output(self) -> None:
        """Requests JSON classification output."""
        prompt = sast_triage_prompt("issue", "code", "tool")
        assert "true_positive" in prompt
        assert "false_positive" in prompt
        assert "confidence" in prompt
