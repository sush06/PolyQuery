# Sequential semantic planning smoke test

This development experiment compares the same local Qwen3 4B checkpoint across four arms.

| Arm | Proposal path |
| --- | --- |
| A | One call with the existing typed DAG schema |
| B | One call with the existing semantic IR schema |
| C | Decomposition, then combined grounding and dependency reasoning |
| D | Decomposition, grounding, and dependency reasoning in separate calls |

C and D validate each intermediate response before advancing. Their final semantic relationships identify the producer and consumer by task position, the consumer's semantic role, and the requested result ordinal. The existing semantic IR compiler resolves the typed field only when a single catalog-approved interpretation exists. The existing typed validator and semantic verifier determine whether execution can proceed. Unsafe requests are refused before calling the model. Each stage gets at most one call, with no repair.

The versioned development manifest contains six LDBC questions and four controls. The answerable rows use fresh graph instances, two-hop paths, and multiple result ordinals within wording families supported by the frozen verifier. Independent reference traversals produce the gold results, and the reference DAGs are separately checked against the engine before inference. This is a verifier-compatible development smoke, with no M6 final questions or prompt examples. The sample does not measure generalization to new wording families.

Run `python -m benchmarks.sequential_planning run` from the repository root with the local `qwen3:4b-instruct` model available. Warm state can differ across requests. Arm order rotates by case, model requests are serialized, and the timeout is 120 seconds per call. The sequential arms can therefore take longer than 120 seconds end to end. The run records model digest and quantization, schema and prompt provenance, dataset and implementation hashes, a resumable per-arm journal, and the aggregate JSON report under `logs/`.

Stage checks compare operation identifiers, literal parameter values, and semantic relationships with the reviewed reference plan. Decomposition task count and literal mentions are transparent proxies. They cannot by themselves establish that a free-text subtask preserved the intended direction, ordinal, or output. Per-stage proposals remain in the local journal for review.

A larger run requires verified correct answers from a sequential arm and refusal behavior at least as strong as the single-call typed baseline.

## Results

All 40 arm–question pairs completed on Ollama 0.40.17 using `qwen3:4b-instruct` Q4_K_M (digest `0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0`). Seed 43, temperature 0, 1,200 maximum output tokens per call, and reasoning off were consistent across arms.

| Arm | Correct answers | Correct refusals | Typed-valid plans | Accepted incorrect | Calls | Input / output tokens | Total p50 / p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 0/6 | 1/4 | 0 | 0 | 9 | 6,016 / 3,896 | 48.8 / 89.7 s |
| B | 0/6 | 3/4 | 0 | 0 | 9 | 3,082 / 1,715 | 27.3 / 39.5 s |
| C | 0/6 | 4/4 | 0 | 0 | 9 | 1,435 / 1,142 | 15.7 / 18.2 s |
| D | 0/6 | 4/4 | 0 | 0 | 9 | 1,435 / 1,160 | 15.5 / 19.2 s |

Across all cases, A first failed on bindings six times, schema twice, and parameters once. B first failed on schema four times and bindings three times. C and D abstained at decomposition on all six supported cases. None of their supported responses preserved the expected task count or literal mentions. No live run reached a separate grounding or dependency call. Its operation, constraint, and dependency checks therefore have no observations. The early exits account for the lower sequential latency. Each arm refused the authorization control before inference.

The decomposition prompt received only operation names and descriptions. In this catalog, the `shortest_path` description is empty, while its path semantics and the source and target person domains are stored elsewhere. Model responses cited missing path support or an undefined mapping from IDs to people. This experiment exposes a missing-context problem before it can distinguish two-stage from three-stage composition. Any follow-up should test a catalog-derived capability view that includes result semantics and parameter domains without changing the verifier or supplying benchmark examples.

The smoke gate failed, and the experiment stopped at ten questions. Full pytest through RTK completed with 374 passed and one skipped.
