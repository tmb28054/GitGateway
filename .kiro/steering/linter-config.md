# Linter Configuration Files

Do **not** use or create `.yamllint` or `.cfnlintrc` configuration files in
this project.

## Rule

- Do not create `.yamllint` (yamllint config).
- Do not create `.cfnlintrc` (cfn-lint config).
- These files are listed in `.gitignore` and must not be committed.

## Rationale

Linter configuration is applied inline or via defaults rather than through
committed config files:

- **yamllint**: configure rules inline in the code that invokes it (e.g. the
  in-Lambda linter passes its config as a string), or use `# yamllint disable`
  comments in the YAML itself where a rule must be overridden.
- **cfn-lint**: rely on default rules; suppress specific findings with inline
  `# cfn-lint: disable` metadata in the template rather than a repo-wide config.

Keeping these out of the repo avoids repo-wide rule suppression that hides
real issues and keeps linting behavior consistent with the in-Lambda PR
linters.

## If You Need a Rule Override

- Prefer an inline directive at the exact location that needs it.
- Document why the override is necessary in a comment.
- Never re-introduce `.yamllint` or `.cfnlintrc`.
