# Review: push-403
Verdict: APPROVED.
- Root cause: WAF logs show 4 BLOCKs on POST git-receive-pack by AWSManagedRulesCommonRuleSet/SizeRestrictions_BODY. The Lambda log has no receive-pack entries, so RBAC and token ownership are ruled out. The 149 KiB pack is far below the API Gateway limit. The evidence supports the stated cause.
- Fix: template.yaml counts SizeRestrictions_BODY and adds BodySizeNonGit (priority 3, unique among 0-3). It applies the same 8192-byte limit to every path except those ending in /git-receive-pack and /git-upload-pack. The limit stays enforced elsewhere and the exemption is narrow. The other managed rules, rate limit, auth and RBAC are unchanged. No ALB, no yamllint or cfnlintrc files, no KMS change.
- Verification (commit messages): cfn-lint OK, pytest 1314 passed at 100% coverage. CHANGELOG [Unreleased] updated, then released as 0.2.6. The deploy ran via a change set after user approval. The live push succeeded and the WAF log shows ALLOW with SizeRestrictions_BODY COUNT.
- The token appears only truncated (gg_b073...) in findings.md, so it is not exposed in tracked files.
- Not blockers: the token rotation and the Authorization RedactedFields follow-ups are recorded as unapproved and out of scope.
