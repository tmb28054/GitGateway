# CLI Reference

The `gitgateway` CLI manages all aspects of the platform: deployment, configuration, repositories, groups, access control, and pull requests.

## Installation

```bash
pip install gitgateway
```

## Global Flags

| Flag | Description |
|------|-------------|
| `-v`, `--verbose` | Increase verbosity (repeatable) |

## Commands

### configure

Set up CLI configuration. Config is stored at `~/.gitgateway/config.json`.

```bash
# Set individual values
gitgateway configure --api-url https://git.example.com --region us-east-1

# Import config from a deployed CloudFormation stack
gitgateway configure --from-stack gitgateway --region us-east-1

# Set all values
gitgateway configure \
    --api-url https://git.example.com \
    --idp-url https://idp.botthouse.net \
    --region us-east-1 \
    --domain-name git.example.com
```

| Flag | Description |
|------|-------------|
| `--api-url` | API endpoint URL |
| `--idp-url` | Identity provider URL |
| `--region` | AWS region |
| `--domain-name` | Custom domain name |
| `--from-stack` | Import outputs from a CloudFormation stack |

### deploy

Deploy the GitGateway infrastructure via CloudFormation.

```bash
gitgateway deploy \
    --domain-name git.example.com \
    --acm-certificate-arn arn:aws:acm:us-east-1:123456789012:certificate/abc-123 \
    --region us-east-1 \
    --profile production
```

| Flag | Required | Description |
|------|----------|-------------|
| `--domain-name` | Yes | Custom domain for the service |
| `--acm-certificate-arn` | Yes | ACM certificate ARN for HTTPS |
| `--region` | No | AWS region (default: us-east-1) |
| `--profile` | No | AWS named profile |

### repo create

Create a new repository. Initializes a bare repo in S3 and sets the creator as admin.

```bash
gitgateway repo create myteam/myproject
```

### repo list

List repositories, optionally filtered by group.

```bash
# List all repos
gitgateway repo list

# Filter by group
gitgateway repo list --group myteam
```

### repo delete

Delete a repository and all its S3 storage.

```bash
gitgateway repo delete myteam/old-project
```

### repo fork

Fork a repository into another group.

```bash
gitgateway repo fork myteam/myproject --target alice
```

### group create

Create a new group.

```bash
gitgateway group create myteam
```

### group list

List all groups.

```bash
gitgateway group list
```

### access set

Grant or change access for a user on a group or repository.

```bash
# Group-level access
gitgateway access set alice myteam admin

# Repo-level access
gitgateway access set bob myteam/myproject contributor
```

Access levels: `admin`, `contributor`, `readonly`

### access list

List all access entries for a user.

```bash
gitgateway access list alice
```

### pr create

Create a pull request.

```bash
gitgateway pr create myteam/myproject \
    --source feature/login \
    --target main \
    --title "Add login feature"
```

### pr list

List pull requests for a repository.

```bash
# All PRs
gitgateway pr list myteam/myproject

# Filter by status
gitgateway pr list myteam/myproject --status open
```

### pr merge

Merge a pull request.

```bash
# Default merge commit
gitgateway pr merge myteam/myproject 5

# Squash merge
gitgateway pr merge myteam/myproject 5 --strategy squash

# Fast-forward
gitgateway pr merge myteam/myproject 5 --strategy fast-forward
```

## Configuration Fallback

The CLI uses a three-level fallback for configuration values:

1. **CLI flags** — Highest priority, always used if provided
2. **Config file** — `~/.gitgateway/config.json`
3. **Interactive prompt** — Asked when running in a terminal with no value available

## Authentication

The CLI stores JWT tokens in the system keyring. When a token expires, it automatically redirects to the IdP for re-authentication.

For non-interactive use (CI pipelines), set the `GITGATEWAY_TOKEN` environment variable with a valid JWT.
