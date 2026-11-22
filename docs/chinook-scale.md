# Chinook SQL engine scale benchmark

The benchmark uses a separate relational projection of the original Chinook
rows. At 1×, Track, Customer, Invoice, InvoiceLine and Genre retain their
original rows and identities. Country is a shared dimension derived from
Customer. Unused Chinook columns and relationships stay outside the
projection. Larger copies offset each primary key and its foreign keys by
the maximum source ID of the referenced table. Money is represented in
integer cents. The loader checks row counts, every projected foreign key,
invoice totals, line totals, and the source-file fingerprint.

## Reproduce

```powershell
python -m benchmarks.chinook_scale build-sqlite --scales 1 10 100 1000
python -m benchmarks.chinook_scale build-sqlserver --scales 1 10 100 1000
python -m benchmarks.chinook_scale compare --scales 1 10 100 1000 --warmup 3 --repeats 12 --output logs/chinook-scale-results.json
python -m benchmarks.plot_chinook_scale logs/chinook-scale-results.json logs/chinook-scale-latency.png
```

The SQL Server build creates separate `pq_scale_1`, `pq_scale_10`,
`pq_scale_100`, and `pq_scale_1000` schemas. Use the loader connection and
an isolated benchmark database. Existing scale schemas cause the loader
to stop. The reader needs the existing base projection permissions and
object-level SELECT on the six scale tables and the manifest in each
schema. The benchmark checks effective privileges. See the [SQL Server
setup](sqlserver.md). Use `--sqlite-only` for the offline control and
install Matplotlib locally to render the plot.

## Workloads and measurement

All four workloads use bound invoice-ID cutoffs. The narrow range covers
approximately 1% of invoice IDs and the broad range covers 90%. The report
records actual qualifying row counts. Workloads include filtered count and
sum, revenue grouped by country, a five-table line-sales join grouped by
country and original genre, and high-cardinality per-customer totals.
The join maps replicated genre IDs back to the original category. The
high-cardinality workload retains replicated customer IDs.

An independent Python reference reduces the original source rows and
combines complete and partial copies. Both engines must agree with that
reference before timing. Primary key and secondary index key columns are
compared. SQLite rowids and SQL Server clustered keys still have different
physical layouts. The report records SQLite file bytes, SQL Server
allocated bytes, logical and physical index metadata, client environment,
and SQLite EXPLAIN QUERY PLAN output. SQL Server SHOWPLAN was outside the
reader's privileges.

Measurements reuse one connection per backend and scale. The connection
timing is separate. SQLite setup measures a read-only open. SQL Server setup
includes establishing the session and checking permissions. Query-only
times include fixed SQL execution, fetch, and result materialization.
DAG times additionally include typed validation and one-node DAG dispatch.
Each paired repeat randomizes engine order with a fixed seed. The report
includes p50, p95, individual samples, and exploratory paired bootstrap
intervals for median SQL Server minus SQLite latency. An interval touching
zero leaves the direction unresolved. Repeated warm local measurements
are sensitive to caching, workload order, and system load.

The local comparison uses a Windows-authenticated connection on SUSHR
followed by the existing database-only SELECT user with no revert. The
benchmark reader checks execute after impersonation. This is a scoped lab
measurement. The direct Windows reader login pytest still requires a
dedicated authenticated identity.
