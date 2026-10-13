# Challenge v1 results

63 unique test questions on the same three full datasets. Three measured repetitions per policy after one warmup pass; four policies per source. The 756 measured runs are repeated trials of 63 questions, not 756 independently sampled questions.

Training used 8 separate canonical cases. Validation used 5 separate cases. Operation families and paraphrases do not cross partitions. Cost profiles were frozen before validation/test. No parser rules were expanded using the challenge results.

| Source | Overall accuracy | Answerable accuracy | Coverage |
| --- | ---: | ---: | ---: |
| chinook | 35.0% | 18.8% | 15.0% |
| taxi | 40.0% | 18.2% | 13.3% |
| ldbc | 35.7% | 30.8% | 28.6% |

All four policies had identical accuracy and coverage within each source: they share the same narrow parser. Canonical questions passed; paraphrases and capability-gap questions scored 0%; required abstentions passed. Thus the score drop is explained by specific semantic and adapter coverage limitations, rather than execution corruption.

## Correct-answer execution performance

| Source | Policy | Correct-answer latency p50 ms | Mean execution regret ms | Eligible regret runs |
| --- | --- | ---: | ---: | ---: |
| chinook | router | 0.742 | 0.295 | 9 |
| chinook | cost-aware | 0.310 | 0.055 | 9 |
| chinook | fixed-sql | 0.707 | 0.266 | 9 |
| chinook | fixed-code | 0.276 | 0.011 | 9 |
| taxi | router | 361.104 | 366.294 | 6 |
| taxi | cost-aware | 44.441 | 1.128 | 6 |
| taxi | fixed-sql | 1072.247 | 1032.365 | 6 |
| taxi | fixed-code | 46.700 | 0.181 | 6 |
| ldbc | router | 1.404 | 0.197 | 24 |
| ldbc | cost-aware | 1.430 | 0.294 | 24 |
| ldbc | fixed-sql | 86.035 | 63.912 | 24 |
| ldbc | fixed-graph | 1.703 | 0.141 | 24 |

Regret is execution-stage time above the fastest correct post-run reference strategy median. It excludes unanswered and capability-gap cases. Signed timing deltas remain in each JSON result; negative noise is clipped in the regret summary. These oracle probes do not update the frozen policy. Tiny Chinook timings and sequential policy order make small differences unreliable.

Cost-aware routing selects Python for the supported tabular test operations and graph BFS for the supported LDBC paths. It can reduce execution cost versus selecting SQLite, but it has no accuracy advantage and does not establish superiority over the best fixed backend. The larger problem is understanding paraphrases and implementing requested operations.

## Review and reuse

Gold is computational, not independently reviewed. This is an authored, family-disjoint diagnostic challenge, not publication-ready held-out evidence. Hash checks verify exact split bytes, data, and profiles; reports also record the implementation fingerprint. Validation reports are saved separately.

Review the expected answers and categories in [Chinook test](chinook-test.json), [Taxi test](taxi-test.json), and [LDBC test](ldbc-test.json). Full policy answers and timings are in [Chinook report](chinook-test-report.json), [Taxi report](taxi-test-report.json), and [LDBC report](ldbc-test-report.json). See [methodology and commands](../../docs/challenge-evaluation.md).

After inspecting v1, do not tune the parser on its failures and call v1 untouched. Improve development tasks, independently review gold, and freeze a new test version for confirmation.
