# Architecture Constraints

Hard constraints on the GitGateway platform architecture. Respect these
when proposing or implementing changes.

## No Application Load Balancer (ALB)

**ALB is not an option** for this project. Do not propose, add, or
implement an Application Load Balancer for any purpose (including as a
way to bypass the API Gateway request-size limit for git pushes).

The platform is intentionally serverless and API Gateway / CloudFront
fronted. Introducing an ALB adds always-on cost and infrastructure that
does not fit the design goals.

## API Gateway 10MB Request Limit

- API Gateway HTTP APIs cap the request body at ~10MB. This is a hard
  AWS limit and cannot be raised.
- `git-receive-pack` (push) sends the full pack in one request, so a
  push whose pack exceeds ~10MB fails with HTTP 413.
- Do **not** work around this with an ALB (see above).

### Approved ways to handle large content

1. **Git LFS** — large *files* going forward should be tracked with Git
   LFS. The file bytes go directly to S3 via presigned URLs and never
   enter the git pack. LFS is already wired up and functional.
2. **History rewriting** — repos that already contain large blobs in
   history (making the pack too big to push) should be shrunk with
   `git filter-repo` or `git lfs migrate import` before pushing. This
   rewrites commit SHAs, which is acceptable for restore/import of
   archived repos. Get owner sign-off before rewriting shared history.

### Not approved

- ALB in front of Lambda.
- Any change that removes authentication from git operations.
- Raising limits by moving off API Gateway to another always-on
  ingress.
