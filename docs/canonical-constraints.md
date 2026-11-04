# Canonical constraints and optional Jev classification

The request and proposed plan now have separate semantic representations. The
engine compares them before any adapter executes. The existing lexical guard
remains an additional check.

## Taxonomy and representation

`canonical.py` defines versioned constraints for source/entity scope, row
predicates, aggregation, grouping, ordering, limits, distinctness, traversal,
projection, and joins. Predicates form explicit AND/OR/NOT trees. Literals retain
their numeric, string, Boolean, date, or null type. Numbers use bounded decimal
strings to avoid float rounding in canonical comparisons; Boolean true is not
numeric one. Null tests use `is_null`/`is_not_null`, not equality to null.

AND/OR terms are normalized for commutativity, associativity, and duplicates.
AND is never turned into OR. Ordering-key order remains meaningful. This is a
restricted canonical comparison, not an arbitrary logical-equivalence theorem
prover. Time zones, unit conversions, relative dates, and Boolean rewrites under
unknown/null semantics are not inferred.

For example, “average fare excluding negative fares” has these requirements:

- Scope: `trips`.
- Aggregate: mean of `trips.fare_amount`.
- Predicate: `trips.fare_amount >= 0` with numeric zero.

Using `> 0` would incorrectly exclude zero fares. Selecting the unfiltered mean
operation drops a predicate. Selecting a distance mean changes the measured field.
All three differences must fail preservation validation.

`CanonicalRequest.from_dict` validates the serialized contract. The lower-level
`validate_preservation` compares complete supplied constraint sets and reports
missing and invented requirements. It does not execute user-supplied SQL or code.

## Planning and enforcement

`extract_constraints(question, catalog)` is a bounded deterministic extractor.
It does not read the proposed plan. It recognizes selected categorical/numeric
filters, aggregates, grouping keys, limits, distinctness, and relationship
endpoints. Exclusion is normalized into its actual comparison where understood.
Unresolved temporal, ordering, or complex Boolean meaning is recorded rather
than guessed. Typed trees can represent constraints that the text extractor or
execution adapters cannot yet support.

`implemented_constraints(plan, source)` derives semantics from the validated
operation and bound parameters, not from model-provided assertions. Its registry
describes the existing fixed adapters; changes to adapters require matching
semantic-registry tests. This is not independent parsing or verification of
arbitrary SQL/Python. A malicious or incorrect adapter implementation can still
violate its declaration.

The engine compares categories the extractor recognized, blocks unresolved
extraction, and records `canonical_validation` in responses and evaluation
trials. A scope-recognized query also rejects invented filters, grouping, limits,
or distinctness. For abstentions, extraction is recorded but preservation is null:
no execution was requested. Original proposals are retained when blocked.

Coverage is partial: undetected wording, fields, or relationships can evade this
extractor. Passing validation does not certify correct natural-language
interpretation. Supported adapter defaults such as row ordering are not treated
as invented user constraints. Complete projection/ordering/default semantics and
arbitrary joins are future work. Existing adapters still abstain on constraints
they cannot execute; this milestone does not add arbitrary filtered queries.

## Offline development evaluation

```powershell
python -m polyquery.cli evaluate-constraints --dataset benchmarks/constraints-dev.json --output benchmarks/constraints-dev-report.json
```

The 16 labels were authored explicitly rather than copied from the extractor.
They require independent review and are development checks, not held-out
research evidence. Exact canonical extraction currently succeeds on 14/16 cases.
Ordering/limit interpretation and numeric disjunction remain unresolved. The
report separately gives constraint-kind precision/recall; recognizing “predicate”
does not prove that its field, operator, value, or Boolean structure is correct.
This command needs no API, dataset imports, or downloaded raw files.

## Jev: classification only

An optional Jev adapter follows the
[OpenRouter classification guide](https://openrouter.ai/docs/cookbook/evaluate-and-optimize/jev-classification).
It uses one `noul` question per taxonomy kind rather than mutually exclusive
SQL/graph/Python categories. Each response is validated as a probability in [0,1].

```powershell
$taskKey = Read-Host "OpenRouter API key" -AsSecureString
$env:OPENROUTER_API_KEY = [System.Net.NetworkCredential]::new("", $taskKey).Password
python -m polyquery.cli classify-constraints "Average fare excluding negative fares" --source taxi --model typesafe/jev-1.13 --threshold 0.5
```

The guide's model ID is explicit in this example; use a version available to
your account. This command makes one paid request to the documented alpha
Decisions endpoint, with a 30-second timeout and no retries. It sends only the
question, source name, and tag definitions, not data rows or gold labels. It
records the resolved model, question-definition hash, probabilities, threshold,
and provider usage. Missing monetary cost stays null, not zero.

The 0.5 threshold is an uncalibrated development default, not a correctness
guarantee. Tune tag thresholds using independently labeled training/validation
examples, freeze the definitions and thresholds, then evaluate on new untouched
questions. Jev has only mocked transport tests here; no OpenRouter key was
configured and no live Jev accuracy, cost, or latency result is claimed.

Jev is deliberately a standalone classifier for now. Its tags cannot authorize
execution or suppress canonical validation, and it is not yet wired into the LLM
prompt. The next experiment is Jev-assisted versus unassisted extraction on the
same reviewed labels, followed by end-to-end answer/cost evaluation. Preserve
the keyword baseline and historical challenge files throughout.
