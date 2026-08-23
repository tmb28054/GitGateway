"""E2E tests: Basic health and connectivity checks.

These tests verify that the deployed GitGateway instance is
reachable and responds correctly to basic requests.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


@pytest.mark.e2e
class TestHealthCheck:
    """Verify the deployed service is reachable."""

    def test_root_returns_html(self, page: Page, base_url: str) -> None:
        """Root URL returns an HTML page (login or dashboard)."""
        response = page.goto(base_url)
        assert response is not None
        assert response.status in (200, 302, 301)

    def test_login_page_renders(self, page: Page, base_url: str) -> None:
        """Login page renders with SSO button."""
        page.goto(f"{base_url}/auth/login")
        # Should have a sign-in link or form
        page.wait_for_load_state("domcontentloaded")
        content = page.content()
        assert "GitGateway" in content or "Sign in" in content

    def test_api_returns_json(self, page: Page, base_url: str) -> None:
        """API endpoint returns JSON response."""
        response = page.goto(f"{base_url}/api/groups")
        assert response is not None
        # Should return 401 (unauthorized) or 200 with JSON
        assert response.status in (200, 401, 403)
        content_type = response.headers.get("content-type", "")
        assert "json" in content_type or response.status in (401, 403)

    def test_git_info_refs_requires_auth(
        self, page: Page, base_url: str,
    ) -> None:
        """Git info/refs endpoint requires authentication."""
        response = page.goto(
            f"{base_url}/test/repo.git/info/refs"
            "?service=git-upload-pack",
        )
        assert response is not None
        # Should return 401 or 403 without auth
        assert response.status in (401, 403, 404)

    def test_lfs_batch_requires_auth(
        self, page: Page, base_url: str,
    ) -> None:
        """LFS batch endpoint requires authentication."""
        response = page.goto(
            f"{base_url}/test/repo.git/info/lfs/objects/batch",
        )
        assert response is not None
        assert response.status in (401, 403, 404, 405)


@pytest.mark.e2e
class TestCORS:
    """Verify CORS headers are present on API responses."""

    def test_options_returns_cors(self, page: Page, base_url: str) -> None:
        """OPTIONS request returns CORS headers."""
        # Use page.evaluate to make a fetch request with OPTIONS
        result = page.evaluate(
            """async (url) => {
                const response = await fetch(url + '/api/groups', {
                    method: 'OPTIONS'
                });
                return {
                    status: response.status,
                    cors: response.headers.get('access-control-allow-origin')
                };
            }""",
            base_url,
        )
        # CORS should be present (204 for preflight)
        assert result["status"] in (200, 204)
        assert result["cors"] is not None
