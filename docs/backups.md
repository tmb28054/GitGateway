# Repository Backups & Restore — Design

Status: **Implemented** (shipped in 0.2.3)
Owner: Topaz Bott

## Goal

Provide a nightly, self-contained archive of every GitGateway repository
(full git history for all refs, plus its Git LFS objects) and an
admin-console restore flow. Backups protect against logical failure modes
that S3 durability does not: accidental/malicious deletion, sync corruption,
and credential compromise.

Scope is **Tier 2 only** — a portable `git bundle` archive per repo. S3
versioning and cross-region replication are explicitly out of scope.

## Summary of decisions

| Topic | Decision |
|-------|----------|
| Storage | New bucket `gitgateway-backups` (same account, SSE-S3) |
| Snapshot format | `git bundle create --all` (all branches + tags) per repo, plus its LFS objects |
| Retention | Tiered: daily (30d), weekly (52w), monthly (36m) via prefix + lifecycle |
| Runtime | AWS CodeBuild (no 15-min Lambda cap) on an EventBridge schedule |
| Restore | Admin console only; always restores to a **new** repo name (never overwrites) |
| Restore scope | Single repo, explicit list, or all registered repos |
| "All repos" | Repos registered in DynamoDB (`list_groups` → `list_repos`) |
| Alerting | New SNS topic `gitgateway-alerts`; email `topaz@topazhome.net`; publish on failure |
| Manifest | Per-snapshot JSON manifest (repos, sizes, LFS counts, per-repo status) |
| Access control | `gitgateway-admin` entitlement for all backup/restore admin pages + APIs |
| Script delivery | Archiver script + buildspec zipped to `gitgateway-repos` under `codebuild/` |

## Storage layout

New bucket `gitgateway-backups` (SSE-S3, KMS not required per decision):

```
gitgateway-backups/
  daily/<YYYY-MM-DD>/
    manifest.json
    <group>/<repo>.bundle
    <group>/<repo>.lfs/<oid>            # referenced LFS objects
  weekly/<YYYY-MM-DD>/   ...            # written on Sundays
  monthly/<YYYY-MM-DD>/  ...            # written on the 1st of the month
```

Lifecycle expiration (per prefix):
- `daily/`   → expire after 30 days
- `weekly/`  → expire after 364 days (52 weeks)
- `monthly/` → expire after 1095 days (36 months)

A single nightly run writes to `daily/`; additionally to `weekly/` on
Sundays and `monthly/` on the 1st. Tier-specific prefixes let plain S3
lifecycle rules express the retention without custom pruning code.

## Nightly archive flow (CodeBuild)

EventBridge Scheduler (cron, nightly) → starts the `gitgateway-backup`
CodeBuild project. The project runs `scripts/backup_repos.py` (packaged into
a zip in `s3://gitgateway-repos/codebuild/backup.zip`), which:

1. Determines today's target tiers (always daily; + weekly/monthly by date).
2. Lists all repos via `db.list_groups()` → `db.list_repos(group)`.
3. For each repo (isolated — one repo's failure does not abort the run):
   a. Sync the bare repo from `gitgateway-repos` to local (`storage.sync_repo_to_local`).
   b. `git bundle create <repo>.bundle --all` (all refs).
   c. Enumerate LFS objects referenced by the repo and copy each from the
      `gitgateway-lfs` bucket into `<group>/<repo>.lfs/<oid>` in the snapshot.
   d. Upload the bundle + LFS objects to each target tier prefix.
   e. Record per-repo status (ok / error, sizes, LFS count) for the manifest.
4. Write `manifest.json` to each target tier prefix.
5. If any repo failed (or the run raised), publish a summary to the
   `gitgateway-alerts` SNS topic. Always log a summary to CloudWatch.

### Manifest schema (`manifest.json`)

```json
{
  "snapshot": "2026-09-07",
  "tier": "daily",
  "started_at": "2026-09-07T04:00:00Z",
  "finished_at": "2026-09-07T04:12:33Z",
  "repo_count": 42,
  "ok_count": 41,
  "failed_count": 1,
  "repos": [
    {
      "group": "botthouse",
      "repo": "gitgateway",
      "status": "ok",
      "bundle_key": "daily/2026-09-07/botthouse/gitgateway.bundle",
      "bundle_bytes": 1048576,
      "lfs_object_count": 3,
      "lfs_bytes": 20971520,
      "refs": ["refs/heads/main", "refs/tags/v0.2.2"]
    }
  ]
}
```

The manifest powers the admin restore-picker (which snapshots exist, what's
in each, per-repo status).

## Restore flow (admin console)

New admin pages (gated behind `gitgateway-admin`):
- **`/admin/backups`** — lists available snapshots (from manifests) across
  tiers; drill into a snapshot to see its repos + status.
- **Restore action** — admin selects a snapshot and one repo / a list / all,
  optionally overrides the target name, and submits.

Restore semantics:
- **Always restores to a new repo** — default target
  `<group>/<repo>-restore-<YYYYMMDD-HHMM>`; the admin may override the name.
  The live repo is never modified or overwritten.
- Restore is executed **asynchronously by the same CodeBuild project**
  (mode = restore), so large repos don't hit any timeout. The console shows
  "restore started"; completion is visible via a status record / notification.
- Restore steps per repo: download the bundle (+ LFS objects) from the chosen
  snapshot, `git clone` the bundle, recreate the target repo in DynamoDB +
  `gitgateway-repos`, push all refs, and re-upload LFS objects to
  `gitgateway-lfs`.

API (admin-only):
- `GET  /api/admin/backups` — list snapshots.
- `GET  /api/admin/backups/<snapshot>` — manifest detail.
- `POST /api/admin/restore` — body: `{snapshot, tier, repos: [...] | "all",
  target_name_override?}` → starts a restore CodeBuild run, returns a job id.

## Infrastructure (CloudFormation additions)

- `AWS::S3::Bucket` **BackupBucket** (`gitgateway-backups`) with the tiered
  lifecycle rules above.
- `AWS::SNS::Topic` **AlertsTopic** (`gitgateway-alerts`) +
  `AWS::SNS::Subscription` (email `topaz@topazhome.net`).
- `AWS::CodeBuild::Project` **BackupProject** — runs the archiver/restore
  script; env selects mode (`archive` | `restore`) and restore parameters.
- `AWS::Scheduler::Schedule` (or EventBridge rule) — nightly trigger for
  archive mode.
- IAM role for CodeBuild: read `gitgateway-repos` + `gitgateway-lfs`,
  read/write `gitgateway-backups`, read DynamoDB tables, publish to
  `gitgateway-alerts`; the Lambda role gains `codebuild:StartBuild` +
  read `gitgateway-backups` for the restore trigger and backup listing.

## Testing

- Unit tests (moto S3/DynamoDB + real dulwich repos, per existing patterns):
  archive of a repo with multiple refs; LFS object copy; tier selection by
  date; manifest contents; per-repo error isolation; SNS publish on failure.
- Restore tests: bundle → new repo with a non-colliding name; refs restored;
  LFS re-uploaded; admin-only gating (401/403 for non-admins).
- Coverage must stay at 100% (enforced gate).

## Open items / follow-ups (not in this change)

- Restore job status surfacing (a small `backups`/`jobs` record the console
  polls) — included, but the UI polish can iterate.
- Optional: verify bundles (`git bundle verify`) as part of the nightly run.
