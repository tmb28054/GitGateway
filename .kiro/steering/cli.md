---
inclusion: manual
---

# GitGateway CLI Reference

The `gitgateway` CLI manages all aspects of the platform: repos, groups, access control, pull requests, and deployment.

## Installation

```bash
pip install -e ".[test]"
```

Entry point: `gitgateway` (or `python -m gitgateway.cli`)

## Configuration

```bash
# Interactive setup
gitgateway configure --api-url https://git.botthouse.net --domain-name git.botthouse.net

# Import from deployed stack
gitgateway configure --from-stack gitgateway --region us-east-1
```

Config is stored at `~/.gitgateway/config.json`.

## Repository Management

```bash
# Create a repo (format: group/name)
gitgateway repo create botthouse/my-app

# List repos (optionally filter by group)
gitgateway repo list
gitgateway repo list --group botthouse

# Delete a repo
gitgateway repo delete botthouse/my-app

# Fork a repo to another group
gitgateway repo fork botthouse/my-app --target my-team
```

## Group Management

```bash
# Create a group
gitgateway group create my-team

# List all groups
gitgateway group list
```

## Access Control

```bash
# Grant access (levels: admin, contributor, readonly)
gitgateway access set topaz botthouse admin
gitgateway access set ci-bot botthouse/my-app contributor

# List a user's access
gitgateway access list topaz
```

Access is evaluated at both group and repo level. The most permissive grant wins.

## Pull Requests

```bash
# Create a PR
gitgateway pr create botthouse/my-app \
  --source feature-branch \
  --target main \
  --title "Add new feature"

# List PRs (optionally filter by status)
gitgateway pr list botthouse/my-app
gitgateway pr list botthouse/my-app --status open

# Merge a PR (strategies: merge, squash, fast-forward)
gitgateway pr merge botthouse/my-app 1
gitgateway pr merge botthouse/my-app 1 --strategy squash
```

## Deployment

```bash
# Deploy the full stack
gitgateway deploy \
  --domain-name git.botthouse.net \
  --acm-certificate-arn arn:aws:acm:us-east-1:123:certificate/abc \
  --profile botthouse \
  --region us-east-1
```

This packages the Lambda, builds layers, uploads to S3, and deploys the CloudFormation stack.

## Git Operations (using tokens)

```bash
# Clone with a Personal Access Token
git clone https://__token__:gg_your_token_here@git.botthouse.net/group/repo.git

# Or configure credentials globally
git config --global credential.helper store
echo "https://__token__:gg_your_token_here@git.botthouse.net" >> ~/.git-credentials
```

Tokens are created via the web UI at `/tokens` or the API:
```bash
curl -X POST https://git.botthouse.net/api/tokens \
  -H "Content-Type: application/json" \
  -d '{"description": "My laptop", "scope": "read/write", "expiry_days": 365}'
```

## Common Workflows

### Set up a new project

```bash
gitgateway group create my-team
gitgateway repo create my-team/my-app
gitgateway access set topaz my-team admin
git clone https://__token__:TOKEN@git.botthouse.net/my-team/my-app.git
```

### Create a PR from the CLI

```bash
git checkout -b feature
# ... make changes, commit, push ...
git push origin feature
gitgateway pr create my-team/my-app --source feature --target main --title "My feature"
```

### Review and merge

```bash
gitgateway pr list my-team/my-app --status open
# Review on web UI, then merge:
gitgateway pr merge my-team/my-app 1
```

## Verbosity

Add `-v` for more output:

```bash
gitgateway -v repo list
gitgateway -vv deploy --domain-name git.botthouse.net ...
```

## API URL Resolution

The CLI resolves the API URL in this order:
1. `--api-url` flag (if provided)
2. `~/.gitgateway/config.json` (`api_url` field)
3. `GITGATEWAY_API_URL` environment variable
