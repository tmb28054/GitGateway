"""Tests for gitgateway.codebuild module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gitgateway.codebuild import (
    BuildResult,
    CodeBuildError,
    _get_codebuild_client,
    get_build_status,
    map_build_status_to_check,
    start_build,
)


class TestBuildResult:
    """Tests for BuildResult dataclass."""

    def test_to_dict(self) -> None:
        """Serializes to dictionary."""
        result = BuildResult(
            build_id="proj:abc123",
            status="SUCCEEDED",
            logs_url="https://logs.aws/123",
        )
        d = result.to_dict()
        assert d["build_id"] == "proj:abc123"
        assert d["status"] == "SUCCEEDED"

    def test_from_dict(self) -> None:
        """Deserializes from dictionary."""
        data = {"build_id": "x:y", "status": "FAILED", "logs_url": ""}
        result = BuildResult.from_dict(data)
        assert result.status == "FAILED"

    def test_from_dict_defaults(self) -> None:
        """Handles missing keys."""
        result = BuildResult.from_dict({})
        assert result.build_id == ""


class TestGetCodebuildClient:
    """Tests for _get_codebuild_client helper."""

    def test_returns_provided_client(self) -> None:
        """Returns the provided client."""
        mock = MagicMock()
        assert _get_codebuild_client(mock) is mock

    @patch("gitgateway.codebuild.boto3.client")
    def test_creates_default_client(self, mock_boto) -> None:
        """Creates default client when none provided."""
        _get_codebuild_client()
        mock_boto.assert_called_once_with("codebuild")


class TestStartBuild:
    """Tests for start_build function."""

    def test_starts_build(self) -> None:
        """Starts a CodeBuild project."""
        mock_client = MagicMock()
        mock_client.start_build.return_value = {
            "build": {"id": "project:build-123"},
        }

        build_id = start_build(
            "my-project", "bucket", "source.zip",
            codebuild_client=mock_client,
        )
        assert build_id == "project:build-123"
        mock_client.start_build.assert_called_once()

    def test_with_env_vars(self) -> None:
        """Passes environment variables."""
        mock_client = MagicMock()
        mock_client.start_build.return_value = {
            "build": {"id": "p:b"},
        }

        start_build(
            "proj", "bucket", "src.zip",
            environment_variables={"PR": "1", "REPO": "team/app"},
            codebuild_client=mock_client,
        )
        call_kwargs = mock_client.start_build.call_args[1]
        env_vars = call_kwargs["environmentVariablesOverride"]
        names = {v["name"] for v in env_vars}
        assert "PR" in names
        assert "REPO" in names

    def test_with_buildspec_override(self) -> None:
        """Passes buildspec override."""
        mock_client = MagicMock()
        mock_client.start_build.return_value = {
            "build": {"id": "p:b"},
        }

        start_build(
            "proj", "bucket", "src.zip",
            buildspec_override="version: 0.2\nphases: ...",
            codebuild_client=mock_client,
        )
        call_kwargs = mock_client.start_build.call_args[1]
        assert "buildspecOverride" in call_kwargs

    def test_failure_raises(self) -> None:
        """Raises CodeBuildError on failure."""
        mock_client = MagicMock()
        mock_client.start_build.side_effect = Exception("access denied")
        mock_client.exceptions = MagicMock()
        mock_client.exceptions.ResourceNotFoundException = Exception

        with pytest.raises(CodeBuildError, match="Failed to start"):
            start_build("proj", "bucket", "src.zip", codebuild_client=mock_client)


class TestGetBuildStatus:
    """Tests for get_build_status function."""

    def test_gets_status(self) -> None:
        """Gets build status."""
        mock_client = MagicMock()
        mock_client.batch_get_builds.return_value = {
            "builds": [{
                "id": "proj:abc",
                "buildStatus": "SUCCEEDED",
                "logs": {"deepLink": "https://logs/abc"},
                "startTime": "2025-01-01",
                "endTime": "2025-01-01",
            }],
        }

        result = get_build_status("proj:abc", mock_client)
        assert result.status == "SUCCEEDED"
        assert result.logs_url == "https://logs/abc"

    def test_build_not_found(self) -> None:
        """Raises when build not found."""
        mock_client = MagicMock()
        mock_client.batch_get_builds.return_value = {"builds": []}

        with pytest.raises(CodeBuildError, match="not found"):
            get_build_status("ghost:id", mock_client)

    def test_api_error_raises(self) -> None:
        """Raises on API error."""
        mock_client = MagicMock()
        mock_client.batch_get_builds.side_effect = Exception("timeout")

        with pytest.raises(CodeBuildError, match="Failed to get"):
            get_build_status("x:y", mock_client)


class TestMapBuildStatusToCheck:
    """Tests for map_build_status_to_check function."""

    def test_succeeded(self) -> None:
        """SUCCEEDED maps to success."""
        assert map_build_status_to_check("SUCCEEDED") == "success"

    def test_failed(self) -> None:
        """FAILED maps to failure."""
        assert map_build_status_to_check("FAILED") == "failure"

    def test_in_progress(self) -> None:
        """IN_PROGRESS maps to pending."""
        assert map_build_status_to_check("IN_PROGRESS") == "pending"

    def test_timed_out(self) -> None:
        """TIMED_OUT maps to error."""
        assert map_build_status_to_check("TIMED_OUT") == "error"

    def test_fault(self) -> None:
        """FAULT maps to error."""
        assert map_build_status_to_check("FAULT") == "error"

    def test_stopped(self) -> None:
        """STOPPED maps to error."""
        assert map_build_status_to_check("STOPPED") == "error"

    def test_unknown(self) -> None:
        """Unknown status maps to pending."""
        assert map_build_status_to_check("UNKNOWN") == "pending"
