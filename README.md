# GitGateway

A serverless Git hosting platform backed by AWS S3, API Gateway, Lambda, and DynamoDB.

## Overview

GitGateway replaces self-hosted GitLab with a minimal, serverless alternative for personal and small-team use. It provides:

- **Git Smart HTTP protocol** — Standard `git clone`, `push`, `pull` work unchanged
- **Pull Requests** — Full review workflow with inline comments, suggestions, and approvals
- **Access Control** — Group and repo-level permissions (admin/contributor/readonly)
- **OAuth Authentication** — Configurable IdP with JWT-based authorization
- **Built-in SAST** — Semgrep, Bandit, pip-audit, Trivy, Checkov, yamllint, check-jsonschema
- **AI Code Intelligence** — Gemini-powered PR summaries, code review, and semantic search
- **Git LFS** — Large file storage via S3 presigned URLs
- **Repository Mirroring** — Push to GitHub/GitLab automatically
- **Web UI** — HTMX + Jinja2 server-rendered interface
- **CLI** — Full management tool and git remote helper

## Installation

```bash
pip install gitgateway
```

For development:

```bash
git clone https://github.com/tmb28054/gitgateway.git
cd gitgateway
pip install -e ".[test]"
```

## Quick Start

```bash
# Deploy infrastructure
gitgateway deploy --domain-name git.example.com --acm-certificate-arn arn:aws:acm:...

# Configure CLI
gitgateway configure --from-stack gitgateway

# Create a group and repo
gitgateway group create myteam
gitgateway repo create myteam/myproject

# Clone and work
git clone https://git.example.com/myteam/myproject.git
```

## Usage

See the [CLI Reference](docs/cli.md) for all available commands.

## Running Tests

```bash
# All tests with coverage
pytest --cov=gitgateway --cov-fail-under=100

# Smoke tests only
pytest -m smoke

# Linting
pylint gitgateway/
bandit -r gitgateway/
cfn-lint gitgateway/template.yaml
yamllint gitgateway/template.yaml
```

## Architecture

GitGateway uses a fully serverless architecture on AWS:

- **API Gateway HTTP API** — Routes requests to Lambda functions
- **Lambda** — Git protocol handler, API/Web UI, webhooks, CodeBuild, mirror, notifications, AI
- **S3** — Bare repository storage, LFS objects, large packfiles, static assets
- **DynamoDB** — All reference data (repos, access, PRs, comments, audit, etc.)
- **CloudFront** — CDN with custom domain and ACM certificate
- **CodeBuild** — SAST pipeline and CI integration
- **SES** — Email notifications
- **Secrets Manager** — IdP config, mirror credentials, Gemini API key

## License

MIT License - see [LICENSE](LICENSE) for details.

