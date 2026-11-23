# Intelligent routing

The opt-in pipeline sends the question to the catalog-grounded Qwen3 4B
DAG planner. The existing semantic verifier and DAG executor still check
the logical operation, parameters, dependencies, bindings, and outputs.
The physical selector runs after named input binding. No relation or SQL
text from the model reaches an adapter. The existing keyword planner and
default routing behavior remain unchanged.

Chinook's five reviewed SQL aggregates can use SQLite or SQL Server.
LDBC person count, friends, and shortest paths can use in-memory graph
execution or SQL Server frontier execution. Both SQL Server adapters
verify projection identity and effective read permissions. Other logical
operations keep their original backend. The fixed benchmark arm uses SQL
Server for the reviewed mappings and the original backend elsewhere.

## Reproduce

Use the original composition manifests and source catalog, the raw
Chinook SQL Server comparison and LDBC frontier development reports,
and a local Ollama endpoint with `qwen3:4b-instruct` installed.

```powershell
python -m benchmarks.intelligent_routing train
python -m benchmarks.intelligent_routing evaluate --server
python -m benchmarks.report_intelligent_routing logs/intelligent-routing-results.json benchmarks/intelligent-routing-summary.json
python -m benchmarks.intelligent_routing query --source chinook --question "How many tracks?" --server
```

Set the existing least-privilege SQL Server reader environment variable for
`--server`. The command never tries a loader or privileged connection.
Omit that option to run locally. Reviewed operations with only one
available implementation use it. The fixed SQL Server control records
unavailable if its configured implementation cannot run. See the
[SQL Server setup](sqlserver.md) and [LDBC projection loader](ldbc-sql-graph.md).

## Features and decisions

The model takes source and operation plus cardinality, graph adjacency
count, source degree, estimated selectivity, join count, a known or
unknown hop estimate, index availability, residency, and connection state.
The shortest-path feature only knows zero hops when both endpoints are
identical. The model never runs a traversal to estimate distance. All
registered candidates are already initialized in this warm experiment, so
connection and residency features do not establish cold-start behavior.

For each candidate, up to five nearby development cases estimate median
completion latency and empirical timeout frequency. Expected cost combines
the completion latency with the benchmark timeout penalty. Missing
calibration, feature distance above 1.5, and a small predicted cost margin
use the reviewed local implementation. The report includes the feature
vector, predictions, eligibility, selection, adapter elapsed time, and
outcome. The training CLI uses existing development runs only. The
held-out gold answers and oracle timings do not update the profile.

## Evaluation interpretation

The natural-language arms compare keyword planning, deterministic DAG,
and identical Qwen proposals sent to fixed, learned, and oracle physical
execution. One model draw per question is shared by all Qwen arms. Strict
plan match checks operations, parameters, bindings, dependencies and
output ordering while ignoring node names and reviewed backend identity.
Independent value comparison is recorded separately. Model failures and
safety abstentions have their own outcomes. Local Ollama token usage is
reported when available. Dollar cost is unknown.

The reference-plan control executes the reviewed logical plans with
zero model time to isolate the physical selector from semantic coverage.
It is reported separately from end-to-end routing. The oracle probes
eligible candidates on the bound plan and uses the fastest completing
median. Its probe overhead is recorded separately. Workload families are
disjoint from the composition validation set. All measurements are local,
warm, and small-sample. Reference answers are independent computations
over the repository datasets. These question families are repository
authored and have not been independently collected.

## Original measured result

On the 12 frozen questions, keyword and deterministic DAG each correctly
refused four requests and answered none. The shared Qwen run produced six
unsupported-capability blocks, five schema failures, and one binding
failure. No Qwen decision reached execution, including both adversarial
requests. Validated refusals are scored separately from failed plans that
also resulted in no execution. Qwen used 11,481 tokens over 12 shared calls,
with 29.4 s p50 and 51.0 s p95 end-to-end latency.

In the separate reference control, all eight answerable DAGs returned
correct answers under each strategy. The learned selector matched 10/10
comparable node-level oracle choices with median regret of 0 ms. Median
execution was 1.08 ms learned, 6.90 ms fixed, and 0.93 ms oracle excluding
its probes. Median selection time was 0.081 ms per reference DAG. These
figures establish the behavior of the physical control on these DAGs.
The semantic pipeline had zero answer coverage, so the experiment does
not establish an end-to-end gain. The direct Windows reader login remains
unverified. [Machine-readable summary](../benchmarks/intelligent-routing-summary.json)

The [verification audit](intelligent-routing-audit.md) separates the original
schema, binding, semantic, and safety failures and documents the rerun.
