# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.2] - 2026-09-07

### Fixed
- `GET /api/access?group=<name>` raised a DynamoDB `ValidationException`
  because the filter expression referenced the reserved word `resource`
  directly; it now aliases it via `ExpressionAttributeNames`.
- `audit.get_user_audit_log()` had the same latent issue with the reserved
  word `actor` in its scan filter; it is now aliased as well.

## [0.2.1] - 2026-09-07

### Added
- Admin audit log viewer at `/admin/audit` that lists audit events across all
  resources (newest first) with time, actor, action, resource, IP address, and
  details, plus a client-side text filter.
- `audit.list_all_events()` to fetch audit events across every resource for
  admin views, with pagination and a configurable limit.

### Fixed
- `/admin/audit` previously rendered a bare `Path: /admin/audit` placeholder
  because the route was never dispatched; it now renders the full audit log.
- Token API routes (`/api/tokens`) treated the `"unknown"` session sentinel as
  a real username, so their 401 checks were dead code and an unauthenticated
  caller could create/list/revoke tokens as user "unknown". `_get_request_username`
  now returns an empty string for the sentinel, activating the intended 401s.

### Changed
- Enforced 100% line and branch coverage across the whole `gitgateway`
  package. The coverage gate now runs on every `pytest` invocation via
  `addopts` in `pyproject.toml` (`--cov=gitgateway --cov-report=term-missing
  --cov-fail-under=100 --ignore=tests/e2e`), and the suite is at 100%.
  Brought previously-thin modules up to full coverage with new/expanded
  tests: `git_backend.py` (5%->100%, new `tests/test_git_backend.py` driving
  the Smart-HTTP info/refs, upload-pack, receive-pack, indexing, and
  report-status paths against real Dulwich bare repos + moto S3/DynamoDB),
  `deploy.py` (38%->100%, Lambda/layer packaging, binary + Node tool
  extraction, bucket/layer publish via moto and mocked downloads), `lint.py`
  (76%->100%, per-linter parse/error/tool-missing branches), and `cli.py`,
  `db.py`, `storage.py`, `tokens.py`.
- Moved the `partial_branches` coverage setting from `[tool.coverage.run]`
  (where it was ignored) to `[tool.coverage.report]`.
- Raised `lambda_handler.py` test coverage from 19% to 100% (line and branch)
  by adding `tests/test_lambda_handler_routes.py` covering the API routes
  (groups, repos, branches, PRs, comments, reviews, merge, tokens, access,
  users, admin AI key, rescan), the LFS handler, git auth, event parsing, the
  OAuth callback, PR linting/AI-review orchestration, and the web render pages
  (dashboard, repo view, file viewer, commits, branches, PR detail, real
  per-file and commit diffs) against moto-mocked DynamoDB and S3. Remaining
  gaps were closed with tests for malformed-path routing legs, non-admin
  permission legs, repo-not-found and empty-state branches, and AI/lint
  early-return paths; genuinely unreachable path-split guards and one
  defensive parent-lookup handler were marked `# pragma: no branch`/`no cover`
  with explanatory comments.
- Replaced silent `try/except/pass` blocks in `db.py` (best-effort repo
  cleanup) and `lambda_handler.py` (post-push linting trigger) with debug
  logging, resolving bandit B110 findings so the bandit smoke test passes.
- Wrapped two over-length lines in `ai.py` so the pylint smoke test passes.
- Updated stale tests that no longer matched current behavior: version test
  now derives the expected version from CHANGELOG.md; `AIConfig` test asserts
  the current default model; `storage` init-failure test patches the dulwich
  backend instead of the removed `subprocess` call; `cli` tests mock the API
  client instead of hitting the network and assert real request routing;
  `lambda_handler` handler/dispatch tests assert current auth/routing behavior
  (401 for unauthenticated git, 400 for auth callback without a token, IdP
  redirect for unauthenticated web) instead of removed stub responses.

## [0.2.0] - 2026-08-21

### Added
- Lambda layers for dependency management: core layer (dulwich, jinja2, pygments, pyjwt, cryptography, requests, markdown), linting layer (pylint, bandit, yamllint, checkov, cfn-lint, ruff, mypy, sqlfluff), and binary tools layer (shellcheck, hadolint, tflint, kubeconform) deployed as separate Lambda LayerVersions.
- Checkov IaC scanner integrated into PR linting pipeline for CloudFormation, Terraform, and Kubernetes manifest scanning with per-check status reporting (lint/checkov).
- cfn-lint integration for CloudFormation template schema validation (lint/cfn-lint).
- Ruff fast Python linter for complementary checks beyond pylint (lint/ruff).
- mypy type checker integration for Python files (lint/mypy).
- TOML syntax validation for pyproject.toml and similar files (lint/toml).
- tflint integration for Terraform-specific linting (lint/tflint).
- kubeval/kubeconform integration for Kubernetes manifest schema validation (lint/kubeval).
- shellcheck integration for shell script analysis (lint/shellcheck).
- hadolint integration for Dockerfile best practices (lint/hadolint).
- markdownlint (mdl) integration for Markdown formatting checks (lint/markdownlint).
- eslint integration for JavaScript/TypeScript linting (lint/eslint).
- sqlfluff integration for SQL linting (lint/sqlfluff).
- File categorization helpers: `_is_cfn_file()`, `_is_k8s_file()`, `_is_shell_file()`, `_is_dockerfile()` in lint.py.
- `_package_layer()` helper in deploy.py for building Lambda layer ZIPs with pip dependencies targeting manylinux2014_x86_64.
- `_package_binary_layer()` helper that downloads precompiled Linux x86_64 binaries (shellcheck, hadolint, tflint, kubeconform) and packages them into a Lambda layer at /opt/bin.
- `_install_node_tools()` helper that downloads Node.js 20.x runtime and installs eslint and markdownlint-cli2 via npm into the binary tools layer.
- `_publish_layer()` helper to publish layer versions via boto3 Lambda API.
- CloudFormation template parameters `CoreLayerArn`, `LintingLayerArn`, and `BinariesLayerArn` with conditional attachment to the Lambda function.

### Changed
- Lambda deployment refactored: `package_lambda()` now produces a code-only ZIP (no dependencies), with all runtime deps split into three Lambda layers (core, linting, binaries).
- `deploy_stack()` now builds and publishes all three layers before deploying the CloudFormation stack.
- `_run_pr_linting()` refactored to use `_run_and_post()` helper pattern, reducing code duplication across 16 linters.
- Binary tools (shellcheck, hadolint, tflint, kubeconform, eslint, markdownlint-cli2) now available in Lambda via precompiled layer with embedded Node.js runtime for JS-based tools.

- GitHub-style repository view with Code/Pull Requests tabs, clone URL box, branch selector dropdown, and file tree from DynamoDB cache showing last commit info per file.
- File viewer with Pygments syntax highlighting, line numbers, copy button, and breadcrumb navigation when clicking files in the repo tree.
- Repository settings page (admin-only) with default branch override, description editing, and Danger Zone with delete repository button.
- Branch switching with dropdown populated from DynamoDB branch entries, different branches show different file trees, branches API for registering branches.
- Admin Access Control page with grant form (known users dropdown + free-text username + group select + level select) and revoke button with confirmation.
- Admin navigation link visible on all pages for admin users, pointing to /admin hub with links to Manage Groups and Access Control.
- View Groups page (/groups) showing only groups the authenticated user has been granted access to.
- Group detail page (/groups/<name>) with repository listing and create repository form for contributors and admins.
- Comprehensive project documentation across all docs/ files (architecture, CLI, API, deployment, configuration, authentication, PRs, SAST, AI, LFS, mirroring, FAQ).
- Steering rule for verbose and comprehensive documentation standards.
- Playwright end-to-end integration tests for login flow, admin groups, admin access control, nav admin link, view groups, group repos, repo view, file viewer, repo settings, and branch switching.
- Full deployment pipeline: Lambda code packaging with pip dependencies for Linux, S3 upload, CloudFormation stack with Lambda function, API Gateway integration/routes/stage, IAM execution role, and 10GB /tmp.
- Working OAuth flow: unauthenticated visitors redirected to IdP, callback sets session cookie, authenticated users see the web UI with nav bar (username + logout + admin link for admins).
- REST API endpoints for groups CRUD, repos CRUD, access control grant/revoke/list, repo settings PATCH, branches register/list, known users list.
- CLI wired to make real HTTP API calls against deployed GitGateway (group/repo/access/PR management).
- IdP programmatic login helper with unicode math challenge support (×, ÷).

### Fixed
- Unicode multiplication (×) and division (÷) signs in IdP math challenges now handled correctly in all test helpers.
- CloudFront origin request policy forwarding cookies to Lambda for session-based auth.
- API Gateway v2 cookies array parsed into request headers for session detection.
- Branch dropdown routing order fixed so /branches endpoints match before generic repo POST.

## [0.1.0] - 2025-08-21

### Added
- Project scaffolding with hatchling build system, version extracted from CHANGELOG.md, pytest configuration with 100% coverage enforcement, pylint and bandit smoke tests.
- Configuration management module (`~/.gitgateway/config.json`) with load, save, merge semantics, and config fallback pattern (CLI flags → config file → interactive prompt).
- DynamoDB data models for repositories, groups, and access control with full CRUD operations. Access levels (admin/contributor/readonly) at both group and repo level, with most-permissive-wins resolution logic.
- Branch protection rules with configurable required approvals, force-push blocking, linear history enforcement, and required status checks.
- OAuth authentication via configurable identity provider (idp.botthouse.net) with RS256 JWT validation, Basic auth for git operations (username `__token__`, password = JWT), HMAC-SHA256 signed session cookies for the web UI.
- Git Smart HTTP protocol handler implementing reference discovery, git-upload-pack (fetch/clone), and git-receive-pack (push) via `git-http-backend` CGI subprocess invocation with proper environment variable construction.
- S3 repository storage layer with bare repo initialization, bidirectional sync between Lambda /tmp and S3, paginated object listing, server-side S3 copy for forking, and optimistic locking support.
- Git LFS Batch API support generating presigned S3 URLs for upload (PUT) and download (GET) operations, with object existence verification and file locking API (create, list, verify, delete with force option).
- Lambda function entry point with API Gateway v2 HTTP API request parsing, regex-based path routing (LFS → Git → API → Auth → Web), CORS support, and response serialization (including base64 for binary payloads).
- Pull request lifecycle management with auto-incrementing PR numbers (DynamoDB atomic counter), status transitions (open → merged/closed), merge strategies (fast-forward, merge commit, squash), and fork PR support (cross-repo source).
- Unified diff generation by invoking `git diff` subprocess on bare repos, with full parser for the unified diff format supporting added/modified/deleted/renamed/binary files, hunk extraction with line numbers, and commit log retrieval.
- PR review system with inline comments (file path, line number, side), threaded discussions with resolution (permission-checked), review submissions (approved/changes_requested/commented), code suggestions, and approval counting for merge gating.
- Webhook event system with HMAC-SHA256 payload signing, configurable event subscriptions (push, PR events, review events, branch protection changes), delivery logging with attempt tracking, and async Lambda dispatch.
- Generic status checks API allowing external CI systems to post pass/fail status on PRs, with merge blocking when required checks haven't passed. Built-in AWS CodeBuild integration for triggering builds with environment variable and buildspec overrides.
- Built-in SAST pipeline orchestrating seven security and validation tools (Semgrep, Bandit, pip-audit, Trivy, Checkov, yamllint, check-jsonschema) via a generated CodeBuild buildspec, with JSON output parsers for each tool and severity-based pass/fail evaluation (blocks on HIGH/CRITICAL).
- Audit logging system recording all state-changing operations with actor, action, resource, details (JSON), IP address, and configurable TTL-based auto-expiration. Includes a decorator pattern for easy integration and filtering by resource or user.
- Email notification system via SES with in-app notifications stored in DynamoDB, user preference management (per-type disable), unread tracking with batch mark-read, and HTML + plain text email template rendering.
- Repository mirroring to external Git hosts (GitHub, GitLab) with per-repo configuration (target URL, credential ARN from Secrets Manager, branch filtering), sync status tracking (last time, status, error message), and manual trigger support.
- Repository forking with server-side S3 copy (no data download/upload), bidirectional metadata linking (fork knows its parent, parent knows its forks), and support for cross-repo pull requests from forks.
- AI-powered PR summarization and advisory code review using Google Gemini API, with rate limiting per repository (configurable calls/hour), opt-in per repo, and all AI output posted as the `gitgateway-ai` bot user clearly marked as AI-generated.
- AI SAST triage classifying findings as likely true/false positives with confidence scores and reasoning, plus semantic code search using Gemini embeddings (text-embedding-004) with cosine similarity nearest-neighbor retrieval over DynamoDB-stored code chunks.
- HTMX + Jinja2 server-rendered web UI with base layout (navigation, notification badge), OAuth login page, repository dashboard grouped by team, file browser with branch selector, and error pages.
- Pull request web interface with filterable PR list (open/merged/closed with counts), PR detail page showing status checks, review list, merge button (disabled when checks failing or approvals insufficient), and diff view link.
- Admin web pages for group management (create/delete with member counts), access control (user/resource/level table with revoke action), audit log viewer (searchable timeline), and repository settings (description, AI/SAST toggles, branch protections, mirror status).
- CLI tool (`gitgateway`) with subcommands: configure (--api-url, --idp-url, --region, --domain-name, --from-stack), repo create/list/delete/fork, group create/list, access set/list, pr create/list/merge (with --strategy), and deploy (--domain-name, --acm-certificate-arn required).
- Git remote helper (`git-remote-gitgateway`) for transparent large packfile transfers via S3 when server responses exceed Lambda streaming limits, with passthrough mode for normal-sized operations.
- CloudFormation template deploying the full infrastructure: 2 KMS-encrypted S3 buckets, 9 DynamoDB tables (including TTL-enabled audit table), HTTP API Gateway, CloudFront distribution with custom domain and ACM certificate. Required parameters: DomainName, ACMCertificateARN. Optional: EnableCodeBuild, EnableAI.
- Deployment module following the s3pypi pattern with CloudFormation waiters, create/update detection, "no updates needed" graceful handling, and output-to-config key mapping.
