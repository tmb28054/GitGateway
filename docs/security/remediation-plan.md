# GitGateway — Security Review Remediation Plan

**In response to:** AWS SecAgent review (`GitGateway-comments-2026-09-27.csv`)
**Prepared by:** Topaz Bott
**Date:** 2026-09-27
**Status:** Proposed — pending owner sign-off

---

## 1. How This Plan Was Built

The SecAgent review evaluated the security *design document* against ~90
controls drawn from PCI DSS, NIST CSF, and AWS Well-Architected. Each control
came back as `COMPLIANT`, `NON_COMPLIANT`, `INSUFFICIENT_DATA`, or
`NOT_APPLICABLE`.

Not every finding is a real defect. We triaged all results into three buckets:

1. **Real defects** — the system (code or CloudFormation) actually behaves
   insecurely. These were confirmed by reading the source, not just the design
   doc. Highest priority.
2. **Documentation gaps** — the *design* is acceptable but the document did not
   describe a control the reviewer expected (most `INSUFFICIENT_DATA` results).
   Addressed by writing/expanding docs and, where a control genuinely doesn't
   exist yet, by adding it.
3. **Not applicable / already compliant** — no action. A large share of the
   `NOT_APPLICABLE` results are PCI cardholder-data controls (CDE segmentation,
   POI devices, PAN masking, SAD retention, wireless). GitGateway stores source
   code and repository metadata and processes **no cardholder data**, so these
   correctly do not apply. See §7 for the full dismissal list and rationale.

The remainder of this plan covers buckets 1 and 2, grouped by theme and ordered
by priority.

---

## 2. Priority 0 — Authorization & Tenant Isolation (the core defect)

This single root cause drives the largest cluster of `NON_COMPLIANT` results:
least-privilege/default-deny, tenant isolation, multi-tenant isolation,
authenticated network communication, security-design-principles integration,
data-at-rest access control, and protection of security-posture artifacts. Fix
this first — it closes the majority of the real risk.

### R-1 — Enforce `check_permission` on the git/LFS data plane *(fixes F-4, Critical)*

**Problem (verified):** `check_permission` is called only in web-UI render
functions (`lambda_handler.py`, lines ~2482–4187). The git dispatch path
(`_dispatch`, ~line 283) performs a single check — read-scope tokens are
refused on push — and nothing else. There is no per-repo/per-group RBAC on
clone, fetch, or push, and the LFS path has none either. Any authenticated
principal can read or write **any** repository.

**Action:**
- Add a fail-closed authorization gate in `_dispatch` (and the LFS handler)
  that calls `check_permission(username, group, repo, required_level)` before
  any git/LFS operation runs.
- Derive `required_level` from the operation: `READONLY` for
  `git-upload-pack`/fetch/LFS download, `CONTRIBUTOR` for
  `git-receive-pack`/push/LFS upload. Reuse the existing
  `git_http.get_required_access_level` logic rather than duplicating it.
- Default-deny: if no access entry resolves, return 403.

**Effort:** M (1–2 days incl. tests). **Owner:** platform.
**Verification:** new tests covering clone/fetch/push for readonly,
contributor, admin, and no-access principals at both group and repo scope; must
reach the project's 100% coverage bar.

### R-2 — Derive git token scope from real entitlement *(fixes F-3, High)*

**Problem (verified):** `_authenticate_git_request` (`lambda_handler.py` ~2414)
returns `{"scope": "read/write"}` for *any* valid JWT, regardless of the user's
actual access level. Combined with R-1's absence, every JWT is a write
credential.

**Action:** stop hardcoding scope. After R-1, scope is moot for the JWT path
because `check_permission` becomes the authority; return the authenticated
username only and let the RBAC gate decide. For PATs, keep the token's own
scope as an upper bound and still run `check_permission`.

**Effort:** S. **Owner:** platform. **Verification:** test that a JWT user with
readonly access is refused push.

### R-3 — Authenticate and authorize all `/api/*` mutations *(fixes F-5, High)*

**Problem (verified):** `_handle_api` group create/list/delete and branch
registration write straight to DynamoDB with no caller verification.

**Action:** add a single authentication+authorization middleware in front of
the API router (fail-closed). Require a verified session or bearer identity on
every mutating route, and `check_permission` at the appropriate level
(e.g., group admin to create/delete groups, contributor to register branches).
Read-only listing routes still require authentication.

**Effort:** M. **Owner:** platform. **Verification:** tests asserting 401 for
unauthenticated and 403 for under-privileged callers on every mutating route.

### R-4 — Centralize auth so controls are applied uniformly

**Problem:** the review's "security-design-consistency" and
"authenticated-network-communication" findings stem from auth being enforced in
some paths (web UI) but not others (git, LFS, some API). Even after R-1–R-3, the
enforcement should live in one place so it can't drift.

**Action:** route every request through one fail-closed
authenticate→authorize middleware ahead of the git/LFS/API/web handlers, rather
than per-handler checks. Document it as the single enforcement point.

**Effort:** M (partly overlaps R-1/R-3). **Owner:** platform.

---

## 3. Priority 1 — Edge & Network Protection

### R-5 — Add AWS WAF to CloudFront

**Problem (verified — no WAF/authorizer in template):** the internet-facing
CloudFront + API Gateway surface has no WAF and no request inspection. Drives
the WAF/traffic-inspection/public-web-app-protection/threat-intel findings.

**Action:** attach an `AWS::WAFv2::WebACL` (CLOUDFRONT scope) with AWS managed
rule groups (Common, Known Bad Inputs, SQLi) plus a rate-based rule; enable WAF
logging. Note: this complements, and does not replace, the app-layer auth in §2.

**Effort:** M. **Owner:** platform. **Note:** per architecture constraints, do
**not** introduce an ALB; WAF-on-CloudFront is the approved edge control.

### R-6 — Restrict CORS to known origins *(fixes F-14, Medium)*

**Problem (verified):** `cors_headers()` returns
`Access-Control-Allow-Origin: *`.

**Action:** replace the wildcard with an allowlist (the CloudFront custom
domain and any sanctioned tooling origins), driven by an env var so it's
per-deployment.

**Effort:** S. **Owner:** platform.

### R-7 — Explicit S3 public-access block and bucket policies *(fixes F-8, Med)*

**Problem (verified — no `PublicAccessBlockConfiguration` in template):** the
three buckets rely on account-level defaults only.

**Action:** add `PublicAccessBlockConfiguration` (all four flags true) to the
repos, lfs, and backups buckets, plus bucket policies restricting access to the
Lambda/CodeBuild roles and denying non-TLS requests (`aws:SecureTransport`).
Enable versioning on the repos and lfs buckets for recovery.

**Effort:** S. **Owner:** platform. **Verification:** `checkov`/`cfn-lint`
against the template (no `.cfnlintrc`; use inline directives if suppression is
ever needed).

### R-8 — Document/justify the flat serverless network model

**Problem:** several findings expect VPC segmentation and egress filtering. For
a single-Lambda serverless design this is partly a documentation matter.

**Action:** document the trust-zone reasoning; where egress control is
warranted (e.g., to the IdP, Gemini, mirror, SES), evaluate VPC attachment with
VPC endpoints + an egress allowlist, or record explicit risk acceptance with
rationale. Decision, not necessarily code, this cycle.

**Effort:** S (doc) / L (if VPC adopted). **Owner:** platform + security.

---

## 4. Priority 1 — Encryption, Secrets & IAM Consistency

### R-9 — Standardize encryption on customer-managed KMS *(fixes F-6, Low→Med)*

**Problem (verified):** repos/lfs use SSE-KMS, but backups use AES256 and
DynamoDB uses the AWS-owned default key. The design's "all data encrypted at
rest (KMS)" claim is therefore inaccurate.

**Action:** provision a customer-managed KMS key (or keys) and apply it to the
backups bucket and all DynamoDB tables (`SSESpecification`). Add a key policy
restricting `kms:Encrypt/Decrypt` to the Lambda and CodeBuild roles, and enable
annual key rotation. Alternatively, if the AES256/default posture is accepted,
correct the design doc's claim and record the risk acceptance.

**Effort:** M. **Owner:** platform.

### R-10 — Move the Gemini API key out of the URL *(fixes F-11, Medium)*

**Problem (verified):** `ai.py::call_gemini` puts the key in the query string
(`...:generateContent?key=<api_key>`), where it can surface in logs/traces.

**Action:** send the key in the `x-goog-api-key` header instead. Add log
redaction for any credential-shaped values as defense in depth.

**Effort:** S. **Owner:** platform.

### R-11 — Tighten IAM resource scoping *(fixes F-13, Low→Med)*

**Problem (verified):** Lambda and backup roles use `table/${prefix}-*` DynamoDB
wildcards; the runtime role holds `PutSecretValue`/`CreateSecret` and can
CRUD the audit table.

**Action:** enumerate per-table ARNs; drop `PutSecretValue`/`CreateSecret` from
the runtime role (create/rotate secrets out of band); scope the backup role's
DynamoDB actions to read-only. This also supports the audit-integrity fix (R-15).

**Effort:** M. **Owner:** platform.

### R-12 — Secrets rotation and lifecycle

**Problem:** IdP config, mirror creds, Gemini key, and the session-cookie HMAC
secret have no documented rotation. Drives the credential-governance findings.

**Action:** enable Secrets Manager rotation schedules where supported; document
rotation/revocation for the Gemini key and mirror creds; clarify the source of
the cookie signing secret and put it in Secrets Manager with rotation. Define a
PAT maximum lifetime and prune stale/unused PATs.

**Effort:** M. **Owner:** platform + security.

---

## 5. Priority 2 — Detection, Logging & Incident Response

These are largely genuine gaps (monitoring beyond an audit table and SNS
operational alerts is thin), addressed by a mix of new config and documentation.

### R-13 — Turn on managed detection

**Action:** enable GuardDuty (incl. Malware Protection for S3 where useful),
enable CloudTrail (management + relevant data events), and enable API Gateway +
CloudFront access logs. Route security-relevant signals to the SNS topic / a log
sink. Addresses threat-intel, continuous-monitoring, adverse-event-analysis,
anti-malware, and vuln-scanning-of-the-platform findings.
**Effort:** M. **Owner:** platform + security.

### R-14 — Define the logging/audit catalogue and retention

**Action:** document what the `audit` table records (auth attempts, clone/push,
RBAC and access-control changes, admin actions) and the fields per event
(actor, action, resource, timestamp, outcome, source IP). Set retention
(≥12 months, ≥3 months queryable). Addresses the audit-logging and
comprehensive-logging findings. Mostly documentation + minor code to ensure
coverage.
**Effort:** M. **Owner:** platform.

### R-15 — Make the audit trail tamper-evident

**Problem:** the Lambda role can rewrite its own audit records (ties to F-13).

**Action:** restrict the Lambda to `PutItem`-only on the audit table, enable
DynamoDB PITR, and stream audit records to S3 with Object Lock (WORM) for
immutable retention.
**Effort:** M. **Owner:** platform.

### R-16 — Write an incident response & recovery runbook

**Action:** document alert routing/escalation from SNS/CloudWatch to an on-call
responder; containment actions (PAT/session mass-revocation, disabling the API
route or Lambda, restrictive WAF rules); a communication plan (stakeholders,
timelines, channels); and recovery sequencing with completion criteria. Covers
the incident-response, containment, recovery-communication, and
forensic-readiness findings. Documentation deliverable.
**Effort:** M. **Owner:** security.

---

## 6. Priority 2 — Data Integrity, Backups, Supply Chain & Docs

### R-17 — Backup integrity, immutability, and restore testing

**Action:** define RPO/RTO; enable S3 Object Lock/versioning on the backups
bucket; add checksum/manifest validation before restore; document periodic
restore tests and post-restore validation. Replace the blind corrupt-repo
reinitialization in `git_backend.py` *(F-10)* with an alert-and-quarantine path
so failures can't silently discard data.
**Effort:** M–L. **Owner:** platform.

### R-18 — Platform SAST, dependency scanning & patching in CI

**Problem:** the 16 linters scan *tenant* code, not the platform. The platform's
own `gitgateway` package, `template.yaml`, and dependencies (Dulwich, PyJWT,
etc.) have no described scanning or patch cadence.

**Action:** add a CI pipeline that runs `bandit`/`ruff`/`pylint` on the
package, `checkov`/`cfn-lint` on the template, and `pip-audit`/Dependabot on
dependencies, with blocking gates. Generate an SBOM. Define severity-based patch
SLAs (critical ≤ 30 days) for the runtime and libraries. Covers the SDLC,
supply-chain, vuln-management, and automated-security-testing findings.
**Effort:** M. **Owner:** platform.

### R-19 — Input normalization for group/repo names *(fixes F-9, Medium)*

**Action:** validate and normalize `group`/`repo` names (allowlist charset,
reject separators/traversal) before they build S3 prefixes or DynamoDB keys.
**Effort:** S. **Owner:** platform.

### R-20 — Move the AI/abuse rate limiter to DynamoDB *(fixes F-16, Low)*

**Action:** replace the in-memory per-instance limiter in `ai.py` with a shared
DynamoDB-backed counter so limits hold across concurrent Lambdas.
**Effort:** S. **Owner:** platform.

### R-21 — JWT hardening *(fixes F-1 Medium, F-2 Low)*

**Action:** enable `verify_aud` in `validate_jwt` with the expected audience;
replace the substring-based `<X509Certificate>` extraction with a proper
namespaced XML parse.
**Effort:** S. **Owner:** platform.

### R-22 — Documentation-only clarifications

Add or expand design-doc sections to close the remaining `INSUFFICIENT_DATA`
results (no code change, but real gaps in the artifact):

- **Data classification & tagging** scheme (source code, PATs, audit data, user
  metadata) mapped to each store, with `Classification`/`Sensitivity` resource
  tags and AWS Config monitoring.
- **Framework mapping** — map controls to NIST CSF / CIS AWS Foundations /
  Well-Architected Security Pillar, documenting deviations. (Closes the
  "no recognized baseline" finding.)
- **Change management** — dev/prod stack + account separation, pipeline
  approval gates, ensuring defaults like the F-12 alert email never reach prod.
- **Code review process** — required approvals and which automated checks gate
  merges for the platform itself.
- **Session idle timeout** — document the cookie `exp` lifetime and whether
  inactivity (not just absolute age) invalidates sessions.
- **Third-party dependency governance** — criticality ratings and
  incident-notification expectations for Gemini, the IdP, SES, and mirror
  targets.
- **Strong-auth details** — reference the IdP's password/MFA/lockout policy and
  PAT rate limiting (delegated to `idp.botthouse.net`).
- **Remove the hardcoded default alert email** from `template.yaml` *(F-12)*.

**Effort:** M (documentation). **Owner:** security + platform.

---

## 7. Explicitly Dismissed — No Action (with rationale)

The following review items require **no remediation**. Recording them so the
decision is auditable.

**Not applicable — no cardholder data (PCI DSS).** GitGateway stores source
code, LFS objects, and repository metadata; it processes no PAN or sensitive
authentication data, so there is no Cardholder Data Environment. All of the
following are correctly `NOT_APPLICABLE`: CDE segmentation and scope
minimization; POI device inventory/integrity; MFA-for-CDE; strong-crypto and
TLS for CDE/POS-POI; PAN masking/storage protection; SAD non-retention;
account-data classification/retention; CDE data-flow validation; wireless
network config and rogue-AP detection; secure payment software lifecycle;
programmatic access control for CHD stores; TPSP accountability for account
data; PCI scope/change-impact assessment; secure media destruction of CHD;
time synchronization (provider-managed on serverless); scheduled PCI activities;
penetration-testing-readiness framed against a CDE perimeter. If GitGateway ever
stores regulated data, these must be revisited.

**Already compliant — no action.** Encryption in transit (TLS 1.2_2021,
redirect-to-https, https-only origin); automated certificate management (ACM);
network & data-flow documentation (diagrams added); systematic threat modeling
(findings register); asset inventory; structured risk assessment; shared-
responsibility documentation; holistic cybersecurity coverage; AI-component
risk assessment; documented control rationale; risk-visibility/communication.

**Partial-credit items** the reviewer marked compliant but that improve with the
above work (e.g., risk-prioritization becomes a tracked POA&M via §8) — no
standalone action.

---

## 8. Consolidated Action Table (POA&M)

| ID | Priority | Fixes | Type | Effort | Owner | Status |
|----|----------|-------|------|--------|-------|--------|
| R-1 | P0 | F-4 (Critical) | Code | M | platform | **Done** |
| R-2 | P0 | F-3 (High) | Code | S | platform | **Done** |
| R-3 | P0 | F-5 (High) | Code | M | platform | **Done** |
| R-4 | P0 | consistency | Code | M | platform | **Done** |
| R-5 | P1 | WAF/inspection | IaC | M | platform | **Done** |
| R-6 | P1 | F-14 | Code | S | platform | **Done** |
| R-7 | P1 | F-8 | IaC | S | platform | **Done** |
| R-8 | P1 | segmentation | Doc/IaC | S–L | platform+sec | Not started |
| R-9 | P1 | F-6 | IaC | M | platform | **Done** |
| R-10 | P1 | F-11 | Code | S | platform | **Done** |
| R-11 | P1 | F-13 | IaC | M | platform | **Done** |
| R-12 | P1 | credential governance | IaC/Doc | M | platform+sec | Not started |
| R-13 | P2 | detection/threat-intel | IaC | M | platform+sec | Not started |
| R-14 | P2 | logging catalogue | Doc/Code | M | platform | Not started |
| R-15 | P2 | audit integrity | IaC/Code | M | platform | Partial (PITR on) |
| R-16 | P2 | incident response | Doc | M | security | Not started |
| R-17 | P2 | backups/F-10 | Code/IaC | M–L | platform | Partial (versioning on) |
| R-18 | P2 | platform SAST/patching | CI | M | platform | Not started |
| R-19 | P2 | F-9 | Code | S | platform | Not started |
| R-20 | P2 | F-16 | Code | S | platform | Not started |
| R-21 | P2 | F-1/F-2 | Code | S | platform | **Done** |
| R-22 | P2 | doc-only clarifications, F-12 | Doc | M | security+platform | Not started |

### Implementation notes (P1 batch)

- **R-11 tradeoff:** the runtime Lambda role lost broad Secrets Manager write
  and `CreateSecret`. The `POST /api/admin/ai-key` route can still *update* the
  existing `gitgateway/gemini-api-key` secret (write is scoped to just that
  secret) but can no longer *create* it — the secret must be provisioned via
  IaC/CLI first. This is the intended least-privilege posture.
- **R-9 note:** encryption now uses one customer-managed CMK
  (`alias/<prefix>-data`) across S3 and DynamoDB. Existing deployments will
  re-encrypt on update; the CMK policy grants only the Lambda and backup roles
  `Encrypt/Decrypt/GenerateDataKey/DescribeKey`.
- **R-5 note:** WAF is CloudFront-scoped and created in us-east-1 (this stack's
  region). No ALB is introduced, per the architecture constraints.
- **R-15/R-17 partial:** DynamoDB PITR and S3 versioning were enabled as part
  of this batch; the remaining audit-immutability (append-only writes, S3
  Object Lock) and backup integrity/restore-testing work stays open under the
  original R-15/R-17 scope.

**Sequencing recommendation:** ship R-1–R-4 first (they close the Critical and
both High findings and the bulk of the mapped `NON_COMPLIANT` controls), then
the P1 edge/encryption/IAM items, then the P2 detection/IR/supply-chain and
documentation work. Track each R-# to closure with an owner and target date.

---

## 9. Note on the Design Document Itself

The reviewed design doc candidly disclosed the F-4 authorization gap. That
transparency is correct for a security review, but until R-1–R-4 land, the
design doc, diagrams, and `template.yaml` themselves live in repositories that
any authenticated principal can clone (the "protection-of-security-posture-
artifacts" finding). Treat these artifacts as sensitive and restrict sharing
until the data-plane RBAC fix is deployed.
