# Experimental semantic IR for Qwen

PolyQuery can opt into a compact Qwen recipe and compile it to the existing
typed DAG. The legacy and direct typed planner modes remain available with
their original defaults.

## Why test a compiler

The previous binding-development run had six provider calls per mode.
Legacy produced one schema failure, four dependency failures and one
semantic rejection. Direct typed produced three schema failures and two
binding failures, and answered the ordered Chinook request. The seventh
request was refused before inference by both arms. No provider output in
that run was classified as malformed JSON. Several visible proposals
supplied a literal and a binding to the same parameter or bound a whole
record set to a scalar. Redaction prevents identifying every wrong
literal or property.

The compiler can pick the first catalog-approved backend, derive the
unique eligible parameter given compatible upstream data, and the unique
identity field for a projected row. The requested position, role, literal
scopes, ordering, and outputs depend on the question. The IR keeps those
choices explicit or rejects ambiguity. It never invents filters or measures.

## Recipe and checks

Each step contains an operation key, literal args, links, and order-only
dependencies. Step references and selected positions are one-based.
A link to an earlier step may use an explicit input or field. Null asks
the compiler to derive that value only if exactly one catalog-approved
route exists. Null item selects the whole output. The compiler rejects
unknown operations, missing or incompatible edges, cycles, duplicate
outputs, and excess cardinality. The compiler's generated bindings use
the executor's existing zero-based `InputBinding` paths.

The semantic verifier remains separate. It checks requested scopes,
bindings, operations, and output ordering across all accepted proposals.
For this experiment, the shared typed verifier recognizes two bounded
neighbor-to-route forms that distinguish the source from the target.
That check is applied equally to the direct typed and IR arms. A compiled
plan may still fail semantic verification. No arbitrary SQL is exposed.
Catalog operations and runtime result checks remain unchanged.

## Reproduce

```powershell
python -m benchmarks.typed_bindings_evaluation --semantic-ir --output logs/semantic-ir-development.json
python -m benchmarks.intelligent_routing evaluate --semantic-ir --server --output logs/intelligent-routing-semantic-ir-heldout.json
python -m benchmarks.report_semantic_ir
```

The development questions are new and their normalized operation/dependency
shapes are disjoint from the sealed set. The forward neighbor-to-route
case shares a dependency shape with one older validation question. The
reverse target-binding case has a distinct shape. The three arms share Qwen3 4B, temperature, output
limit, timeout and fixtures. Gold is computed separately from the planner.
There is one generated decision per case and arm. Provider calls spent
on authorization refusals are zero. The frozen held-out run keeps its
original manifest and independent reference checks. Its three physical
arms share the same generated logical decision. The reference-plan
control is reported separately.

## Development results

| Measure | Legacy | Direct typed | Semantic IR |
| --- | ---: | ---: | ---: |
| Provider calls | 4 | 4 | 4 |
| Structurally valid | 3 | 4 | 2 |
| Catalog valid | 1 | 1 | 0 |
| Semantically accepted / correct answers | 0 / 0 | 1 / 1 | 0 / 0 |
| Correct refusals | 1 | 1 | 1 |
| Tokens | 3,002 | 3,840 | 2,168 |
| End-to-end p50 / p95 | 29.3 s / 35.9 s | 37.6 s / 49.9 s | 28.1 s / 31.0 s |

| Request | Legacy | Direct typed | Semantic IR |
| --- | --- | --- | --- |
| Forward neighbor-to-route | Schema | Parameters | Binding |
| Reverse neighbor-to-route | Binding | Binding | Binding |
| Ordered invoice median and revenue | Semantic rejection | Correct | Schema |
| Ambiguous graph ranking | Binding | Binding | Schema |
| Unauthorized data change | Refused | Refused | Refused |

The compiler resolved omitted IDs, identity fields and input roles in
controlled valid inputs. Neither valid IR behavior nor the token savings
translated into improved model proposals here. The two structurally valid
generated recipes failed binding validation before compilation completed.
No live IR development proposal reached semantic acceptance.

## Frozen held-out result

| Measure | Prior direct typed | Semantic IR |
| --- | ---: | ---: |
| Generated plans accepted | 0/12 | 0/12 |
| Correct answers / answerable requests | 0/8 | 0/8 |
| Answer coverage | 0% | 0% |
| Correct refusals | 2/4 | 4/4 |
| Provider calls / tokens | 10 / 12,526 | 10 / 6,478 |
| End-to-end p50 / p95 | 37.6 s / 1,996.3 s | 15.5 s / 44.9 s |

The prior typed p95 includes an unexplained wall-clock outlier. The IR's
five schema failures and two binding failures account for seven of the
eight answerable requests. It explicitly abstained on the eighth. Two
provider-generated abstentions resolved the unsupported and ambiguous
requests. Both security refusals happened before inference. The additional
correct refusals came from model decisions, rather than the compiler
repairing a generated DAG.

There were no comparable generated plans across the fixed, learned, and
oracle execution arms. The separate reference control matched the oracle
on all 10 comparable choices. Keep IR experimental. Its lower token use
and better refusal coverage are useful, while direct typed was the only
mode that answered a development question. [Machine-readable results](../benchmarks/semantic-ir-summary.json)
