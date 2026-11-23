# Push 403 findings

## Reproduction
`git push` from linting-api (remote `https://git.botthouse.net/botthouse/linting-api.git`, creds from osxkeychain;
`GITGATEWAY_TOKEN` is not read by git). The `info/refs?service=git-receive-pack` GET succeeds (401 without creds, 200 with),
the pack POST to `/botthouse/linting-api.git/git-receive-pack` gets 403 and git reports `RPC failed; HTTP 403`.

## Evidence
- WAF log group `aws-waf-logs-gitgateway`, last 24 h: 4 BLOCK events, all `POST .../botthouse/linting-api.git/git-receive-pack`,
  `terminatingRuleId=AWSCommonRules`, ruleGroup `AWSManagedRulesCommonRuleSet`, terminating rule `SizeRestrictions_BODY`
  (content-type `application/x-git-receive-pack-request`, user-agent git/2.54.0, authorization header present).
- Lambda log group `/aws/lambda/gitgateway-handler`: no `receive-pack` entries in the window, so the request never reached the Lambda
  (not RBAC, not token ownership).
- Not an API Gateway limit: pack is ~149 KiB. `SizeRestrictions_BODY` rejects bodies over 8 KB.
- Reads pass because fetch POSTs (`git-upload-pack`) and GETs are small.

## Root cause
Infra config: the WAF web ACL added this session runs the AWS Common rule set unmodified, and its 8 KB body-size rule blocks all pushes
larger than 8 KB. Any real push fails.

## Fix (commit on main, not deployed)
`gitgateway/template.yaml`: override `SizeRestrictions_BODY` to Count in the Common rule group, and add rule `BodySizeNonGit`
(priority 3, Block) that applies the same >8192-byte body limit to every path except those ending in `/git-receive-pack` or
`/git-upload-pack`. All other managed rules, KnownBadInputs, rate limit, authn and RBAC are unchanged. cfn-lint passes; full suite
1314 passed, 100% coverage (needs AWS_DEFAULT_REGION and dummy creds set in the environment).
LFS uploads go to S3 by presigned URL, so they never hit this rule.

## Proposed production change (awaiting approval)
Deploy the updated template to stack `gitgateway` with profile `botthouse`, us-east-1, via a CloudFormation change set,
`--disable-kms` (EnableKmsEncryption=false). Expected change set: Modify `WebAcl` (in-place, no replacement) only. After approval,
re-run `git push origin main` in linting-api and confirm 200 from receive-pack. Group `botthouse` admin for user topaz already exists,
so no data or token change is needed.

## Residual risk / notes
- Other Common rules inspect the first 16 KB of the body (e.g. CrossSiteScripting_BODY, GenericRFI_BODY); binary packs could trip them.
  Logs show only SizeRestrictions_BODY so far; if another appears, scope an override the same way.
- WAF logs record the `authorization` header (Basic token). Recommend adding `RedactedFields` for that header on the logging
  configuration; not changed here. The token pasted in the report should be treated as exposed in these logs: rotate it.
- A live push could not be demonstrated without deploying.
