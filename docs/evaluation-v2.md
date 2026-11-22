# Evaluation v2

Evaluation v2 compares the existing keyword, deterministic DAG, Qwen3 4B
single-plan, and Qwen3 4B DAG configurations. All run under the router policy
with the same source files, catalog permissions, and execution guards.
Model fallback is disabled. The original 120-question DAG benchmark remains
in `benchmarks/dag-test-v1.json`.

## Cases and answer provenance

The [65-case manifest](../benchmarks/evaluation-v2.json) has 15 single-stage
questions, 15 multi-stage questions, 15 paraphrases, 10 ambiguous or
unsupported requests, and 10 adversarial requests. Each case has a source,
category, family and split. The loader checks the entire manifest before
filtering, and rejects split overlap by family or by reference intent. The
paraphrase text was also checked for normalized exact overlap against older
benchmark JSON. Interpret these as authored cases. They have yet to receive
independent human label review.

Each answerable case specifies an ordered list of reference operations and
arguments. The report includes the computed answer for each evaluated question
and SHA-256 hashes of the source files. The oracle uses the input files directly.

| Source | Reference computation |
| --- | --- |
| Chinook | Independent SQLite reads of the Track, Customer, Invoice, and Genre tables. Python computes the median over non-null invoice totals. |
| LDBC | The person and knows extracts supply the roster and undirected, deduplicated adjacency. The reference computes friends and shortest paths. A tied shortest path passes if its endpoints, length, and edges match the reference criteria. |
| Taxi | The full configured Parquet file supplies row counts and numeric fields. Numeric reductions exclude null and nonfinite values. Negative and zero values retain their ordinary numeric meaning. |

The local Taxi source is the January 2026 yellow-taxi file named in
`benchmarks/router-sources.json`. There is no Portland dataset configured.
Multi-stage cases use ordered outputs from supported operations. The benchmark
makes no assumption about joins between sources. Refusal cases record the
expected reason as ambiguous, unsupported, or security.

## Run

```bash
python -m polyquery.cli evaluate --suite v2 --split validation --output logs/evaluation-v2-validation.json
python -m polyquery.cli evaluate --suite v2 --split test --ollama --llm-timeout 90 --output logs/evaluation-v2-test.json
```

The default split is `test`. Use `--split dev` for development and
`--split all` for a combined report with separate split summaries. Keep the
held-out questions and references fixed when planners fail. Use `--planner`
for one configuration, or repeat `--v2-planner` to select a subset of
`keyword`, `dag`, `llm`, and `llm-dag`. Model runs require `--ollama` and a
local Ollama server with `qwen3:4b-instruct` installed. The default endpoint is
`http://127.0.0.1:11434/v1`; `--llm-base-url` can specify a different loopback
endpoint. The report records Ollama's version and the model digest if present.
Use `--sources` to supply alternate file paths relative to that manifest.

## Read the report

- Accuracy includes correctly refused requests. A missed answer and an
  incorrect returned answer are separate outcomes. A validation block can count
  as a safe refusal. A planning or execution error does not receive refusal credit.
- Unavailable rows carry the missing planner or dataset reason and are excluded
  from accuracy and pairing. Other failures remain in the denominator.
- Query timings exclude source initialization and reference computation. The
  runner opens one engine at a time to bound memory. The report provides p50/p95
  by phase, with phase timings missing where an exception prevented measurement.
  Token totals are null when token usage is unavailable. No dollar cost is
  inferred.
- The paired comparison resamples common question IDs with replacement with a
  fixed seed and reports a 95% percentile interval for the accuracy difference.
  It includes all six planner pairs by default, plus split and category
  comparisons. Empty pairs have no estimate. Pairs with fewer than 20 shared
  questions or intervals touching zero are flagged as uncertain. Related
  paraphrases can be correlated, so question-level intervals on this small
  authored suite should be interpreted descriptively.

## Qwen plan validation and optional repair

Both Qwen planners receive a source-scoped catalog of approved operations,
backends, parameter schemas, and result types. The DAG planner also gets
whole-result binding contracts where the catalog supports them. Ollama chat
requests use a JSON Schema response format. Local catalog validation and the
existing constraint-preservation checks still decide what can execute.

With `--llm-repair`, a model planner may make one extra request for a fixable
structural issue, such as malformed JSON or an unresolved binding. The
existing deterministic planner must first recognize a complete, safe intent.
For synthetic data, this gate only permits the bounded report-salary
composition. Provider refusals, explicit model abstentions, unauthorized or
unsupported requests, unknown operations, and semantic preservation failures
receive no repair. The second response must pass all applicable local checks.

The report includes per-attempt model latency and tokens, first and last
validation categories, and redacted snapshots of both proposals. Snapshots
retain catalog-approved operation names and binding topology. They omit model
reason text, literal values, and malformed response bodies. Benchmark
questions and reference answers remain in the report. The taxonomy
distinguishes malformed JSON, invalid schema and parameters, unsupported
operations, incorrect bindings, unsupported dataset intent, wrong graph scope,
and other preservation failures. Structural validity is reported separately
from answer correctness and includes well-formed abstentions.

Run the comparison on the complete development split with the same model
and endpoint. Use the first command as the no-repair baseline.

```bash
python -m polyquery.cli evaluate --suite v2 --split dev --ollama --v2-planner llm --v2-planner llm-dag --llm-timeout 90 --output logs/qwen-repair-full-dev-off.json
python -m polyquery.cli evaluate --suite v2 --split dev --ollama --v2-planner llm --v2-planner llm-dag --llm-timeout 90 --llm-repair --output logs/qwen-repair-full-dev-on.json
```

Keep regression tests with simulated model output out of the performance
denominator. Two sequential local runs can have different latency because of
model loading, caching, and system load, even when both use the same number of
model calls.

### Measured development run

Both arms used all 16 existing development questions, the same manifest and
implementation hashes, and the same installed Qwen3 4B digest.

| Planner | Correct off → on | Structurally valid off → on | Validation failures off → on | Median seconds off → on | Total tokens off → on |
| --- | ---: | ---: | ---: | ---: | ---: |
| Qwen single-plan | 11/16 → 11/16 | 14/14 → 14/14 | 3 → 3 | 5.99 → 7.32 | 8,456 → 8,429 |
| Qwen DAG | 12/16 → 12/16 | 14/14 → 14/14 | 6 → 7 | 7.82 → 7.26 | 8,251 → 8,267 |

Both arms made 14 model calls per planner and refused all three security
cases. No response triggered repair. The live run therefore provides no
evidence of a repair benefit. Tests exercise structural repairs with simulated
provider responses and do not enter this comparison. The no-repair DAG run
also contained an extreme elapsed-time outlier, retained in the raw report;
tail latency from this run should not be used to estimate repair overhead.
