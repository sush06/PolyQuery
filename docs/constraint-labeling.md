# Constraint labels v2: annotation and review

The expanded pool contains **160 questions in 40 semantic families**, tied into
26 leakage groups. There are four related formulations per family. They are not
160 independent semantic tasks. The original 16 development cases and all older
challenge files remain unchanged. No extractor, model, or prompt was tuned in
this labeling phase, and no new test extraction scores were collected.

| Phenomenon | Questions |
| --- | ---: |
| Negation | 24 |
| Strict/inclusive numeric boundaries and hop bounds | 16 |
| Boolean scope and mixed AND/OR | 24 |
| Absolute and unanchored relative dates | 20 |
| Grouping versus row/post-aggregation filtering | 20 |
| Ordering, tie-breaking, and limits | 24 |
| Ambiguous fields, statistic, identity, or scope | 32 |

## Artifacts

- `benchmarks/constraint-labels-v2/labels.json`: questions, canonical labels where expressible, annotation rationale, context, expected taxonomy tags, and pending review status.
- `review-packet.md`: a readable family-by-family packet with all variants and interpretations.
- `review-responses.template.json`: copy before entering independent reviewer decisions and disagreements.
- `splits.json`: draft 96/32/32 train/validation/test assignment by leakage group.
- `annotation-schema.json`: the field vocabulary covered by these annotations, not an execution-capability catalog.
- `audit.json`: field/contract/family/split checks, boundary witnesses, hashes, and review counts.

The split proposal balances size by whole groups; it is not guaranteed to be
stratified by phenomenon. Inspect `split_phenomenon_coverage` before freezing an
experiment or calibrating per-tag thresholds. Closely linked minimal contrasts
share a group: strict versus inclusive boundaries, Boolean parenthesization,
negated AND versus negated OR, and country grouping versus filtering cannot be
separated merely because the wording differs.

## Label policy

**112 cases have canonical labels.** These describe requested meaning whether
or not current adapters can execute it. Unsupported filters are not relabeled as
intentional abstentions merely because the implementation lacks them.

**36 cases require clarification.** They lack an identifiable monetary field,
timestamp field, reference clock, statistic, entity ID, or Boolean attachment.
They have no forced full canonical label. Under this conservative annotation
protocol, backend capability alone cannot resolve an ambiguous user request.

**12 cases expose representation gaps.** HAVING filters require a post-aggregation
predicate stage. Numeric ordering of string person IDs requires collation/coercion
semantics. Case-sensitive Unicode code-point name ordering also needs explicit collation metadata. Canonical v1 cannot encode these completely, so intended meaning is
recorded explicitly rather than mislabeled as WHERE or ordinary string ordering.
The evaluator for the original canonical-only cases is not a scorer for this
mixed-status draft pool; do not silently discard clarification/gap cases to
inflate exact-match accuracy.

Boundary policies are explicit:

- Excluding negative fares retains zero; excluding positive fares also retains zero.
- A value equal to 100000 satisfies `>= 100000`, not `> 100000`.
- NOT applies to its stated Boolean subtree; SQL UNKNOWN remains UNKNOWN under NOT.
- Explicit date literals denote midnight at the start of the date in stored wall-clock time, with no timezone conversion. Relative dates without anchors require clarification.
- WHERE filters individual invoices; HAVING filters grouped totals.
- Ordering-key sequence matters. A limit without a required order does not invent a ranking.
- LDBC hop bounds count edges. Names do not replace IDs when more than one person matches.

The ambiguous-name family uses Aaron: two matches were checked in the supplied
people file. Its hash is recorded in label metadata so this evidence can be
rechecked if data changes. Projection defaults and numeric-ID ordering context
must be reviewed explicitly rather than inferred from a model output.

## Review performed and still needed

Consistency review found no current structural/field/family/split issues. There
are 45 unique boundary/Boolean/date witnesses, checked across the four variants
for 180 checks. Repeated witnesses are not independent annotation evidence.
Two labeling hazards were corrected during author review: ambiguous-name examples
must actually be non-unique, and numeric string-ID ordering must not claim a
complete canonical label without ordering-type metadata.

**No case is independently approved.** Automatic audits and the author's
consistency pass do not replace a second annotator. All labels and splits remain
draft, `research_ready` is false, and these files must not be described as
independently labeled held-out evidence.

For independent review:

1. Read the packet and annotation policies without viewing model predictions.
2. Check every formulation in a family, including field, operator, literal type,
   Boolean scope, null/date policy, grouping stage, projection, and ordering.
3. Record agree/revise/clarify decisions, reviewer identity, alternative labels,
   and disagreements in a copy of the response template.
4. Adjudicate disagreements and revise labels deliberately. Retain review history;
   do not automatically mark approval from audit success.
5. Freeze reviewed labels, split assignments, and threshold configuration before
   scoring a new test partition. If tuning uses test failures, create a new test version.

## Audit commands

```powershell
python -m polyquery.cli audit-constraint-labels --pool benchmarks/constraint-labels-v2
python -m unittest discover -s tests -v
```

The audit rewrites generated audit/packet files but never overwrites reviewer
responses. It does not call models, score extraction on test, or alter parser
behavior. `benchmarks/build_constraint_labels.py` contains the authored family
specifications; it refuses to overwrite an existing directory. Use a fresh output
version when regenerating, then reconcile review changes explicitly.


## Policy revisions after review

Every predicate item now supplies SQL three-valued null policy in its context:
only TRUE retains a row, and NOT UNKNOWN remains UNKNOWN. In particular, a null
experience and salary 120000 does not pass not-both. This supplied policy resolves
the possible keep-null reading of plain-English exclusion. Added witnesses cover
these null cases. Boolean, date, and fare-sign rationales now explain each item.

Employee row prompts explicitly request name, department, salary, and experience.
Name ordering uses case-sensitive Unicode code-point order without normalization
or locale rules, and is a representation gap until canonical collation exists.
Grouped Chinook contexts omit groups emptied by row filters, preserve a null SUM
for existing all-null groups, and combine null grouping keys into one group.

`polyquery/label_answers.py` provides validity scorers for non-unique answers.
An unordered employee limit requires min(3, eligible rows) distinct full projected
rows; any valid subset/order passes. Reference input must contain all eligible
rows, not a single arbitrarily selected answer. A bounded path accepts any tied
shortest path within three edges and requires successful null when disconnected
or beyond the bound. Abstention is never a correct null answer. Graph references
must be independently prepared. These scorers are tested utilities; the old
canonical-only evaluator does not automatically dispatch this mixed-status pool.

Policies supplied in context are part of the task input for future experiments.
Do not pass annotation-only `intended_semantics`, expected labels, rationales, or
witnesses to a router. Record exactly which public policies the model receives.
Splits and reviewer response files were preserved. Review remains pending.

The [paired router comparison](router-comparison.md) now dispatches validity
scoring and independent reference execution for canonical cases in this pool.
It keeps clarification and representation-gap outcomes separate and does not
replace the original canonical extraction evaluator.
