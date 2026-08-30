# Code Coverage Standard

This project requires **100% test coverage**. Every line and branch of code in
the `gitgateway` package must be exercised by the test suite.

## Requirements

- All code must maintain **100% line and branch coverage**.
- Coverage is enforced via `--cov-fail-under=100` in `pyproject.toml`.
- New code must include tests before it is considered complete.
- A change is not done until coverage is back at 100% and all tests pass.

## Running Coverage

```bash
# Full coverage report (excludes e2e which needs a live server)
python -m pytest tests/ --ignore=tests/e2e \
  --cov=gitgateway --cov-report=term-missing --cov-fail-under=100

# HTML report for detailed inspection
python -m pytest tests/ --ignore=tests/e2e \
  --cov=gitgateway --cov-report=html
open htmlcov/index.html
```

## Writing Tests to Reach 100%

- Use `pytest` with `unittest.mock.patch` to isolate AWS calls.
- Use `moto` for DynamoDB/S3 mocking where a realistic backend helps.
- Cover every branch: success paths, error paths, empty results,
  and exception handlers.
- For handler/router code (`lambda_handler.py`), test each route with
  valid, invalid, and unauthenticated requests.
- For git protocol code (`git_backend.py`), test upload-pack,
  receive-pack, ref creation, and ref deletion.

## Excluding Code (rare)

Only exclude code that genuinely cannot be tested (e.g. a
`if __name__ == "__main__"` block). Use an explicit pragma with a reason:

```python
if __name__ == "__main__":  # pragma: no cover
    main()
```

Do not use `# pragma: no cover` to hide untested logic. If it can run,
it must be tested.

## When Adding a Feature

1. Write the implementation.
2. Write unit tests covering all paths.
3. Run the coverage command above and confirm 100%.
4. Fix any coverage gaps before committing.
5. Ensure `pylint` is 10/10 and `bandit` is clean (see other steering).

## Current Debt

Coverage is currently below 100% in these modules and must be brought up:

- `lambda_handler.py` — routes, LFS, tokens, sidebar, dashboard, AI wiring
- `git_backend.py` — Smart HTTP handlers, ref updates
- `deploy.py` — layer packaging, binary layer, publish
- `lint.py` — subprocess-based linters
- `cli.py` — command handlers
- `tokens.py` — a few branches

New work must not decrease coverage further, and existing gaps should be
closed as the affected code is touched.
