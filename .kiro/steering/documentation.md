---
inclusion: auto
---

# Documentation Standards

All documentation in this project must be verbose and comprehensive. Apply these rules whenever creating or modifying code, documentation files, or configuration.

## Python Docstrings

- Every module must have a module-level docstring explaining its purpose, responsibilities, and how it fits into the larger system.
- Every public class must have a docstring describing its role, usage patterns, and relationships to other classes.
- Every public function and method must have a Google-style docstring that includes:
  - A one-line summary followed by a detailed description if the behavior is non-obvious.
  - An `Args` section documenting every parameter with its type, purpose, constraints, and default behavior.
  - A `Returns` section describing what is returned, including edge cases (e.g., "Returns None if not found").
  - A `Raises` section listing every exception that can be raised, with the condition that triggers it.
  - An `Example` section for complex functions showing typical usage.
- Private functions (prefixed with `_`) should still have docstrings, though they can be shorter.
- Docstrings must describe *why* something exists, not just *what* it does.

## Inline Comments

- Add inline comments to explain non-obvious logic, business rules, performance considerations, and workarounds.
- Comment *why* a decision was made, not *what* the code does (the code shows what).
- Use comments to mark sections of longer functions (e.g., `# --- Validation ---`, `# --- Persistence ---`).
- Document any magic numbers, thresholds, or constants with their reasoning.

## README and docs/ Files

- The project README must be kept up to date with:
  - A clear project description and motivation.
  - Complete installation instructions for both users and developers.
  - Usage examples covering the most common workflows.
  - Architecture overview explaining how components interact.
  - Full CLI reference with examples for every subcommand.
  - Troubleshooting section for common issues.
- The `docs/` folder must contain:
  - An architecture document explaining system design decisions, data flow, and component responsibilities.
  - API reference documenting every REST endpoint with request/response examples.
  - A deployment guide with step-by-step instructions.
  - A configuration reference listing every config key, environment variable, and CLI flag.
  - A contributor guide explaining how to add new features, run tests, and submit changes.

## CHANGELOG

- Every feature, fix, or change must have a corresponding CHANGELOG entry.
- Entries must be descriptive enough that someone unfamiliar with the code can understand what changed and why.
- Use full sentences, not terse bullet fragments.

## Type Hints as Documentation

- All function signatures must include complete type hints.
- Use descriptive type aliases for complex types (e.g., `EventPayload = dict[str, Any]`).
- Annotate return types even when they seem obvious.

## Test Documentation

- Test class docstrings must describe what aspect of the system they verify.
- Test function docstrings must describe the specific behavior being tested, not just restate the function name.
- Complex test fixtures should have docstrings explaining what they set up and why.
