# M7.1 independent physical routing benchmark

## Finding

The existing learned policies did not beat the deterministic heuristic
on this 12-workload held-out reference-plan benchmark. They made the
same selections, and the learned policy added about 0.15 ms per warm
trial (paired case-bootstrap 95% interval 0.11 to 0.19 ms). Fixed SQL
Server timed out on ten warm and four cold trials. Both routing policies
steered those workloads to local BFS. This compares execution of
verified plans, without natural-language planning.

| Held-out policy | Warm completed | Warm p50 / p95 | Cold completed | Cold p50 / p95 | Warm / cold oracle choices |
| --- | ---: | ---: | ---: | ---: | ---: |
| Fixed local | 60/60 | 9.4 / 874.6 ms | 24/24 | 68.6 / 763.2 ms | 8/12, 11/12 |
| Fixed SQL Server | 50/60 | 28.7 / 2,033.1 ms | 20/24 | 100.1 / 2,301.3 ms | 4/12, 1/12 |
| Heuristic | 60/60 | 9.4 / 105.1 ms | 24/24 | 68.6 / 111.1 ms | 9/12, 12/12 |
| Learned | 60/60 | 9.6 / 105.2 ms | 24/24 | 68.8 / 111.2 ms | 9/12, 12/12 |
| Offline oracle | 60/60 | 9.3 / 105.1 ms | 24/24 | 68.6 / 111.1 ms | 12/12, 12/12 |

All completed results matched independent gold. Warm mean penalized cost
was 27.72 ms learned, 27.57 ms heuristic, 92.51 ms fixed local and
454.23 ms fixed SQL Server. The warm paired learned-minus-fixed-local
estimate was -64.79 ms with a 95% interval of -194.69 to +0.19 ms.
The large point estimate came mainly from the broad 250x three-join
workload. Learned-minus-fixed-server was -426.51 ms (95% interval
-1,043.24 to -4.97 ms). The +16.7 percentage-point warm completion
difference against fixed SQL Server had a paired interval of 0 to
41.7 points. Cold results showed the same learned choices as warm.
The observed learned overhead p50 was 0.106 ms warm and 0.118 ms cold.

## Scope and eligibility

Production Chinook routing exposes equivalent SQLite and SQL Server
implementations for track, customer, and invoice counts, invoice
revenue, and invoice mean. LDBC routing exposes in-memory graph and SQL
Server frontier for person count, friends, and shortest path. M6
reference controls already showed LDBC frontier timeouts on reviewed
plans. This experiment uses the existing relational scale-benchmark
selector and the existing LDBC learned cost model. It adds benchmark
query templates over the existing SQLite and SQL Server adapters.
Other catalog operations gain no physical alternatives.

The versioned manifest splits benchmark families and parameters before
measurement. The relational test uses new 25x and 250x projections
against the archived original calibration at 1x, 10x, 100x, and 1000x.
Windows cover about 5% and 67% of invoice IDs. The relational families
contrast invoice bands with three-join distinct customer reach. Graph
cases use reviewed two-operation DAGs at 128 and 1,528 people, with short,
long, and disconnected paths. Some graph scales and individual
operations were present in the existing training profile, while the
held-out DAG compositions were absent. The graph results therefore
test new compositions, with relational results providing the unseen
scale test. The original learned profiles remain unchanged.

## Method and calibration

Every backend sees the same typed DAG and independent gold. Backend
order is randomized per trial. Each policy selects before measurements.
Policy latency is calculated from its selected backend's matched trial
plus that policy's measured selection time. A warm trial reuses an
initialized executor after one warm-up. A cold trial includes fresh
connections, reader checks and applicable graph construction. It keeps
the existing process, OS and database caches. All graph DAGs in this
experiment had uniform learned backend choices across their nodes.
The harness stops if a modified workload needs mixed-backend timing.

The heuristic chooses BFS for graph work. On SQL it chooses SQL Server
for two or more joins with at least 100,000 estimated input rows touched,
and SQLite otherwise. It uses no timing labels. The learned model
receives only existing operation features. Gold, measured path depth,
latency, and timeout outcomes are excluded from its runtime input.
The oracle minimizes mean penalized cost per workload and mode, using
the measured candidates. Failures count as setup time plus a fixed
2,500 ms penalty. Latency percentiles show actual elapsed time including
timed-out trials. The SQL Server statement timeout is two seconds.
Paired intervals resample workload instances with repeats kept together.
Related scales and windows can still correlate.

The learned relational policy missed three warm oracle choices at 250x
on narrower or simpler workloads. It correctly chose SQL Server for the
broad three-join workload. Cold connection setup shifted those three warm
winners back to SQLite. For warm paired successful SQL trials, the
predicted log1p latency-ratio MAE was 0.77 and the signed bias +0.26.
For graph nodes, the completed-node latency MAE was 4.09 ms. The profile
predicted zero timeout risk on every attempted node with predictions,
while 13.3% timed out. The Brier score was 0.133 on 75 attempts. This
sample shows underprediction of timeout risk. No selector was recalibrated.

The measured SQL selection time includes the diagnostic reconstruction
of its predicted ratio. Thus the observed overhead is for this benchmark
policy, including instrumentation. The identical choices still leave no
execution savings for learned routing over the heuristic on these cases.

## Reproduce

Ensure the earlier 10x Chinook and 128-, 512-, and 1,528-person LDBC
projections are loaded. Use the existing load and read connection
settings. The load command creates the additional isolated scale schemas.
Give the read identity object-level SELECT on the new projection tables
and manifests.

```powershell
python -m benchmarks.m7_physical --load
python -m benchmarks.m7_physical
python -m benchmarks.report_m7_physical
```

The live run used the existing restricted database-only SQL Server lab
reader after Windows login. The direct Windows authentication test
remains skipped. The archived relational calibration stores the exact
original case medians. It reconstructs the same time-difference model,
without using any M7.1 samples. Raw results go to
`logs/m7-physical-results.json` and detailed analysis to
`logs/m7-physical-analysis.json`.

[Manifest](../benchmarks/m7-physical-manifest.json) · [Machine-readable summary](../benchmarks/m7-physical-summary.json)
