# Authentication

## Overview

GitGateway uses OAuth 2.0 with JWT tokens for all authentication. The identity provider is configurable — by default it integrates with `idp.botthouse.net` but any OAuth provider that issues RS256-signed JWTs can be used.

## Authentication Methods

### Web UI (Browser)

1. User visits GitGateway and clicks "Sign in with SSO"
2. Redirected to IdP login page
3. After authentication, IdP redirects back with `?token=<jwt>`
4. GitGateway validates the JWT and sets an HMAC-SHA256 signed session cookie
5. Session cookie is HttpOnly, short-lived (1 hour default)

### Git Operations (CLI)

For `git clone`, `git push`, `git pull`:

- **Username**: `__token__`
- **Password**: Your JWT token

Git sends these via HTTP Basic Authentication. The server validates the JWT from the password field.

Example `.gitconfig`:
```ini
[credential "https://git.example.com"]
    username = __token__
    helper = !gitgateway credential-helper
```

### API (Programmatic)

For REST API calls, use Bearer token authentication:

```
Authorization: Bearer <jwt-token>
```

## JWT Claims

The IdP must include these claims in the JWT:

| Claim | Type | Description |
|-------|------|-------------|
| `sub` | string | Username (unique identifier) |
| `iss` | string | Must match `<idp_url>/metadata` |
| `exp` | number | Expiration timestamp |
| `gitgateway-access` | boolean | Whether user has platform access |
| `gitgateway-admin` | boolean | Whether user is a system admin |

## Access Levels

| Level | Permissions |
|-------|------------|
| `readonly` | Clone, fetch, browse repos and PRs |
| `contributor` | Above + push, create PRs, comment, review |
| `admin` | Above + manage settings, delete repos, merge without approval |

## Access Resolution

When a user has access at both group and repo level, the **most permissive** level wins:

- Alice has `readonly` on group `team`
- Alice has `admin` on repo `team/app`
- Result: Alice has `admin` on `team/app`, `readonly` on other repos in `team`
