# API Reference

Base URL: `https://git.example.com/api`

All API endpoints require authentication via `Authorization: Bearer <token>` header. Responses are JSON.

## Repositories

### Create Repository
```
POST /api/<group>/<repo>
```

### List Repositories
```
GET /api/repos?group=<group>
```

### Delete Repository
```
DELETE /api/<group>/<repo>
```

## Groups

### Create Group
```
POST /api/groups
Body: {"name": "myteam", "description": "My team"}
```

### List Groups
```
GET /api/groups
```

## Access Control

### Set Access
```
PUT /api/access/<username>/<resource>
Body: {"level": "contributor"}
```

### List User Access
```
GET /api/access/<username>
```

### Revoke Access
```
DELETE /api/access/<username>/<resource>
```

## Pull Requests

### Create PR
```
POST /api/<group>/<repo>/pulls
Body: {"title": "...", "source_branch": "...", "target_branch": "main"}
```

### List PRs
```
GET /api/<group>/<repo>/pulls?status=open
```

### Get PR Detail
```
GET /api/<group>/<repo>/pulls/<number>
```

### Merge PR
```
POST /api/<group>/<repo>/pulls/<number>/merge
Body: {"strategy": "squash"}
```

### Get PR Diff
```
GET /api/<group>/<repo>/pulls/<number>/diff
```

## Comments and Reviews

### Add Comment
```
POST /api/<group>/<repo>/pulls/<number>/comments
Body: {"body": "...", "file_path": "app.py", "line_number": 10, "side": "new"}
```

### Create Review
```
POST /api/<group>/<repo>/pulls/<number>/reviews
Body: {"status": "approved", "body": "LGTM"}
```

## Status Checks

### Post Status
```
POST /api/<group>/<repo>/pulls/<number>/statuses
Body: {"name": "ci/tests", "status": "success", "description": "All passed"}
```

### List Statuses
```
GET /api/<group>/<repo>/pulls/<number>/statuses
```

## Webhooks

### Register Webhook
```
POST /api/<group>/<repo>/hooks
Body: {"url": "https://ci.example.com/hook", "events": ["push", "pr.opened"]}
```

### List Webhooks
```
GET /api/<group>/<repo>/hooks
```

### Delete Webhook
```
DELETE /api/<group>/<repo>/hooks/<hook_id>
```

## Search

### Semantic Code Search
```
GET /api/<group>/<repo>/search?q=<natural language query>
```

## Audit

### Repository Audit Log
```
GET /api/<group>/<repo>/audit?start_time=2025-01-01T00:00:00Z
```

### System Audit (Admin Only)
```
GET /api/admin/audit
```
