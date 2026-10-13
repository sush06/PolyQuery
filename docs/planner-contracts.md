# Planner contracts and source catalogs

The engine now validates plans and results against a source-scoped catalog.
`ParameterSpec`, `ResultSpec`, and `OperationSpec` declare required fields,
nullability, value types, known entity domains, and eligible backends. Existing
`Plan` construction remains compatible; serialized planners use `Plan.from_dict`.
Operation-specific validation happens at the engine boundary, before execution.
Backend validation remains in place for direct adapter calls.

```python
from polyquery import Engine
from polyquery.planner import Plan

plan = Plan.from_dict({"backend": "sql", "operation": "count",
                       "parameters": {"department": "Research"}})
with Engine() as engine:
    engine.catalog.validate_plan(plan)
    description = engine.catalog.to_dict()
```

The catalog contains schema descriptions, operation parameters/results, backend
capabilities, and cost provenance. It does not expand the keyword grammar.

```powershell
python -m polyquery.cli describe-source
python -m polyquery.cli describe-source --source chinook --data benchmarks/Chinook_Sqlite.sqlite
python -m polyquery.cli describe-source --source ldbc --people benchmarks/people_0_0.csv --knows benchmarks/person_knows_person_0_0.csv --cost-profile benchmarks/challenge-v1/ldbc-cost-profile.json
python -m polyquery.cli describe-source --source taxi --data benchmarks/yellow_tripdata_2026-01.parquet
```

Chinook describes actual SQLite columns, primary-key positions, and foreign keys,
and marks which tables participate in execution. Taxi describes all Parquet
fields separately from its loaded projection. Merely seeing a field in a file
does not imply that an adapter supports a query over it. The synthetic and LDBC
catalogs describe their explicit projections and relationship direction.

Entity identity is scoped by source and entity type. Synthetic employee names
and LDBC person IDs are strings from their loaded domains. Large LDBC identifiers
remain strings. Taxi has no declared stable trip key; row positions cannot be
used as portable identities. There is no automatic cross-source entity matching.

Returned values are checked for scalar types, finite numbers, count sign,
record fields, entity domains, and path endpoints/edges. Null numeric aggregates
and disconnected paths have explicit meanings. These checks establish structural
contracts, not answer correctness: shortest-path minimality and numeric gold
agreement remain evaluator responsibilities.

With a cost profile, the catalog exposes measured execution estimates, unseen
operation fallback estimates, training/data hashes, and calibration time. Without
one, cost is explicitly unmeasured. Cost-aware selection intersects estimates
with declared operation capabilities. It does not reinterpret questions, refit
costs, or use challenge test answers.

Frozen challenge partitions, profiles, and historical reports are preserved.
Validation overhead changes current latency, so historical timings should not be
silently replaced. New timing experiments should record the implementation hash.

This milestone remains single-step and local. A future multi-stage plan needs
typed intermediate tables, key/identity checks, null and cardinality semantics,
and validation of dependencies between steps. PostgreSQL/Neo4j adapters and
trained planners can implement these contracts later. The optional [LLM planner](llm-planner.md)
implements the current single-step contracts.
