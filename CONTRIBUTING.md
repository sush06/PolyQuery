# Contributing

Use Python 3.11 or newer. From the project root:

```bash
python -m pip install -e . pytest
python -m pytest -q
polyquery evaluate --output benchmarks/latest.json
```

Use `python -m pytest -m unit`, `-m integration`, `-m security`, or
`-m evaluation` to select a test category. Integration tests use generated
schema fixtures and also exercise the local Chinook and LDBC benchmark files
when present. Install `.[taxi]` to include the Parquet-backed tests.

Use development fixtures for planner changes and separate validation cases
for regression tests. Held-out benchmark tests check label and split integrity.
Run planner scoring on held-out questions as a separate evaluation after the
planner is frozen.
