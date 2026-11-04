# Constraint preservation

A structurally valid plan can still answer the wrong question. For example,
`mean_fare` has no exclusion parameter: selecting it for “average fare excluding
negative fares” would silently broaden the request.

The engine now runs a deterministic constraint check after plan validation and
before backend execution. A rejected plan becomes an explicit abstention with
`constraint_check.allowed=false`, named findings, and the original `proposed_plan`.
No backend executes that plan. The check applies to keyword, LLM, fallback, and
cost-aware planners alike. Provider usage still includes a completed LLM call
even if its proposed plan is subsequently blocked.

The initial guard detects known exclusion, comparison, time, ordering/limit,
quantile, conditional, sign-filter, grouping, and write markers. It checks
explicit statistic names, synthetic department filters, synthetic relationship
endpoints, and LDBC person IDs. Tabular qualifiers that cannot be represented
by current parameter-free operations are conservatively blocked. Known supported
constructions, such as negative-fare counts and country revenue grouping, remain
allowed. The existing synthetic regression cases remain supported.

This is a bounded lexical guard, not general semantic equivalence checking.
Unrecognized synonyms and field substitutions can still evade it. Legitimate
wording may be rejected conservatively. A passing check says only that no known
mismatch was found. Result correctness and coverage must still be evaluated
against independently reviewed labels. The guard does not add filtered-query
execution capabilities or claim that every unrecognized constraint is handled.

Evaluation records the guard findings and rejected proposal in each trial.
Unintended guard abstentions on answerable requests count as incorrect; they
are not successful null answers. Historical challenge data/profiles/reports
are unchanged. New timings include guard overhead.

The [canonical constraint layer](canonical-constraints.md) now adds typed
requirements, adapter-declared semantics, preservation validation, and separate
extraction evaluation. Independent annotation review remains pending. Until
adapters implement a constraint, preserving it means abstaining rather than
dropping it.
