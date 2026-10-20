# Tracked Note: Git First-Request Latency (S3 ↔ /tmp repo sync)

**Status:** Open — measurement captured, optimization not yet started
**Area:** `gitgateway/storage.py` (`sync_repo_to_local`, `sync_repo_to_s3`)
**Priority:** Low (not user-blocking, but noticeable)
**Owner:** platform

## Summary

The first git request to a repository (and every request that lands on a cold
Lambda instance, or the first touch of a repo on a warm instance) pays a
multi-second penalty while the Lambda downloads the entire bare repository
from S3 to `/tmp` object-by-object. This note records the measured cause and
the candidate fixes so the optimization can be picked up with data in hand.

## Measurement (production, 2026-10-20)

Captured via the `SYNC_METRIC` structured log added to `storage.py`
(`operation=s3_to_local`), on `botthouse/GitGateway`:

| Metric | Value |
|--------|-------|
| Objects | 147 |
| Bytes | 2,157,440 (~2.1 MB) |
| Duration | 8,411.9 ms (~8.4 s) |
| Throughput | 0.26 MB/s |
| Warm cache | false |

Observed end-to-end `info/refs` latency on cold requests: ~8–15 s.

## Root cause

The latency is **request-count-bound, not bandwidth-bound**. 2.1 MB over
8.4 s is only 0.26 MB/s — far below available bandwidth — because
`sync_repo_to_local` issues **147 sequential, blocking `s3.download_file`
calls**, one per object, each paying full S3 request round-trip latency
(~57 ms/object here). The payload size is almost irrelevant; the object
*count* dominates.

A secondary issue: warm Lambda instances currently **re-download the whole
repo anyway** (the code removes and re-syncs `/tmp` even when the repo is
already present), so the `warm_cache` flag in the metric reports whether the
repo *was* present, not whether a download was avoided.

## How to observe

CloudWatch Logs Insights over `/aws/lambda/gitgateway-handler`:

```
fields @timestamp, @message
| filter @message like /SYNC_METRIC/
| parse @message '"duration_ms":*,' as duration_ms
| parse @message '"objects":*,' as objects
| parse @message '"operation":"*"' as op
| stats avg(duration_ms), max(duration_ms), avg(objects) by op
```

Let this collect across multiple repos/requests before optimizing, and watch
the `warm_cache` distribution to quantify how often the cost is paid.

## Candidate optimizations (in recommended order)

1. **Parallelize the downloads.** Replace the sequential loop with a bounded
   thread pool of `download_file` calls (S3 I/O is GIL-friendly). Expected to
   cut the 147-object sync by roughly the pool size. Lowest risk, biggest
   immediate win, no architectural change. **Recommended first step.**
2. **Incremental / skip-unchanged sync on warm instances.** Keep the `/tmp`
   copy across warm invocations and only fetch objects whose S3 ETag/size
   differs, instead of wiping and re-downloading. Removes the cost entirely on
   warm hits. Requires care around correctness (git object immutability helps:
   loose objects never change, only refs do).
3. **Packed storage.** Store/serve the repo as packfiles so a sync moves a few
   large objects instead of ~150 loose ones. Attacks the root cause but is a
   larger change to the storage format and the git protocol handlers.

Not worth pursuing for this problem: bandwidth upgrades, EFS-backed `/tmp`
(adds always-on cost + VPC wiring, against the serverless design goals), and
provisioned concurrency (always-on cost; only masks cold starts, not the
per-request object-count cost).

## Constraints / cautions

- `storage.py` is on the git **data path** and carries a 100% coverage bar.
  Any change needs full unit coverage plus the e2e git tests
  (`tests/e2e/test_git_auth.py`, `test_git_protocol.py`) exercised against a
  deployed stack.
- Measure before/after with the existing `SYNC_METRIC` log so the win is
  quantified rather than assumed.
- Preserve the fail-closed RBAC and the existing sync-to-S3-after-push
  semantics; this is purely a performance change.

## Next action

Prototype option (1) — parallel downloads behind the existing `SYNC_METRIC`
instrumentation — and compare the before/after `duration_ms` on
`botthouse/GitGateway` (the 147-object / 8.4 s baseline above).
