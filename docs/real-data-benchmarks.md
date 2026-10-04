# Real-data development benchmarks

All three downloaded datasets are selectable sources. Every supplied row is used;
there is no automatic sampling. The questions use a small explicit grammar so
policies interpret each question the same way.

| Source | Observed local size | Operations | Policies |
| --- | --- | --- | --- |
| Chinook | 3,503 tracks, 412 invoices, 59 customers | Counts, revenue, mean/median, country groups, genre joins | Router, fixed SQLite, fixed Python |
| LDBC person/knows | 1,528 people, 14,073 undirected edges | Count, friends, shortest paths | Router, fixed SQLite, fixed graph BFS |
| January 2026 Yellow Taxi file | 3,724,889 rows | Counts, fare/total sums, means, median fare, negative-fare count | Router, fixed SQLite, fixed Python/Arrow |

The LDBC files suffice for this workload. Posts, messages, comments, and other
SNB tables would support richer workloads later; no extra download is needed now.
The loaded subset is not the full SNB benchmark implementation.

## Commands

From the project root, install the optional Taxi dependency if needed:

```powershell
python -m pip install -e ".[taxi]"
```

Synthetic, LDBC, and Chinook use the standard library. Taxi uses
[PyArrow's Parquet reader](https://arrow.apache.org/docs/python/parquet.html).
The development gold-building script also uses NumPy; install it separately if
needed with `python -m pip install numpy`.

```powershell
python -m polyquery.cli query "Median invoice total" --source chinook --data benchmarks/Chinook_Sqlite.sqlite
python -m polyquery.cli query "Average fare amount" --source taxi --data benchmarks/yellow_tripdata_2026-01.parquet
python -m polyquery.cli inspect-source --source chinook --data benchmarks/Chinook_Sqlite.sqlite

python -m polyquery.cli compare --source chinook --data benchmarks/Chinook_Sqlite.sqlite --dataset benchmarks/chinook-benchmark-dev.json --repeats 3 --warmup 1 --output benchmarks/comparison-chinook.json
python -m polyquery.cli compare --source ldbc --people benchmarks/people_0_0.csv --knows benchmarks/person_knows_person_0_0.csv --dataset benchmarks/ldbc-benchmark-dev.json --repeats 3 --warmup 1 --output benchmarks/comparison-ldbc.json
python -m polyquery.cli compare --source taxi --data benchmarks/yellow_tripdata_2026-01.parquet --dataset benchmarks/taxi-benchmark-dev.json --repeats 3 --warmup 1 --output benchmarks/comparison-taxi.json
```

Add `--reverse-order` to reverse policy order. Use `evaluate --policy fixed-sql`
for a single policy report. `query` accepts `--policy` too. Supported questions
are listed in the development case files and in `CHINOOK_QUESTIONS` and
`TAXI_QUESTIONS` in `polyquery/tabular.py`. Unknown modifiers and fields abstain.

## Semantics

Chinook opens the downloaded database read-only. SQL uses fixed SELECT templates
and denies writes through an authorizer. Python materializes four supported tables
and applies fixed count, statistics, grouping, and join templates. Genre counts
include tracks joined to a known genre; invoice aggregates ignore null totals.

Taxi reads three numeric columns from all Parquet rows into Arrow and loads the
same values into in-memory SQLite. Nonfinite values become null in both
projections. SQLite has a covering fare index so median does not repeatedly sort
the full file; index construction is included in initialization.
Numeric metrics ignore nulls; trip count includes every row.
Negative and zero values remain included. No date filter is inferred from the
filename: any out-of-month records remain included. Median averages the two
central valid values for even-sized input. Python interprets fixed AST expressions
through an allowlist and Arrow kernels; it does not run arbitrary programs.

LDBC SQL stores the same undirected, deduplicated projection as graph BFS.
Frontier joins and visited tables run in SQLite; Python controls the iteration
and reconstructs paths. This is a SQL execution strategy, not a single recursive
SQL statement or a Neo4j adapter. Shortest-path ties may differ between strategies.
The scorer checks endpoints, actual graph edges, and the reference path length.
Disconnected gold paths use null.

Input files are never modified. Downloaded datasets remain ignored by Git.
Initialization loads both available strategies for every policy to give the
harness a common setup. Thus its initialization metrics do not describe minimal
startup for a standalone backend. Taxi requires memory for both Arrow and SQLite;
this materialization is not an out-of-core solution. Load time is separate from
query latency.

## Gold and research interpretation

There are 10 Chinook, 11 LDBC, and 9 Taxi authored development cases. The script
below generates reference answers directly from raw files without calling
PolyQuery planners or adapters:

```powershell
python benchmarks/build_gold.py --source all
```

It overwrites the development gold files. Chinook references use raw SQLite
queries and sorted middle invoice values. Taxi references use NumPy reductions.
LDBC references use a separate CSV traversal implementation. These are
computational cross-checks, not independent human annotation or held-out labels.
Each suite records source hashes; evaluation rejects mismatched data. Regenerate
only for intentional development data changes, then review the answers.

All answerable cases accept both available execution strategies. Fixed policies
share the router's exact grammar, so comparisons isolate execution selection on
recognized questions. They do not measure general language understanding. The
tabular router chooses Python for medians and SQL otherwise; LDBC chooses graph
BFS. Neither policy learns from measured costs.

Reports contain accuracy, errors, abstention, total/stage latency, initialization,
source hashes, and family summaries. Monetary cost remains unmeasured. Repeat
runs with varied policy order and representative question families before drawing
performance conclusions. Broader independently reviewed gold, frozen held-out
splits, and measured cost-aware routing are the next research steps.
