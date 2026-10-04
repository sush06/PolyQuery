# Evaluation

Run these commands from the project root:

```powershell
python -m polyquery.cli evaluate --dataset benchmarks/demo.json --repeats 5 --warmup 1 --output benchmarks/evaluation-synthetic.json
python -m polyquery.cli evaluate --source ldbc --people benchmarks/people_0_0.csv --knows benchmarks/person_knows_person_0_0.csv --dataset benchmarks/ldbc-dev.json --repeats 5 --warmup 1 --output benchmarks/evaluation-ldbc.json
```

The demo has 31 synthetic development cases. The LDBC file has six smoke cases
for the local CSVs. Both were written during development. Passing them checks
functionality; it does not measure held-out generalization or establish a benefit
from routing. Repetitions measure runtime variation and repeated behavior, not
independent evidence from additional questions.

## Metrics

| Field | Definition |
| --- | --- |
| `answer_accuracy` | Correct answer or correct requested abstention, divided by all measured runs |
| `answerable_accuracy` | Correct answers divided by runs whose gold route is not abstention |
| `route_accuracy` | Selected route appears in the acceptable route list |
| `joint_accuracy` | Both route and answer are correct |
| `coverage` | Successful executions divided by all measured runs; wrong answers still count as executions |
| `answered_accuracy` | Correct answers among successful executions |
| `error_rate` | Exceptions divided by all measured runs |
| `abstention_precision` | Intended abstentions among actual abstentions |
| `abstention_recall` | Actual abstentions among intended abstentions |

Undefined ratios are `null`. Per-family and expected-backend summaries use the
same definitions. Cases with multiple acceptable routes appear in each matching
backend summary.
Each result retains the actual answer, route, status, and exception details.
Errors count as incorrect answers and routes. An unintended abstention cannot
match an expected `null`; a successful execution returning `null` can.

Latency summaries report mean, p50, p95, and sample count in milliseconds.
Percentiles use linear interpolation. Planning measures the planner call;
execution includes backend execution and response construction. Total query time
includes both. Source initialization is measured separately. Warmup runs are
excluded; questions run in dataset order. Errors have total latency but no stage
times. Tiny fixtures produce very short timings, so use representative workloads
and multiple process runs before drawing performance conclusions.

Cost fields are `null`: there are no instrumented model calls or monetary costs.
Do not interpret them as zero. The SQL-only route-label score is a diagnostic,
not a comparison with a capable SQL query system.

## Dataset format

Existing case lists still work. New datasets can include metadata:

```json
{
  "metadata": {
    "source": "synthetic",
    "split": "development",
    "label": "Example development cases"
  },
  "cases": [
    {
      "id": "employee-count",
      "family": "aggregation",
      "question": "How many employees?",
      "backends": ["sql", "code"],
      "expected": 6
    }
  ]
}
```

Use `backend` for one acceptable route or `backends` for several. Route labels
describe acceptable strategies; they do not add capabilities to the adapters.
IDs must be unique. Abstention cases use `backend: "abstain"` and `expected: null`.

Numbers use relative and absolute tolerance of 1e-8, including inside lists and
objects. Booleans are distinct from numbers. Lists compare in order unless
`comparison: "unordered"` is supplied; unordered matching preserves duplicates.
Use `expected_alternatives` for several valid answers, for example two equally
short graph paths. Include every accepted answer in that list. A general path
validity or constraint scorer is not implemented yet.

Reports include a canonical JSON dataset hash, hashes of source data, source
metadata, Python version, platform, UTC timestamp, and run settings. Paths do not
contribute to source hashes. Record the code commit separately when archiving a
research run. Changing dataset metadata changes its canonical dataset hash.

## Policy comparisons

`evaluate_policies` accepts named factories for Engine-compatible context managers:

```python
from polyquery.evaluation import evaluate_policies

# Each factory supplies a planner, adapters, and the same source data.
report = evaluate_policies(
    {"router": router_factory, "fixed-sql": fixed_sql_factory},
    dataset,
    repeats=5,
    warmup=1,
)
```

This evaluates actual answers and checks source hashes across policies. Policies
run sequentially in insertion order; repeat with reversed order to check cache
and order effects. The current project has no capable fixed-SQL implementation
for every graph and statistics operation. Build those equivalent adapters before
making a routing comparison; forcing unsupported operations into the existing
SQL adapter would produce a misleading baseline.

For research evaluation, freeze question families before router tuning, keep
paraphrases within a split, and have gold answers independently reviewed. Report
uncertainty across unique cases rather than treating timing repetitions as
independent accuracy samples. Confidence intervals, timeout isolation, cost
instrumentation, and automated constraint scoring remain future additions.
