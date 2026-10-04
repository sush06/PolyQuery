# Local development benchmark results

All supplied data rows; 30 authored questions across three sources; three measured repetitions per policy after one warmup pass. All 270 measured policy/query runs passed answer scoring with zero execution errors. Repetitions are not independent question samples.

| Source | Policy | Cases | Runs | Answer accuracy | p50 ms | p95 ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| chinook | router | 10 | 30 | 100% | 0.291 | 1.158 |
| chinook | fixed-sql | 10 | 30 | 100% | 0.337 | 1.154 |
| chinook | fixed-code | 10 | 30 | 100% | 0.094 | 1.023 |
| ldbc | router | 11 | 33 | 100% | 0.072 | 16.383 |
| ldbc | fixed-sql | 11 | 33 | 100% | 0.233 | 92.630 |
| ldbc | fixed-graph | 11 | 33 | 100% | 0.100 | 17.041 |
| taxi | router | 9 | 27 | 100% | 84.128 | 686.534 |
| taxi | fixed-sql | 9 | 27 | 100% | 527.640 | 1150.267 |
| taxi | fixed-code | 9 | 27 | 100% | 5.041 | 79.858 |

Timings include abstentions and use a shared exact-grammar planner. Initialization, including all projections and the Taxi fare index, is excluded here and recorded in the JSON reports. Runs were sequential in router, fixed-sql, alternate-backend order on one local machine. This is an initial development observation, not a held-out routing result.

The fixed Python/Arrow policy was faster than the current router on this Taxi workload. The router selects SQLite for most aggregates without using cost measurements. Graph BFS was faster than the SQL frontier strategy on the authored LDBC path cases. Chinook timings are too small for strong performance claims from a single process run. These results support investigating cost-aware selection, not claiming that the current router wins.

Exact timings, source hashes, run timestamps, gold provenance, family summaries, and answers are in [Chinook](comparison-chinook.json), [LDBC](comparison-ldbc.json), and [Taxi](comparison-taxi.json). See [benchmark methodology](../docs/real-data-benchmarks.md).
