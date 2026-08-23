# Pull Requests

## Overview

GitGateway provides a full pull request workflow with inline comments, review threads, code suggestions, status checks, and merge strategies.

## Creating a PR

```bash
gitgateway pr create myteam/app --source feature/login --target main --title "Add login"
```

Or via the web UI: navigate to the repository and click "New Pull Request".

## Review Workflow

1. **Create PR** — Author opens a PR from source branch to target branch
2. **AI Summary** (if enabled) — Gemini generates a description summarizing changes
3. **SAST Scan** (if enabled) — CodeBuild runs security tools, posts findings as inline comments
4. **Request Reviewers** — Author or admin assigns reviewers
5. **Review** — Reviewers add inline comments, approve, or request changes
6. **Suggestions** — Reviewers can propose code changes via suggestion blocks
7. **Thread Resolution** — Comment threads can be marked as resolved
8. **Status Checks** — External CI posts pass/fail status
9. **Merge** — When all requirements met, PR can be merged

## Merge Strategies

| Strategy | Description |
|----------|-------------|
| `merge` | Creates a merge commit (default) |
| `squash` | Squashes all commits into one, then merges |
| `fast-forward` | Fast-forwards target branch to source HEAD (no merge commit) |

## Merge Requirements

Before merging, all configured requirements must be satisfied:

- **Required approvals** — Minimum number of "approved" reviews
- **Required status checks** — Named checks that must have "success" status
- **No force-push** — Branch protection may block the merge target

## Inline Comments

Comments can be attached to specific lines in the diff:

- **file_path** — The file being commented on
- **line_number** — The line in the diff
- **side** — `new` (addition side) or `old` (deletion side)
- **thread_id** — Groups replies into threads

## Code Suggestions

Reviewers can propose specific code changes using suggestion format. The PR author can apply suggestions directly, which creates a commit on the source branch.

## Cross-Repository PRs (Forks)

PRs can be created from a forked repository:

```bash
gitgateway pr create upstream/app --source feature/x --title "Fix from fork"
```

The `source_repo` field tracks which fork the PR comes from.
