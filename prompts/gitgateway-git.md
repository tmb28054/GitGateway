# GitGateway Git Server

GitGateway hosts Git repositories over Smart HTTP at `git.botthouse.net`. All git operations (clone, fetch, push) require authentication.

Copy this file into your project's `.kiro/steering/` to give Kiro knowledge of how to interact with the GitGateway git server.

## Authentication

All git operations require credentials via HTTP Basic auth:

- **Username**: `__token__` (literal)
- **Password**: a Personal Access Token (starts with `gg_`) OR a short-lived JWT

Unauthenticated requests return `401 Unauthorized`.

### Personal Access Tokens

Create tokens via the web UI at `https://git.botthouse.net/tokens` or the API:

```bash
curl -X POST https://git.botthouse.net/api/tokens \
  -H "Content-Type: application/json" \
  -d '{"description": "My laptop", "scope": "read/write", "expiry_days": 365}'
```

Token properties:
- **Scope**: `read` (clone/fetch only) or `read/write` (clone + push)
- **Expiration**: default 1 year, configurable at creation
- Tied to the creating user; inherits that user's repo permissions
- Shown once at creation, stored hashed (SHA-256) server-side
- Revocable anytime from `/tokens`

## Cloning

```bash
git clone https://__token__:gg_your_token_here@git.botthouse.net/group/repo.git
```

## Persisting Credentials

```bash
# Store credentials so you don't repeat the token
git config --global credential.helper store
echo "https://__token__:gg_your_token_here@git.botthouse.net" >> ~/.git-credentials
```

Or use the macOS keychain helper:

```bash
git config --global credential.helper osxkeychain
```

## Common Workflows

### Clone, commit, push

```bash
git clone https://__token__:TOKEN@git.botthouse.net/my-team/my-app.git
cd my-app
# ... make changes ...
git add .
git commit -m "My changes"
git push origin main
```

### Feature branch + pull request

```bash
git checkout -b feature
# ... make changes, commit ...
git push origin feature
# then create a PR (see gitgateway-cli.md) or use the web UI
```

## Automated Linting & AI Review

When a PR is opened (or its source branch is pushed), GitGateway automatically:
- Runs 16 linters (pylint, bandit, ruff, mypy, yamllint, json, toml, checkov, cfn-lint, tflint, kubeval, shellcheck, hadolint, markdownlint, eslint, sqlfluff)
- Runs a Gemini AI code review posting actionable WHAT/WHY/HOW feedback
- Posts per-linter status checks (✓/✗) on the PR

Results appear on the PR detail page. Push fixes to the source branch to re-trigger.

## Notes

- Pushing to a repo you have `read` scope on returns `403 Forbidden`.
- The server is NOT open to the world — every operation is authenticated.
- Web UI: browse repos, PRs, commits, and branches at `https://git.botthouse.net`.
