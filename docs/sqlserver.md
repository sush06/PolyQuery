# Optional SQL Server backend for Chinook

Chinook can route five existing SQL operations to SQL Server: track count,
customer count, invoice count, total invoice revenue, and average invoice
total. The other Chinook SQL operations continue to use the local SQLite
file. The code backend and the planner's existing `sql` and `code` contracts
are unchanged. Select SQL Server explicitly with `--sql-backend sqlserver`
or `Engine(source="chinook", data_path=path, sql_backend="sqlserver")`.

## Setup

Install the Microsoft ODBC Driver for SQL Server and the Python extra.

```powershell
python -m pip install -e ".[sqlserver]"
```

Create a dedicated user database for the Chinook projection. Use a separate
loader identity with schema creation rights and a restricted application
identity. Pass connection strings through the environment and keep real
credentials out of source control, command arguments, and logs.

On a local Windows instance such as `SUSHR`, the shared-memory protocol
can be selected explicitly. The following loader example uses Windows
authentication. `TrustServerCertificate=yes` is for the local example.
For a remote connection, use a trusted certificate with certificate
validation enabled.

```powershell
$env:POLYQUERY_SQLSERVER_LOAD_CONNECTION_STRING = 'DRIVER={ODBC Driver 17 for SQL Server};SERVER=lpc:SUSHR;DATABASE=PolyQueryChinook;Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=yes;'
python -m benchmarks.load_sqlserver_chinook --data benchmarks/Chinook_Sqlite.sqlite
Remove-Item Env:POLYQUERY_SQLSERVER_LOAD_CONNECTION_STRING
```

The loader creates a fresh `polyquery` schema in one transaction, copies
the Track ID, Customer ID, and Invoice Total projections using bound values,
and writes the source SQLite SHA-256. It refuses an existing schema. Use a
standalone SQLite snapshot and keep both copies fixed during evaluation.

Provision an ordinary Windows account or service identity dedicated to
PolyQuery. It needs access to Python, the repository, and the SQLite
fixture, plus permission to run the application. Keep it out of Windows
Administrators and SQL Server administrative roles. Replace the example
account below with that existing Windows principal. A SQL Server
administrator runs this setup after loading the fixture.

```sql
USE [master];
GO
CREATE LOGIN [SUSHR\PolyQueryReader] FROM WINDOWS WITH DEFAULT_DATABASE = [PolyQueryChinook];
GO
USE [PolyQueryChinook];
GO
CREATE USER [polyquery_reader] FOR LOGIN [SUSHR\PolyQueryReader];
GRANT SELECT ON OBJECT::[polyquery].[Track] TO [polyquery_reader];
GRANT SELECT ON OBJECT::[polyquery].[Customer] TO [polyquery_reader];
GRANT SELECT ON OBJECT::[polyquery].[Invoice] TO [polyquery_reader];
GRANT SELECT ON OBJECT::[polyquery].[DatasetManifest] TO [polyquery_reader];
```

Set `POLYQUERY_SQLSERVER_CONNECTION_STRING` for that reader identity.
For example, in a PowerShell session running as the mapped Windows reader:

```powershell
$env:POLYQUERY_SQLSERVER_CONNECTION_STRING = 'DRIVER={ODBC Driver 17 for SQL Server};SERVER=lpc:SUSHR;DATABASE=PolyQueryChinook;Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=yes;'
```

The adapter checks effective server, database, projection schema, and table
permissions at startup and rejects broad grants and administrator identities.
SQL Server grants enforce read-only access. The adapter only executes fixed
one-row SELECT templates, validates the fixture hash using a bound parameter,
and bounds result reads to two rows to detect invalid responses. The hash
records the loaded snapshot. Administrators must preserve it and the
projection data after loading.
[Microsoft SQL Server effective permissions](https://learn.microsoft.com/en-us/sql/relational-databases/system-functions/sys-fn-my-permissions-transact-sql)

## Query and compare

```powershell
python -m polyquery.cli query --source chinook --data benchmarks/Chinook_Sqlite.sqlite --sql-backend sqlserver "How many tracks?"
python -m benchmarks.compare_chinook_sqlserver --data benchmarks/Chinook_Sqlite.sqlite --repeats 30 --warmup 5 --output logs/chinook-sqlserver.json --markdown logs/chinook-sqlserver.md
python -m pytest -q tests/test_sqlserver.py
```

The benchmark uses independent Python counts and numeric reductions from
the SQLite source records as gold. Each engine executes the same five typed
SQL operations. `--sqlite-only` runs the offline control. The live pytest
test requires the reader environment variable and a matching loaded fixture.
Override the SQLite test path with `POLYQUERY_SQLSERVER_CHINOOK_PATH` if
needed. Existing benchmark manifests are unchanged.

Pytest selects the live test automatically when
`POLYQUERY_SQLSERVER_CONNECTION_STRING` is set. Run it in a session under the
mapped Windows identity. A database user created `WITHOUT LOGIN` can verify
database permissions through controlled impersonation, but it cannot
authenticate an ODBC connection.

### Benchmark interpretation

Each backend reuses one engine connection for all five operations with empty
parameters. Each operation receives five warm-ups and 30 measured runs by
default. Measured query latency includes one-node typed DAG dispatch, query
and fetch, and result validation. Initialization and separate connection
probes are reported independently. The JSON records row counts, indexes,
SQLite and SQL Server configuration, driver and pooling information,
environment, and all query samples. Pooling settings and local transport can
affect the connection timings.

### Cost-aware routing

The catalog declares equivalent `sql` and `code` implementations for these
operations. SQLite and SQL Server both occupy the same `sql` slot.
`CostPlanner` chooses among catalog backend IDs, so it cannot choose between
these two engines within a single run. In addition, the five operations
have no parameters and a fixed fixture. Rephrasings of a query would exercise
the same timing key and workload. The comparison therefore measures the
two fixed engines. It does not fit a routing model or estimate routing regret.
