# Configuration Reference

## CLI Configuration File

Location: `~/.gitgateway/config.json`

```json
{
  "api_url": "https://git.example.com",
  "domain_name": "git.example.com",
  "idp_url": "https://idp.botthouse.net",
  "region": "us-east-1"
}
```

| Key | Description | Default |
|-----|-------------|---------|
| `api_url` | GitGateway API endpoint URL | — |
| `domain_name` | Custom domain for the service | — |
| `idp_url` | Identity provider base URL | — |
| `region` | AWS region | us-east-1 |
| `profile` | AWS named profile | — |
| `client_id` | OAuth client ID | gitgateway |

## CloudFormation Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `DomainName` | Yes | — | Custom domain for CloudFront |
| `ACMCertificateARN` | Yes | — | ACM certificate for HTTPS |
| `StackNamePrefix` | No | gitgateway | Prefix for all resource names |
| `EnableCodeBuild` | No | true | Enable CodeBuild for SAST |
| `EnableAI` | No | false | Enable Gemini AI features |

## Repository Settings

Per-repository settings managed via API or Web UI:

| Setting | Default | Description |
|---------|---------|-------------|
| `ai_enabled` | false | Enable AI PR summaries and code review |
| `sast_enabled` | true | Enable SAST scanning on PRs |
| `default_branch` | main | Default branch name |

## Branch Protection Options

| Option | Default | Description |
|--------|---------|-------------|
| `required_approvals` | 0 | Number of approvals needed before merge |
| `force_push_allowed` | false | Whether force-push is permitted |
| `require_linear_history` | false | Enforce linear commit history |
| `required_status_checks` | [] | Check names that must pass before merge |

## Environment Variables

| Variable | Used By | Description |
|----------|---------|-------------|
| `GITGATEWAY_TOKEN` | CLI | JWT token for non-interactive auth |
| `AWS_PROFILE` | CLI, Deploy | AWS named profile |
| `AWS_DEFAULT_REGION` | CLI, Deploy | AWS region |
