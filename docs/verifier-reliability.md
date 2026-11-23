# Semantic verification reliability

The typed verifier now independently recognizes a bounded ordered-list
envelope and complete Chinook measure phrases. The phrase mapping is
limited to existing catalog operations with matching descriptions,
parameter shapes and scalar result contracts. The ordered verifier
checks operation, scope, dependencies, and requested output order. It
also recognizes explicit ascending employee-name order. Other wording
requires existing independent evidence before it can execute. The
catalog, model schema, authorization rules, and physical selector are
unchanged. No change enables typed planning by default.

The historical replay remains in
[the regression fixture](../benchmarks/ordered-verifier-regression.json).
It contains the original wording, normalized proposal IDs, and the
canonical ordering edge added during the earlier audit to reconstruct the
typed proposal. Before the change, that plan abstained on the original
wording. It now executes and matches independently computed gold.

## Decision taxonomy

- **Verified match** means the expected operations, parameters,
  dependencies, bindings and requested outputs agree.
- **Verified mismatch** means the verifier derived the request's intent
  and found a conflict in the proposal.
- **Insufficient evidence** means the limited recognizer cannot establish
  the intent. It never licenses execution.
- Schema, catalog/binding, and authorization rejections precede the
  semantic comparison and retain their existing categories.

The independent harness varied count versus sum, filtered versus entire
population, ascending versus descending, first versus bottom of a named
ordering, whole-result versus row-field binding, omitted and extra
outputs, and missing dependencies. Ambiguous rankings and
unsupported requirements still abstain.

## Results

The 16 reviewed cases yielded four true acceptances, 12 correct
rejections, zero false acceptances, and zero false rejections. Five of
the 12 rejections reflected insufficient evidence. The reviewed
verification p50/p95 was 0.62/2.91 ms, including local plan decoding
and catalog checks, without model inference.

The Qwen3 4B live set contained two answerable questions and one unsafe
request. Each answerable question had one shared model draw. The prior
verifier accepted and answered 1/2, and the revised verifier accepted and
answered 2/2. Both refused the unsafe request before inference. The prior
failure was a semantic rejection. Neither side recorded a generation or
catalog failure on this sample. Qwen planning time, including the revised
verification, is shared in both end-to-end columns, while baseline
verification replays the same locally captured proposal.

| Case | Prior end-to-end | Revised end-to-end |
| --- | --- | --- |
| Checklist revenue and customers | 39.00 s, semantic rejection | 39.01 s, correct |
| Ordered track and invoice counts | 17.45 s, correct | 17.45 s, correct |
| Unauthorized track modification | Refused before inference | Refused before inference |

Fixed SQL Server and learned routing received identical verified plans
and both gave correct answers on both cases. The learned selector chose
SQLite. Their median execution times per case were 3.04 versus 1.31 ms
and 2.06 versus 1.25 ms, respectively. This local control used an existing
restricted database-only SQL Server reader and is too small to establish
a routing gain. Natural-language coverage remains bounded. New ranking
semantics, filters, and requested fields require independent evidence.

```powershell
python -m benchmarks.verifier_reliability
python -m benchmarks.verifier_reliability_live --server
```

[Machine-readable summary](../benchmarks/verifier-reliability-summary.json)
