# Research design

## Hypothesis

Capability- and schema-aware routing can improve the tradeoff among correctness, latency, and compute cost over a fixed-backend policy. Backend selection is a planning decision, not a claim that a question belongs to exactly one query language.

## Current implementation

The v0.1 planner detects aggregate, traversal, and statistical intent, fills fixture-specific slots, generates a backend plan, and abstains on detected ambiguity. SQL is native SQLite execution; graph traversal is native BFS; generated code is a restricted statistical expression interpreted from its AST. All three use a consistent synthetic employee fixture. Cross-backend multi-stage execution is future work.

## Dataset protocol for a research result

Chinook, LDBC person/knows, and the full supplied Taxi file now have development
suites comparing actual answers across equivalent execution strategies. LDBC
uses graph BFS and SQL frontier joins; Chinook and Taxi use SQL and fixed Python
templates. The suites are authored development data with computational reference
answers, not independently reviewed held-out labels. See
[real-data benchmarks](real-data-benchmarks.md) for commands and limitations.
Record dataset hashes, completeness, initialization, and query timings separately;
freeze question families before held-out evaluation.

1. Build equivalent relational tables and graph projections across several domains, including schema distractors and missing values.
2. Annotate gold answer, constraints, acceptable execution strategies, and an oracle strategy measured on the same data. Have a second reviewer adjudicate disputed labels.
3. Split by schema/domain and question family before prompt or router development; keep paraphrases of a family in one split.
4. Include single-backend, multiple-valid-backend, composite, ambiguous, unsupported, and adversarial questions. Gold routes can be sets; do not penalize a correct efficient alternative solely for using SQL.
5. Freeze router configuration and evaluate on an untouched test split. Report bootstrap confidence intervals and multiple seeds for stochastic policies.

The demo cases were developed alongside the rules. Use a separate held-out set to measure generalization.

## Comparisons

| Policy | Purpose |
| --- | --- |
| Fixed SQL with recursive CTEs and statistical capabilities | Fixed-backend answer and cost comparison |
| Random among capable backends | Random selection comparison |
| Keyword router (v0.1) | Deterministic rule baseline |
| Schema-aware trained or LLM router | Main proposed method |
| Oracle measured strategy | Upper reference and routing regret |

Keep data, allowed resources, timeout, and answer scoring identical. Separate router cost from execution cost. A SQL route-label score alone does not establish superiority over SQL.

## Metrics and ablations

- Answer/execution accuracy; constraint satisfaction; chosen strategy in acceptable set.
- Coverage versus correctness among answered requests; abstention precision/recall.
- End-to-end p50/p95, planning time, backend time, cold-start separately, tokens and monetary cost if an LLM is used.
- Execution cost regret relative to oracle, measured only among correct answers.
- Remove schema descriptions, capability metadata, cost estimates, and ambiguity gating independently.
- Test increasing graph size/depth, relational row count, and statistical complexity.

## Known limitations

The baseline is lexical and can misinterpret unrecognized qualifiers. It does not discover schemas, persist data, run Cypher, call LLMs, combine backends, calibrate confidence, or isolate arbitrary Python. No empirical claims about production latency or model accuracy are made. Amounts are fictional USD; standard deviation uses sample semantics; reporting traversal includes all depths; graph shortest path ignores edge direction.
