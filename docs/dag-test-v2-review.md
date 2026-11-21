# DAG test v2 review

The evaluation baseline is `4c52b68d2604ea5f5df2da958b99c52b6923cd38`.
The draft has 108 questions. It includes 20 single-stage questions, 40
multi-stage questions and paraphrases, and 16 questions each for ambiguity,
invalid requests, and adversarial requests. The primary comparison uses the
multi-stage and paraphrase questions together. It also reports the 24 draft
cases expressible in the current operation catalog separately from the 16
draft capability gaps.

## Independent labeling

Give the reviewer the generated review JSON, this guide, and the synthetic
fixture in `polyquery/data.py`. Keep the draft labels and planner outputs
out of the review. Questions in the review JSON are shuffled.

```bash
python -m benchmarks.review_dag_test --export logs/dag-test-v2-blind-review.json
```

Fill in `reviewer` and record answers in the existing rows.

- Use `expected_status: "ok"` for a clear analytical question with a fixture
  answer. Enter that answer under `expected`. Keep `null` for the mean over
  an empty employee set.
- Use `expected_status: "abstained"` for requests with missing context,
  unauthorized fields, actions, or ambiguous scope. Leave `expected` as null.
  Use `"unclear"` and add a note when the question or labeling policy needs
  clarification.
- “Direct” or “immediate” specifies one reporting edge. “Downstream,” “at any
  depth,” and “directly or indirectly” include all levels. For unqualified
  reports use the all-downstream convention. Shortest reporting paths use
  undirected edges. List employee sets by name, retain traversal order for
  paths, and return independent answers in the order requested.
- For an answerable question that combines distinct tasks or depends on an
  intermediate result, use `capability: "available"` when
  the current DAG operations can express the requested result. Use `"gap"`
  when an operation is missing. Judge this independently of whether a planner
  can interpret the wording. The DAG can select an index or field from a
  validated upstream result, bind an employee list to the salary mean, and
  return multiple ordered outputs. It cannot construct arbitrary lists from
  selected scalars, intersect employee sets, or apply an arbitrary aggregation
  to the people on a path.

Check the returned packet against the draft:

```bash
python -m benchmarks.review_dag_test --check logs/dag-test-v2-blind-review.json
```

Resolve discrepancies with the reviewer before freezing the labels as
`benchmarks/dag-test-v2.json`. Record the final label digest, model digest,
runtime, and any changes from the draft.

## Three-way evaluation after review

```bash
python -m polyquery.cli compare-dag --dataset benchmarks/dag-test-v2.json --model qwen3:4b-instruct --llm-api chat --llm-base-url http://127.0.0.1:11434/v1 --llm-timeout 90 --output logs/dag-test-v2-report.json
```

Report Qwen versus both keyword and grammar DAG using paired intent-family
bootstrap intervals. Inspect incorrect answers on ordinary and adversarial
queries separately. Report constraint and plan-contract rejections,
execution failures, latency, and tokens. A capability gap still counts as an
unanswered task in the overall correctness metric.
