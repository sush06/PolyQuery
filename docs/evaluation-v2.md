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

## Qwen DAG grounding ablation

The 16-question development report for the committed no-repair Qwen DAG had
14 structurally valid proposals and six semantic validation blocks. The blocks
included one compound LDBC request where the model proposed only the count.
Five requests fell outside the verifier's recognized intent. Three of those
were answerable benchmark paraphrases, and two were ambiguous requests.
Thus a capability diagnostic describes what the verifier could certify. It
does not prove that the underlying catalog lacks an operation.

The semantic preflight checks the proposal against locally recognized,
source-scoped operations, exact parameters and graph scope, binding contracts,
and the order and completeness of requested outputs. It rejects unverifiable
plans before dispatch. The report separates JSON shape checks, catalog checks,
and correctness against the independent answer. Failure categories distinguish
unsupported operations, invalid parameters, wrong graph scope, bad bindings,
unverified dataset capability, unnecessary abstention, and wrong plans.

The enriched catalog derives operation descriptions, input types, result
semantics, and binding compatibility from catalog metadata. It also exposes
the current router's preferred backend and graph endpoint semantics. The
examples variant adds at most one verified development sequence per source.
An example's stages and literal arguments must match the authored development
reference. The runner records which examples it used.

```bash
python -m polyquery.cli evaluate --suite v2 --split validation --ollama --llm-timeout 90 --v2-planner dag --v2-planner llm-dag --v2-planner llm-dag-catalog --v2-planner llm-dag-examples --output logs/qwen-grounding-validation.json
```

The command uses identical decoding settings across the three model variants
and disables repair. The full validation split supplies the same questions for
all arms. The enriched prompts are opt-in, and no held-out cases or labels
are used in the ablation.

### Live validation results

All arms evaluated the same 18 validation questions. Each Qwen arm made
17 model calls. The examples arm used `chinook-multi-1`, `ldbc-multi-1`,
and `taxi-multi-1` from development.

| Planner | Correct outcomes | Invalid plans | Semantic blocks | Plain abstentions | Incorrect answers | Tokens | Median latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Deterministic DAG | 14/18 | 0 | 1 | 9 | 0 | — | 0.52 ms |
| Qwen DAG | 10/18 | 4 | 4 | 6 | 0 | 9,851 | 8.00 s |
| Qwen with enriched catalog | 6/18 | 7 | 7 | 1 | 0 | 11,767 | 13.72 s |
| Qwen with catalog and examples | 13/18 | 0 | 10 | 1 | 0 | 12,266 | 7.95 s |

Examples raised correctness by three paired cases versus baseline Qwen
with zero losses. The paired 95% interval was 0 to 33.3 percentage points,
and deterministic DAG still scored one case higher. Catalog enrichment
alone reduced correctness. Its two adversarial invalid plans failed closed
before execution and received no refusal credit. No arm returned an
incorrect answer. The examples arm eliminated catalog-invalid outputs, but
semantic blocks remained, including a graph-scope mismatch. These results
come from one small validation run. Timing may reflect model warmup and
system load across sequential arms.

## Four-arm grounding and semantic-block audit

The four-arm run uses all 18 existing validation questions. Examples-only
appends the same single verified development sequence per source to the
original Qwen DAG prompt. Both example arms use the same sequence and the
catalog-only and combined arms use the same enriched catalog.

```bash
python -m polyquery.cli evaluate --suite v2 --split validation --ollama --llm-timeout 90 --v2-planner llm-dag --v2-planner llm-dag-catalog --v2-planner llm-dag-examples-only --v2-planner llm-dag-examples --output logs/qwen-grounding-four-arm.json
```

Each row records whether semantic preflight was reached, accepted, or
rejected. Accuracy credits correct refusals. The answered count measures
requests that returned a value. Pairwise transition tables keep the scored
correctness and the observed response outcome in separate columns.

For each semantic block, the audit records typed, catalog-valid proposal
evidence and checks the ordered outputs against independent reference
operations and arguments. A block is a true positive when the reference
requires refusal or the proposal returns incomplete or different outputs.
A block is a false positive when the proposal uses equivalent catalog
operations and exact reference arguments for every requested output. Cases
outside the independent reference's scope remain indeterminate pending manual
review. The audit does not execute rejected proposals. Literal identifiers
remain redacted, and the report records whether the arguments matched.

The four-arm run recorded false positives with the preflight in effect at the
time. It left authorization, repair settings, and held-out labels unchanged.

### Results

Each arm scored all 18 validation questions and made 17 model calls. Both
example arms used the same three development sequences.

| Arm | Correct decisions | Answered | Invalid plans | Semantic blocks | Incorrect answers | Security refusals | Tokens | p50 / p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline Qwen DAG | 10/18 | 4 | 4 | 4 | 0 | 3/3 | 9,851 | 8.34 / 24.25 s |
| Catalog only | 6/18 | 3 | 7 | 7 | 0 | 1/3 | 11,767 | 14.21 / 25.75 s |
| Examples only | 13/18 | 7 | 2 | 4 | 0 | 3/3 | 11,049 | 6.17 / 10.29 s |
| Catalog and examples | 13/18 | 7 | 0 | 10 | 0 | 3/3 | 12,266 | 6.88 / 10.06 s |

The paired accuracy difference versus baseline was −22.2 points for
catalog only (95% interval −44.4 to 0), +16.7 for examples only (−5.6 to
+38.9), and +16.7 for the combination (0 to +33.3). Adding catalog
enrichment to the examples-only arm changed accuracy by zero points
(−16.7 to +16.7), with one paired gain and one loss. The per-question
report contains all six transition tables. This validation run is too
small to establish generalization, and sequential latency includes system
and model state variation.

The audit reviewed all 25 semantic blocks. There were 15 false positives,
10 true positives, and zero indeterminate cases. The false positives
included valid answers to supported paraphrases, an equivalent Taxi backend,
and one-output DAGs with the correct operation. The true positives included
unsupported or security-sensitive requests, an incomplete compound answer,
and a mismatched graph request. Review of the refusal cases confirmed that
their time, location, alternate database, or raw-export constraints cannot
be authorized by the existing source contracts. Catalog-only invalid plans
on two adversarial cases were blocked before preflight and execution and
received no refusal credit.

### Follow-up verifier fix

Qwen DAG preflight now verifies supported count, arithmetic-mean, and direct
graph-neighbor paraphrases with whole-clause patterns and the existing
constraint guards. Alternate backends must implement the same source-local
catalog operation with the same effective parameters. A single-output DAG can
answer a single-stage request when it contains exactly that verified operation
and no bindings. Regression tests now execute the previously blocked
equivalent plans and check their answers against independent references.
