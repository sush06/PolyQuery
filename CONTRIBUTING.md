# Contributing

Use Python 3.11 or newer. From the project root:

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
polyquery evaluate --output benchmarks/latest.json
```

For a new operation, define its meaning and constraints, add adapter validation, then add independent answer checks and unsupported-input cases. Preserve abstention behavior. Add development fixtures to `benchmarks/demo.json`; keep future held-out test sets separate and untouched during development.

Do not commit API keys, company datasets, `.env` files, or invented benchmark claims. Update README scope if behavior changes. Generated code must remain inside the expression allowlist unless a separately reviewed isolation mechanism is introduced.
