# Opt-in typed Qwen bindings

The executor previously supported a whole result, a position in a path or
entity list, and a field in a result row. Qwen's schema exposed the first
only. The typed opt-in exposes the executor's existing positional paths
and explicit ordering edges. Production defaults and the legacy Qwen
schema remain available.

Node objects include `depends_on`. An input binding includes `name`,
`node`, and `path`. The name identifies the consumer parameter. The node
and path deserialize directly to `InputBinding(node, tuple(path))`.
A binding is itself an explicit data dependency. Use `depends_on` for
ordering edges. For example, `{"name":"source","node":"neighbors","path":[2,"id"]}`
reads the third neighbor's person ID. Empty paths preserve whole-value
bindings, and a single nonnegative index selects a path or entity-list
member. The opt-in caps projected indices at 100 and keeps the existing
five-node, five-output limit.

The DAG executor validates producer operations, parameter types,
dependency cycles, and field paths before dispatch. Typed model plans
also require source-local identity fields when projecting into a domain
such as person or employee. Runtime checks still validate the actual
values and output contracts. An index that falls outside a real result
fails at runtime. No arbitrary SQL or new backend operation is exposed.

## Development partition

| Case | Independent reference dependency |
| --- | --- |
| Endpoint to neighborhood | Select index 1 from an LDBC shortest path and bind that person to friends. |
| Third-neighbor projection | Select index 2 and field id from LDBC friends and bind that ID to friends. |
| Roster to direct reports | Select index 1 and field name from a Research roster, then return immediate reports. |
| Roster to downstream reports | Select index 1 and field name from an Engineering roster, then return transitive reports. |

The partition also includes an ordered Chinook query and ambiguous and
unauthorized requests. Independent reference operations read the source
data and verify the expected DAG results. Separate negative controls
inject missing producers, nonexistent fields, numeric-to-identity
mismatches, cycles, and a valid but wrong index. These controls have no
provider calls and are excluded from the live model metrics.

The ordered verification fix recognizes explicit ordering connectors and
verifies every complete stage separately. It compares operations, scopes,
dependencies and output order. The projected verifier checks a small
allowlist of complete graph and roster intent forms. A valid dependency
shape alone never authorizes execution. Free-form compound requests can
still abstain. The existing supported graph-to-statistics path is unchanged.

## Reproduce

```powershell
python -m benchmarks.typed_bindings_evaluation
python -m benchmarks.intelligent_routing evaluate --typed-bindings --server --output logs/intelligent-routing-typed-heldout.json
python -m benchmarks.report_typed_bindings
```

The two development arms use the same Qwen3 4B model digest, catalog
grounding, temperature, inference limit, and timeout. The opt-in changes
the JSON schema and describes the new contract. It uses no prompt examples
from the sealed partition. The held-out run keeps the original manifest,
gold, and physical calibration. It shares each accepted logical decision
across the fixed, learned, and oracle execution arms. Reference controls
remain separate from generated plans.

## Development results

Each arm saw seven requests and made six provider calls. The safety gate
refused the unauthorized request before inference in both arms.

| Measure | Legacy | Typed |
| --- | ---: | ---: |
| Structurally valid / provider calls | 5/6 | 3/6 |
| Catalog valid / provider calls | 1/6 | 1/6 |
| Accepted / correct answers | 0 / 0 | 1 / 1 |
| Correct refusals | 1 | 1 |
| Tokens | 5,370 | 6,846 |
| End-to-end p50 / p95 | 26.3 s / 44.3 s | 36.0 s / 61.2 s |

The accepted answer was the ordered Chinook query. None of the four
projected-binding requests produced a verified model plan. Typed generation
had three schema failures and two binding failures. Legacy generation had
one schema failure, four binding failures and one semantic block. The
extended contract used more tokens and had higher measured latency on this
small partition.

## Frozen held-out rerun

Legacy and typed Qwen each accepted 0/12 plans, answered 0/8 answerable
requests, and correctly refused both unauthorized requests. Each arm made
10 calls. Tokens increased from 10,071 to 12,526. End-to-end median latency
was 29.7 s legacy and 37.6 s typed. The typed run's p95 was 1,996.3 s
versus 48.6 s legacy. The typed p95 includes a single unexplained 4,354.6 s
wall-clock observation. It is retained in the raw data and prevents a
meaningful held-out tail-latency comparison.

The typed run recorded five binding failures, one invalid-parameter
failure, one schema failure, and three semantic rejections. The verified
ordered development syntax did not cover the sealed ordered question.
Its new proposal matched the expected operations and literal parameters
but failed semantic verification. The possible Research-scope false
positive could not be resolved. Original literals were redacted, and the
new proposal failed the typed binding checks before semantic review.

The fixed, learned, and oracle arms had no accepted generated plans to
compare. The separate reference-plan control reached 10 comparable
physical choices and matched all 10 oracle selections. [Machine-readable comparison](../benchmarks/typed-bindings-summary.json)
