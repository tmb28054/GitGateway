# Repository Mirroring

## Overview

GitGateway can automatically push repository changes to external Git hosts (GitHub, GitLab, Bitbucket) after every push. This enables using GitGateway as your primary Git server while maintaining visibility on public platforms.

## Configuration

### Via CLI

```bash
gitgateway mirror set myteam/myproject --target https://github.com/user/repo.git
```

### Via Web UI

Repository Settings → Mirror section → Set target URL

### Via API

```
PUT /api/<group>/<repo>/mirror
Body: {
    "target_url": "https://github.com/user/repo.git",
    "credential_arn": "arn:aws:secretsmanager:...",
    "branches": ["main", "develop"]
}
```

## Credentials

Mirror credentials (SSH key or HTTPS token) are stored in AWS Secrets Manager. The `credential_arn` points to the secret containing the authentication data.

For GitHub, create a personal access token with `repo` scope. For GitLab, use a project access token with `write_repository` permission.

## Branch Filtering

By default, all branches are mirrored. To mirror only specific branches:

```json
{"branches": ["main", "release/*"]}
```

An empty `branches` list means "mirror everything".

## Status Tracking

The mirror configuration tracks:
- `last_sync_time` — When the last push mirror occurred
- `last_sync_status` — "success" or "failure"
- `error_message` — Error details if the last sync failed

## Manual Trigger

Force an immediate sync:

```
POST /api/<group>/<repo>/mirror/sync
```

## Disabling

```
DELETE /api/<group>/<repo>/mirror
```
