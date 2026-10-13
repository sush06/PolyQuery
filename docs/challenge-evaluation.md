# Challenge evaluation and measured-cost routing

The earlier 100% scores measured exact supported strings and execution templates.
They were legitimate regression results but weak evidence for natural-language
query routing. Challenge v1 adds 63 test questions, including natural paraphrases,
unseen operation families, conditional queries, multi-table joins, ranking,
percentiles, mutual friends, ambiguous requests, and writes that require abstention.

## Partitions and labels

| Source | Train | Validation | Test |
| --- | ---: | ---: | ---: |
| Chinook | 3 | 2 | 20 |
| Taxi | 3 | 2 | 15 |
| LDBC | 2 | 1 | 28 |

Every variant of an operation family stays in one partition. For example, Taxi
sum-total and median-fare families are absent from cost training; all LDBC path
questions are in test. The datasets are the same supplied files across partitions:
this tests unfamiliar operation families and language, not unseen schemas/domains.
The training partition is intentionally small. Dataset hashes anchor exact data
sizes; this policy does not yet extrapolate to a different file or graph scale.

`benchmarks/challenge-v1/manifest.json` freezes each partition's bytes and family
list. The CLI checks hashes, source labels, case counts, and family disjointness.
`profile-manifest.json` separately freezes cost profiles before validation/test
runs. The builder refuses to overwrite the split directory; calibration refuses
to write into a frozen profile directory.

Gold answers were computed from raw data, not produced by the router. They have
not been independently reviewed by a human. JSON metadata records this explicitly.
Treat these as an authored challenge, not publication-ready held-out evidence.
We inspected v1 results, so future parser tuning must use development data and a
new untouched test version. Do not tune against v1 failures and then claim v1 is
still unseen.

## Categories

- `canonical`: recognizable question about an operation family excluded from calibration.
- `paraphrase`: same semantic operation and gold answer phrased naturally; grouped with its canonical case in test.
- `capability_gap`: answerable from the downloaded data, but requires operations/fields the current adapters do not implement.
- `required_abstention`: ambiguity or a write request for which abstention is the intended behavior.

Report categories separately. A capability-gap failure is an adapter limitation,
not necessarily a routing error. A paraphrase failure exposes parser coverage.
Correct abstentions contribute to overall accuracy, so also report answerable
accuracy and coverage. Correct-answer latency and total query time per correct
answer accompany overall timing; a policy that rapidly refuses most questions
must not be called an efficient answering system.

## Cost policy

`cost-aware` uses the existing parser unchanged. Calibration executes only training
reference plans on each capable backend, checks the answers, excludes incorrect
strategies, and records execution-time medians. No test answers or timings update
this profile. The selected backend minimizes estimated execution cost among
capable strategies. Known operations use lookup costs; unseen operations use a
backend-wide training median, a deliberately simple fallback that can be wrong.
When no measured capable strategy exists, the original route is retained.

This improves execution selection only. It cannot interpret an unsupported
paraphrase or add a missing adapter operation. It is not an LLM, a trained semantic
parser, or a general cost model. Full-file startup loads both strategies and is
reported separately, as in the earlier benchmarks.

## Commands

Use the paths for your local files. Calibration below describes how the saved
profiles were created, before the directory was frozen:

```powershell
python -m polyquery.cli calibrate --source taxi --data benchmarks/yellow_tripdata_2026-01.parquet --dataset benchmarks/challenge-v1/taxi-train.json --repeats 5 --warmup 1 --output benchmarks/new-experiment/taxi-cost-profile.json
python -m polyquery.cli compare --source taxi --data benchmarks/yellow_tripdata_2026-01.parquet --dataset benchmarks/challenge-v1/taxi-test.json --cost-profile benchmarks/challenge-v1/taxi-cost-profile.json --repeats 3 --warmup 1 --oracle-repeats 3 --output benchmarks/challenge-v1/taxi-test-report.json
python -m polyquery.cli compare --source chinook --data benchmarks/Chinook_Sqlite.sqlite --dataset benchmarks/challenge-v1/chinook-test.json --cost-profile benchmarks/challenge-v1/chinook-cost-profile.json --repeats 3 --warmup 1 --oracle-repeats 3 --output benchmarks/challenge-v1/chinook-test-report.json
python -m polyquery.cli compare --source ldbc --people benchmarks/people_0_0.csv --knows benchmarks/person_knows_person_0_0.csv --dataset benchmarks/challenge-v1/ldbc-test.json --cost-profile benchmarks/challenge-v1/ldbc-cost-profile.json --repeats 3 --warmup 1 --oracle-repeats 3 --output benchmarks/challenge-v1/ldbc-test-report.json
```

Profiles are machine-specific. To recalibrate, use a new output directory and
freeze the new profiles before evaluating a new untouched test version. Add
`--reverse-order` and run in a fresh process to assess policy-order effects.

## Regret and remaining uncertainty

With `--oracle-repeats`, reference-plan probes run after measured query trials.
Each correct backend is timed after one probe warmup. The fastest correct median
is an execution oracle for that reference plan. It does not interpret the question
and is not a language-understanding baseline. Identical reference plans share
probe measurements within a policy run. Oracle measurements never feed the policy.

Regret is measured execution-stage time minus the fastest correct reference
strategy median, clipped at zero; the signed difference remains in each result.
Different timing trials introduce noise, especially on Chinook. This is execution
regret, excluding planner and startup costs; end-to-end timings are reported too.
Incorrect answers, unintended abstentions, and tasks without executable reference
plans have null regret. They are excluded from the regret denominator, not counted
as zero-cost successes. Always show coverage alongside regret.

Results on this small authored suite are diagnostic. Independent gold review,
additional question families, new schemas, graph scale variation, multiple fresh
process runs, confidence intervals grouped by family, and model-cost instrumentation
remain necessary before research conclusions. The next semantic-parser or adapter
change should improve development tasks and use a new test version for confirmation.
