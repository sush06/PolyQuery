# Paired keyword and LLM comparison

`compare-routers` runs the revised draft label pool against the same local data
and questions. It uses the keyword planner and an explicit LLM, with fallback
disabled. Within each source, it alternates which router runs first by question
and repetition. Each router keeps its own engine; initialization and independent
reference scoring are excluded from query latency. There is no warmup, so first
model requests can include model loading. Report that overhead rather than
silently treating it as steady-state latency.

Run from the repository root. `benchmarks/router-sources.json` names the four
sources; paths are relative to that file. It points to the actual downloaded
Chinook SQLite, Taxi Parquet, and people/knows CSVs, plus the synthetic fixture.
Missing files fail before evaluation. Keep datasets and reports local.

```powershell
python -m polyquery.cli compare-routers --model qwen3:4b-instruct --llm-api chat --llm-base-url http://localhost:11435/v1 --llm-timeout 120 --output logs/router-comparison-qwen-01.json
```

That example requires a running local server with the named model; it neither
installs a runtime nor downloads a model. For the default hosted Responses
transport, set `OPENAI_API_KEY` in the process environment and pass an explicit
`--model`. Never put keys in source configuration or reports.

When no model endpoint is available, this explicitly incomplete run is useful:

```powershell
python -m polyquery.cli compare-routers --keyword-only --output logs/router-comparison-keyword-01.json
```

Outputs cannot overwrite existing files. `logs/` is Git-ignored. The console
shows summaries; the local report retains per-case outcomes and plans, without
answer payloads, provider response IDs, or raw exception messages.

## Inputs and scoring

Both engines receive the original question and a separate whitelist of public
policy context. Gold canonical labels, annotation rationales, witnesses, and
`intended_semantics` are excluded. The keyword planner currently ignores context;
the LLM receives it as task text. Lexical guards see the original question so
policy prose does not create accidental keyword matches or spurious constraints.
The report hashes the exact paired inputs. This is a comparison of the current
systems, including their different ability to consume context, not a pure model
architecture experiment.

There are **112 canonical**, **36 clarification**, and **12 representation-gap**
questions. Rates explicitly identify their denominator:

| Metric | Meaning |
| --- | --- |
| Proposed constraint preservation | Fraction of canonical runs whose proposed operation semantics exactly preserve the authored canonical structure, including plans later blocked by guards. Abstention and error score false. |
| Executed constraint preservation | Same check among successfully answered canonical runs. |
| Answer accuracy | Correct answers divided by all canonical runs; abstentions/errors are incorrect. |
| Answered accuracy | Correct answers divided by successfully answered canonical runs. |
| Clarification abstention recall | Abstentions divided by clarification runs; not added to answer accuracy. |
| Abstention, coverage, error, guard-block rates | Separate rates across all runs, with canonical abstention also reported. |
| Latency | Mean, p50 and p95 for total, planning and execution time; correct-answer latency is separate. |
| Cost | API calls, input/output tokens, and an optional token-price estimate. |

Constraint scoring compares the proposed plan's registered implementation
semantics against gold. It does not reuse the guard's extracted question as the
reference. Structural agreement is stricter than logical equivalence and does
not prove equivalence on every possible database. Unsupported operation contracts
remain a capability limitation, rather than becoming correct abstention labels.
Gap questions have no complete canonical or answer score; their statuses still
contribute to coverage, latency, and cost. Do not silently drop them from those
totals.

Reference code in `polyquery/label_oracle.py` executes authored canonical labels
without calling a router or application backend. It uses SQLite for synthetic,
Chinook, and a full-file Taxi reference projection, and an independently loaded
CSV adjacency map for bounded paths. Taxi includes pickup timestamps even though
the current application adapters only expose numeric statistics. SQL implements
three-valued filters and grouping; empty groups disappear and all-null sums stay
null. Numeric comparisons use the existing answer tolerance. Ordered projections
stream their references, and unordered grouping ignores row order.

Employee limits use full-row membership, uniqueness, and cardinality, accepting
any valid subset. Bounded paths accept any tied shortest path within the limit;
an over-bound or disconnected case requires a successful null, never an error or
abstention. The reference implementation has semantic tests but still needs
independent review, as do the authored labels.

Optional prices are explicit USD per million tokens:

```powershell
python -m polyquery.cli compare-routers --model YOUR_MODEL --input-usd-per-million INPUT_RATE --output-usd-per-million OUTPUT_RATE --output logs/router-comparison-hosted-01.json
```

Replace rate placeholders with verified prices for the chosen model. This simple
estimate does not account for discounts, cached-token tiers, or other billing
details. Missing usage or pricing produces null, not zero. No API calls means
zero API charge; local compute, hardware and energy costs remain unmeasured.

## Interpretation

The report includes source, labels, splits, exact input and implementation hashes;
per-source and per-family summaries; and paired wins/losses on canonical answers.
Errors remain in denominators. Family paraphrases and repeated runs are correlated;
the report does not claim statistical significance. This is an exploratory run
over the whole draft pool, including proposed train/validation/test partitions,
not an independently labeled held-out experiment. Do not use it to claim a
general router advantage, or tune on these questions and later call them unseen.

Many hard questions exceed today's operation catalog. Inspect both capability
and routing failures before expanding plans. A fast abstaining system cannot be
called a latency winner without considering coverage and correctness. Compare
capable fixed-backend systems separately before making a routing-benefit claim.
