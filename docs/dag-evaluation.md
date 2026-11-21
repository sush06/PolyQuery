# DAG planning evaluation

The [120-question test set](../benchmarks/dag-test-v1.json) has 20 cases each
for single-stage requests, multi-stage requests, multi-stage paraphrases,
ambiguity, invalid requests, and adversarial requests. It was authored after
the two planners were implemented. Both planners were frozen for the
comparison. The questions are disjoint from the development set, with some
intent families in common. The test set uses the synthetic fixture and has
not received independent human review.

Gold answers come from the synthetic fixture and the requested operations.
An answerable request retains its expected value even when the current
catalog lacks an operation needed to answer it. In the reporting examples,
unqualified “reports” follows PolyQuery's all-downstream convention. Alice's
downstream reports have a mean salary of 100,000. Her **direct** reports have a
mean salary of 95,000.

```bash
python -m polyquery.cli compare-dag --dataset benchmarks/dag-test-v1.json --repeats 20 --output logs/dag-test-v1-report.json
```

The harness pairs outcomes by question and alternates planner order. It reports
correct answers and correct abstentions, total abstentions, incorrect answers,
and failures separately. It distinguishes typed execution failures, contract
failures, and other exceptions. Latency is summarized across the per-question
medians. Repeats measure latency and stability and do not increase the
statistical sample size. The 95% percentile interval uses 5,000 paired bootstrap
draws over intent families, keeping related questions and their two planner
outcomes together. The report records dataset, implementation, and fixture
hashes.

| Category | Keyword correct | DAG correct |
| --- | ---: | ---: |
| Single-stage | 18/20 | 18/20 |
| Multi-stage | 0/20 | 7/20 |
| Multi-stage paraphrases | 0/20 | 0/20 |
| Ambiguous | 19/20 | 19/20 |
| Invalid | 19/20 | 20/20 |
| Adversarial | 18/20 | 18/20 |
| **Total** | **74/120** | **82/120** |

| Metric | Keyword | DAG |
| --- | ---: | ---: |
| Abstentions | 98 | 92 |
| Incorrect answers | 4 | 3 |
| Execution failures | 0 | 0 |
| Median latency | 0.31 ms | 0.35 ms |
| p95 latency | 0.71 ms | 1.10 ms |

Across the 40 multi-stage and paraphrase questions, the gain was **17.5
percentage points** with a **95% interval of 0.0 to 48.1 points** across nine
families. Every gain came from explicit sequences. Both planners abstained on
the three Alice salary formulations. Both returned zero incorrect answers on
the 20 single-stage questions. That observed count leaves the risk on a
larger set unresolved.

Both planners returned ordinary analytical answers to two adversarial requests
that should have been refused. Both also answered an ambiguous headcount
question. DAG mode correctly abstained on an invalid follow-up that the
keyword planner answered as a standalone count. These results measure refusal
behavior and answer quality on the fixture.

## Label audit and refusal follow-up

`python -m benchmarks.audit_dag_labels` recalculates all 60 answer labels
directly from the fixture using independently implemented reference operations.
All 60 matched. A manual review kept the 60 abstention labels in their three
categories. The versioned test data are unchanged. This audit checks fixture
arithmetic and the stated labeling policy. It has yet to receive independent
human review, particularly for implicit reporting scope.

The shared request guard now blocks the two observed host-file and host-code
requests. Regression tests verify that neither reaches a backend and that
Qwen DAG mode also blocks both before model dispatch. Rerunning the test set
after this change gives 76/120 for keyword and 84/120 for DAG, with 20/20
adversarial refusals for each. Ordinary single-stage results remain 18/20 for
both. These are post-remediation results on a known set.

## Qwen3 4B DAG planning

```bash
python -m polyquery.cli query --planner llm-dag --model qwen3:4b-instruct --llm-api chat --llm-base-url http://127.0.0.1:11434/v1 --llm-timeout 90 "Among Alice's direct reports, what is the mean salary?"
python -m polyquery.cli compare-dag --dataset benchmarks/qwen-dag-smoke.json --model qwen3:4b-instruct --llm-api chat --llm-base-url http://127.0.0.1:11434/v1 --llm-timeout 90 --output logs/qwen-dag-smoke.json
```

Qwen proposes an explicit decision and a graph with named inputs and requested
outputs. Local checks authorize exact operation, scope, and binding semantics
before execution. Single questions and explicit sequences are cross-checked
against the guarded keyword plan. A separate semantic check recognizes bounded
report-salary forms and distinguishes direct reports from all downstream
reports. The statistics node receives the graph's validated employee IDs.
The model gets the question and operation catalog in a single request.

The six-case local integration smoke scored 5/6 with Qwen, 4/6 with the grammar
DAG planner, and 3/6 with keyword. Qwen answered both reporting aggregates,
including a null mean over an empty set, and safely abstained on the standalone
sequence that it failed to preserve. It made four calls and used 4,289 total
tokens. The timed run contained one extreme latency outlier. No monetary cost
was measured. This smoke includes prompts used during integration. A fresh
independently reviewed set is needed to estimate paraphrase coverage and safety
after prompt development.

On a separate live check of the three Alice formulations, Qwen returned
100,000 for “employees reporting to Alice” and 95,000 for “Alice’s direct
reports.” It abstained on “Find Alice’s reports, then calculate their average
salary” because its proposed plan failed the preservation check. The check
therefore exercised both accepted bindings and a remaining planning failure.

For a full three-way run, pass `--model` and the compatible endpoint options to
`compare-dag` with the chosen test dataset. The report includes separate model
planning failures, token counts, latency, and paired comparisons against keyword.
[Local model setup](llm-planner.md) · [Earlier Qwen3 single-plan experiment](../benchmarks/experiments/qwen3-4b/results-summary.md)
