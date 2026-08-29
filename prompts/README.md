# GitGateway Prompts

Shareable steering files that teach Kiro (or any AI assistant) how to use GitGateway from another project.

## Usage

Copy the relevant files into your project's `.kiro/steering/` directory:

```bash
cp gitgateway-cli.md gitgateway-git.md /path/to/your-project/.kiro/steering/
```

Or reference them manually in chat with `#gitgateway-cli` / `#gitgateway-git`.

## Files

| File | Purpose |
|------|---------|
| `gitgateway-cli.md` | How to use the `gitgateway` CLI (repos, groups, access, PRs, deploy) |
| `gitgateway-git.md` | How to authenticate and use the git server (tokens, clone, push, PR workflow) |

## What GitGateway Is

A serverless Git hosting platform built on AWS (S3, Lambda, DynamoDB, API Gateway, CloudFront). It provides:
- Git Smart HTTP hosting with token-based auth
- Pull requests with review, comments, approval, and merge
- 16 automated linters on PRs
- Gemini AI code review
- Web UI at `git.botthouse.net`
- A CLI for scripting repo/group/access/PR management
