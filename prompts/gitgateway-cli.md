# GitGateway CLI

GitGateway is a serverless Git hosting platform (S3, Lambda, DynamoDB, API Gateway, CloudFront) hosted at `git.botthouse.net`. The `gitgateway` CLI manages repos, groups, access control, and pull requests.

Copy this file into your project's `.kiro/steering/` to give Kiro knowledge of the GitGateway CLI.

## Installation

```bash
pip install gitgateway
# or from source:
pip install -e ".[test]"
```

Entry point: `gitgateway` (or `python -m gitgateway.cli`)

## Configuration

Config is stored at `~/.gitgateway/config.json`.

```bash
# Point the CLI at the server
gitgateway configure --api-url https://git.botthouse.net

# Import config from a deployed CloudFormation stack
gitgateway configure --from-stack gitgateway --region us-east-1
```

API URL resolution order: `--api-url` flag → config file → `GITGATEWAY_API_URL` env var.

## Repositories

```bash
gitgateway repo create botthouse/my-app       # create (format: group/name)
gitgateway repo list                          # list all
gitgateway repo list --group botthouse        # filter by group
gitgateway repo delete botthouse/my-app       # delete (removes PRs too)
gitgateway repo fork botthouse/my-app --target my-team
```

## Groups

```bash
gitgateway group create my-team
gitgateway group list
```

## Access Control

Levels: `admin`, `contributor`, `readonly`. Access is evaluated at both group and repo level; most permissive wins.

```bash
gitgateway access set topaz botthouse admin           # group-level
gitgateway access set ci-bot botthouse/my-app contributor  # repo-level
gitgateway access list topaz
```

## Pull Requests

```bash
gitgateway pr create botthouse/my-app \
  --source feature-branch --target main --title "Add feature"

gitgateway pr list botthouse/my-app
gitgateway pr list botthouse/my-app --status open

gitgateway pr merge botthouse/my-app 1                # merge commit
gitgateway pr merge botthouse/my-app 1 --strategy squash
```

Merge strategies: `merge`, `squash`, `fast-forward`.

## Deployment (admins only)

```bash
gitgateway deploy \
  --domain-name git.botthouse.net \
  --acm-certificate-arn arn:aws:acm:us-east-1:711387107691:certificate/... \
  --profile botthouse --region us-east-1
```

## Verbosity

`-v` (INFO), `-vv` (DEBUG). Example: `gitgateway -v repo list`.
