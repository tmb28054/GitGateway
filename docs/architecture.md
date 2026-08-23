# Architecture

## Overview

GitGateway is a fully serverless Git hosting platform running on AWS. It replaces self-hosted GitLab with a minimal-cost, zero-maintenance alternative designed for personal and small-team use.

## Design Principles

1. **Serverless-first** — No servers to manage, pay only for what you use.
2. **Standard Git compatibility** — Vanilla `git clone/push/pull` work unchanged for repos under 20MB packed.
3. **Security by default** — All data encrypted at rest (KMS), OAuth-only authentication, least-privilege IAM.
4. **Opt-in complexity** — AI features, SAST, and mirroring are all configurable per repository.

## Component Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Client Layer                             │
├─────────┬──────────┬───────────────┬──────────────┬─────────────┤
│ git CLI │ git-lfs  │ gitgateway    │ gitgateway   │ Web Browser │
│         │          │ remote helper │ CLI          │             │
└────┬────┴────┬─────┴───────┬───────┴──────┬───────┴──────┬──────┘
     │         │             │              │              │
     ▼         ▼             ▼              ▼              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    CloudFront + ACM Domain                       │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                    API Gateway HTTP API                          │
│  Routes:                                                         │
│    /<group>/<repo>.git/info/lfs/* → LFS Handler                 │
│    /<group>/<repo>.git/*          → Git Protocol Handler        │
│    /api/*                         → REST API Handler            │
│    /auth/*                        → OAuth Handler               │
│    /*                             → Web UI Handler              │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                       Lambda Functions                           │
├──────────────┬──────────────┬──────────────┬────────────────────┤
│ Git Handler  │ API/Web UI   │ Webhook      │ Mirror/Notify/AI   │
│ (git-http-   │ Handler      │ Dispatcher   │ Handlers           │
│  backend)    │              │              │                    │
└──────┬───────┴──────┬───────┴──────┬───────┴────────┬───────────┘
       │              │              │                │
       ▼              ▼              ▼                ▼
┌────────────┐ ┌────────────┐ ┌──────────┐ ┌─────────────────────┐
│ S3 Buckets │ │ DynamoDB   │ │ CodeBuild│ │ External Services   │
│ - repos    │ │ - repos    │ │ (SAST)   │ │ - IdP (OAuth)       │
│ - lfs      │ │ - access   │ │          │ │ - Gemini (AI)       │
│            │ │ - prs      │ │          │ │ - GitHub (mirror)   │
│            │ │ - comments │ │          │ │ - SES (email)       │
│            │ │ - webhooks │ │          │ │                     │
│            │ │ - status   │ │          │ │                     │
│            │ │ - audit    │ │          │ │                     │
│            │ │ - notifs   │ │          │ │                     │
│            │ │ - embeds   │ │          │ │                     │
└────────────┘ └────────────┘ └──────────┘ └─────────────────────┘
```

## Data Flow

### Git Clone/Fetch (Read)

1. Client sends `GET /<group>/<repo>.git/info/refs?service=git-upload-pack`
2. API Gateway routes to Lambda git handler
3. Lambda authenticates (JWT via Basic auth or Bearer token)
4. Lambda checks access level (readonly sufficient for fetch)
5. Lambda syncs bare repo from S3 to /tmp
6. Lambda invokes `git-http-backend` as CGI subprocess
7. Response streamed back through API Gateway
8. If response > 20MB, signals client helper via `X-GitGateway-LargeTransfer` header

### Git Push (Write)

1. Client sends `POST /<group>/<repo>.git/git-receive-pack`
2. Authentication + permission check (contributor or admin required)
3. Branch protection rules evaluated (required approvals, status checks)
4. Bare repo synced from S3 → /tmp
5. `git-http-backend` processes the push
6. Changed files synced back /tmp → S3
7. Post-push events fired: audit log, webhooks, mirror trigger, AI indexing

### Pull Request Workflow

1. User creates PR via API or Web UI
2. DynamoDB atomic counter assigns PR number
3. If SAST enabled: CodeBuild triggered with SAST buildspec
4. If AI enabled: Gemini generates PR summary and advisory review
5. Reviewers add inline comments, approve/request changes
6. Status checks gate merge (required checks must pass)
7. On merge: git merge in /tmp, sync to S3, fire webhooks, update mirror

## DynamoDB Table Design

All tables use a composite primary key (PK + SK) for flexible access patterns:

| Table | PK Pattern | SK Pattern | Purpose |
|-------|-----------|------------|---------|
| repos | `GROUP#<group>` | `REPO#<name>` or `METADATA` or `MIRROR#<repo>` or `FORK#<repo>` | Repository metadata, groups, mirrors, forks |
| access | `USER#<username>` | `GROUP#<group>` or `REPO#<group>/<repo>` | Access control entries |
| prs | `REPO#<group>/<repo>` | `PR#<number>` or `COUNTER` | Pull requests and number counter |
| comments | `PR#<group>/<repo>/<number>` | `COMMENT#<ts>#<id>` or `REVIEW#<ts>#<id>` | Comments and reviews |
| webhooks | `REPO#<group>/<repo>` | `HOOK#<id>` or `DELIVERY#<hook>#<ts>#<id>` | Webhook configs and delivery log |
| status-checks | `PR#<group>/<repo>/<number>` | `CHECK#<name>` | CI status checks |
| audit | `REPO#<resource>` | `EVENT#<ts>#<id>` | Audit trail (TTL-enabled) |
| notifications | `USER#<username>` | `NOTIF#<ts>#<id>` or `PREFERENCES` | In-app notifications and prefs |
| embeddings | `REPO#<group>/<repo>` | `FILE#<path>#<chunk>` | Code embeddings for search |

## Security Model

- **Authentication**: OAuth 2.0 via configurable IdP (JWT with RS256 signing)
- **Authorization**: Three-level RBAC (admin/contributor/readonly) at group and repo level
- **Encryption**: S3 buckets use KMS server-side encryption; DynamoDB encrypted at rest by default
- **Branch protection**: Configurable per branch — required approvals, no force-push, linear history, required status checks
- **Secrets**: IdP config, mirror credentials, and Gemini API key stored in AWS Secrets Manager
- **Audit**: All state-changing operations logged with actor, timestamp, IP, and auto-expiring TTL
