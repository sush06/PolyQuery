# Opt-in physical SQL routing

The natural-language planners and typed DAGs continue to emit logical
`sql` operations. SQLite remains the default. For Chinook, an application
can pass `Engine(..., physical_sql_selector=selector)`. The selector
receives a validated, fully bound logical plan and returns `sqlite` or
`sqlserver`. Use `polyquery.physical_sql.fixed_sqlite` for a deterministic
control or call `PhysicalCostModel.choose(SQLFeatures(...))` from the
selector. Supply cardinalities and estimates from the deployment's
metadata and calibrate with its own development workload. The included
experiment's model is calibrated for the benchmark projection.

The opt-in production mapping is limited to the five implemented Chinook
SQL Server aggregates. Existing permissions and fixture-fingerprint
checks run on startup. Missing connections, unsupported mapped SQL
operations, invalid decisions, and failed execution close the request
without switching backend. Operations assigned to logical graph or code
retain their original paths. The physical dispatcher and caller check
typed results. SQL comes from the existing fixed adapters.

## Held-out experiment

The versioned [manifest](../benchmarks/physical-routing-manifest.json)
fixes three test families and two nonprefix invoice-ID windows. The four
development families and both prefix cutoffs come solely from the earlier
scale benchmark. The test uses the replicated Chinook projections at
1x, 10x, 100x, and 1,000x. It adds customer activity by country via a
semijoin, a histogram of invoice basket sizes, and distinct sold-track
coverage by original genre. Independent Python reductions use original
fixture rows and deterministic ID offsets. The raw report fingerprints the
manifest, the calibration JSON, and the source fixture. This is a
manifest-frozen experiment on visible workload definitions.

The model uses log-scaled input rows and estimated group counts, join count,
and invoice-ID range fraction. Five nearby development cases vote by
inverse squared distance with a stabilizer. The development target is
the difference of log1p fixed-backend DAG medians in milliseconds. The
model receives only metadata and bound parameter estimates at selection
time. Group cardinality estimates are simple caps based on relation size
and range fraction. No answers or measured timings from the test
partition enter selection.

Reproduce with the existing verified scale projections, the original
development JSON, and a configured least-privilege reader.

```powershell
python -m benchmarks.physical_routing
python -m benchmarks.plot_physical_routing logs/physical-routing-results.json logs/physical-routing-latency.png
```

The runner reuses one checked connection per backend and scale and shuffles
fixed SQLite, fixed SQL Server, and routed DAG execution each round. It
times plan dispatch through result validation. Independent reference checks
run after each sample. It also measures the selector call alone. The oracle
uses the faster fixed median for each entire case and reuses that case's
fixed samples. Regret is routed minus oracle median latency and can be
slightly negative due to sampling noise. Wrong-backend penalty isolates the
two fixed medians. Speedup is the ratio of sums of per-case medians. Paired
bootstrap intervals resample whole cases and treat observed oracle labels
as fixed. Cases sharing a family, scale, or replicated rows may be
correlated, and the intervals are exploratory.

The local run used a Windows-authenticated lab connection that explicitly
activated the existing database-only SELECT principal with no revert
before permission checks. The standard command uses the configured reader
connection and has no impersonation or privileged fallback. The direct
Windows reader integration test still requires a dedicated identity.

For the next experiment, lock the policy and add independently designed
families, concurrent sessions, cold connections, and remote SQL Server.
Compare against whichever fixed backend is fastest on that workload mix.
