# Contributing

Use Python 3.11 or newer. From the project root:

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
polyquery evaluate --output benchmarks/latest.json
```
