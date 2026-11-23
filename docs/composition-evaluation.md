# Compositional generalization benchmark

The benchmark has 24 questions from Chinook, Taxi, LDBC, and the synthetic
employee fixture. Validation and final test each contain two questions for
each of six categories. They cover unfamiliar paraphrases of supported
analyses, new operation combinations, cross-backend bindings, branching DAGs,
unsupported or ambiguous requests, and adversarial requests.

## Catalog audit

| Source | Executable composition in the existing catalog |
| --- | --- |
| Chinook | SQL count or grouped revenue stages and a code median can return separate ordered results. The source exposes no scalar input contracts for binding those aggregate outputs into a later stage. |
| Taxi | Fare, trip amount, and trip-distance reductions compose as ordered independent outputs. The catalog exposes no location joins or writable operations. |
| LDBC | A SQL friends result can supply a selected row's ID to a graph traversal. Two traversals can consume the same selected ID. Shortest-path references accept valid ties. |
| Synthetic | A SQL roster row supplies a manager name to a graph operation. A graph employee-ID list supplies the statistics backend. A single lookup can feed independent direct and downstream branches. |

Admission evaluates every node from independent source computations and
checks the result against the existing DAG executor, including intermediates.
The reference interpreter resolves bindings against independently computed
values. Matches require the expected result shape and value, with validated
shortest-path ties allowed. The refusal cases use explicit reason labels.
Their reference graphs contain zero nodes.

Each entry records reference node count, distinct reference backends,
longest dependency path in edges, and maximum direct fan-out. Explicit order
edges and data-binding edges both count toward depth. A sequencing edge
orders stages. A data-binding edge also carries a value. Results distinguish
the wording category from the complexity band.

The authoring checks reject normalized question duplicates against Evaluation
v2 and reject shared families or equivalent operation-and-dependency shapes
across the new splits. The 18 previous validation questions are excluded.
The reference shapes ignore backend choices and literal entity IDs when
checking split overlap.

## Validate and freeze

\`\`\`bash
python -m polyquery.composition_evaluation --ollama --output logs/composition-validation.json
\`\`\`

The runner compares deterministic DAG, baseline Qwen3 4B, examples-only Qwen,
and catalog-plus-examples Qwen. All model configurations use temperature 0,
a 1,200-token output cap, and repair disabled. The example arms reuse the
same historical development examples. No benchmark DAG or answer is sent to
the model. Report rows include decision accuracy, conditional answer
accuracy, coverage, failures, safety refusals, calls, tokens, and latency.
Paired intervals and transitions compare matching question IDs overall and
by complexity band. Null answer accuracy means an arm returned no answers.

The final manifest is separate from validation. The validation runner does
not load its questions. Reference admission checks both manifests without
querying planners. After configuration selection, freeze a complete live
validation report, then use the receipt to open the final partition.

\`\`\`bash
python -m polyquery.composition_evaluation --freeze-report logs/composition-validation.json --output logs/composition-selection.json
python -m polyquery.composition_evaluation --split test --ollama --selection logs/composition-selection.json --output logs/composition-test.json
\`\`\`

This is a procedural seal. The repository contains authored test questions,
and the evaluation runner gates model access until it receives a selection
receipt. The receipt binds the implementation, benchmark manifests, model
digest and server version, timeout, and development examples. The separate
reference admission command records fixture hashes and both manifest digests.
Use the final split once after selection and avoid prompt or verifier changes
based on its outcomes.

These cases distinguish executable composition from planner coverage. The
executor supports row and list selectors. The current Qwen DAG wire format
exposes whole-result bindings, and preflight also requires recognized
semantics. Deterministic DAG supports bounded sequence and binding patterns.
An abstention on an executable reference can reflect any of these limits.
A small authored validation set cannot establish generalization.
