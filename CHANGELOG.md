# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Comprehensive project documentation across all docs/ files.
- Steering rule for verbose documentation standards.

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
