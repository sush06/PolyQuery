# LDBC SQL graph benchmark

The SQL baselines run fixed, parameterized queries against a Person and
Knows projection. The existing planner, graph backend, and routing policy
remain unchanged. Run this comparison separately from the normal query CLI.

## Setup and reproduce

Use the optional SQL Server dependency and a dedicated benchmark database.
Set `POLYQUERY_SQLSERVER_LOAD_CONNECTION_STRING` for the setup principal
with permission to create schemas and their tables. Set
`POLYQUERY_SQLSERVER_CONNECTION_STRING` for the authenticated read-only
principal. Grant the reader object-level SELECT on `person`, `knows`, and
`manifest` in each generated `pq_ldbc_<size>` schema. The reader passes the
same effective server, database, schema, and object permission checks used
for the Chinook integration. See [SQL Server authentication](sqlserver.md).

```powershell
pip install -e ".[sqlserver]"
python -m benchmarks.ldbc_sql_graph load --sizes 128 512 1528
python -m benchmarks.ldbc_sql_graph compare --server --sizes 128 512 1528 --warmups 2 --repeats 7 --timeout 2
python -m benchmarks.report_ldbc_sql_graph logs/ldbc-sql-graph.json logs/ldbc-sql-graph.md logs/ldbc-sql-graph.png
```

Install Matplotlib to generate the plot. Omit `--server` to run the BFS and
SQLite control without a SQL Server connection. The local live run used
the existing database-only lab reader via explicit Windows-authenticated
impersonation with no revert. Normal CLI runs require the configured
reader. The direct Windows authentication regression remains skipped
when that reader is unconfigured.

The loader takes numeric-ID induced subgraphs of the checked-in CSVs.
It retains each original string ID, materializes both orientations of
distinct friendships, preserves self-loops, and uses a composite
(source, target) primary key and endpoint foreign keys. Source lookup uses
the person-ID unique index. Load commits each graph size separately.
An exact rerun verifies all rows and the fingerprint. A mismatch fails and
leaves existing data in place. The loader prints creation or verification
time. The recorded benchmark setup timings use already loaded SQL tables.

## Semantics and bounds

The indexed direct-neighbor join returns unique neighbors ordered
numerically. The source appears as its own friend only when the data
contains a self-loop. Shortest path returns ordered original IDs,
including both endpoints, with a one-element path for identical endpoints.
It returns null when disconnectedness has been established. The catalog
has no independent exact-hop or bounded-reachability operation, so the
comparison covers the existing count, friends, and shortest-path
contracts.

The recursive CTE enumerates simple paths using delimiter-protected rank
tokens and increasing parameterized hop ceilings. Numeric rank tie
ordering reproduces BFS for the supplied IDs. Inputs with distinct string
IDs that tie numerically are rejected by the SQL adapter because BFS has
no deterministic tie order for them. A fixed recursion guard and a
12-hop ceiling cap the search. The per-case time budget uses the SQL
Server driver's whole-second statement timeout and SQLite's progress
handler. If the CTE cannot decide reachability within its bounds, it
reports a coverage limit. Dense neighborhoods can cause large
intermediate path sets even when only one result is requested.

## Measurements

The report embeds the case manifest, source hashes, parameters, degrees,
hop distances, status, independent gold, and raw timings. The reference
reads the original CSVs separately and computes distances from the
destination. The full-size run also cross-checks the existing LDBC
development labels. Warm trials randomize backend order each round.
Direct adapter timings exclude DAG scheduling and include each adapter's
local contract checks. DAG timings include typed dispatch and result
validation. BFS loading, SQLite setup, and SQL Server connection and
projection validation are recorded separately. The SQL page figures
measure storage footprint. Native SQL query working memory was unavailable
to the reader, so it is not compared with Python allocation figures.

This local run found no warm DAG latency crossover. It evaluates
recursive-CTE SQL and preloaded BFS on these projections. The results
do not measure routing, concurrent workloads, or indexed SQL strategies
that maintain a shared visited set.

## SQL Server frontier baseline

Add `--frontier` to the SQL Server comparison to run a second SQL
implementation on the same graph snapshots and cases. It expands one
distance layer at a time and visits each person at most once. The SQL
batch uses request-local table variables for the visited set and the
current and next frontiers. The checked projection principal has
SELECT-only access to persistent Person and Knows data. The adapter
does not require schema changes, persistent writes, or a privileged
connection.

For a target with multiple shortest paths, the batch keeps the smallest
fixed-width numeric rank sequence, matching the existing BFS tie order.
The loop terminates when it finds the target or exhausts the reachable
component. It has a driver timeout and no recursive CTE hop ceiling.
Count and friends continue to use the original SQL statements.

With the same two-second timeout, the frontier recovered five shortest-path
cases that the CTE did not complete. It finished 32 of 38 paths, compared with
27 for the CTE and 38 for BFS. All six frontier timeouts were on the full
1,528-person projection. The frontier beat the CTE in 7 of 27 matched
shortest-path cases by DAG median latency. BFS beat the frontier in every
matched case. These are local warm-connection results.

```powershell
python -m benchmarks.ldbc_sql_graph compare --server --frontier --sizes 128 512 1528 --warmups 2 --repeats 7 --timeout 2 --output logs/ldbc-sql-frontier.json
python -m benchmarks.report_ldbc_sql_graph logs/ldbc-sql-frontier.json logs/ldbc-sql-frontier.md logs/ldbc-sql-frontier.png
```
