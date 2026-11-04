"""Conservative, deterministic checks for known constraint-loss patterns.

This is not a general semantic equivalence proof. Unrecognized language can
still evade detection; independent answer evaluation remains necessary.
"""
import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ConstraintCheck:
    allowed: bool
    findings: tuple[str, ...]
    guard_version: int = 1
    scope: str = 'bounded lexical checks; not a semantic-equivalence guarantee'

    def to_dict(self):
        return asdict(self)


def check_constraints(question, plan, catalog):
    if plan.backend == 'abstain':
        return ConstraintCheck(True, ('No execution requested.',))
    if not isinstance(question, str):
        return ConstraintCheck(False, ('Executable plan requires a text question.',))
    q = question.lower()
    failures = []
    # The one supported correlation construction is not a range constraint.
    modifiers = re.sub(r'\bbetween salary and experience\b', '', q) if plan.operation == 'correlation' else q
    # Endpoints of a relationship path can be introduced with "between".
    if plan.operation == 'shortest_path' and re.search(r'\b(path|connection|chain)\b', q):
        modifiers = re.sub(r'\bbetween\b', '', modifiers)
    patterns = {
        'exclusion_or_negation': r'\b(exclud\w*|except|without|ignoring|not|nonnegative|non-negative)\b',
        'comparison_or_range': r'\b(above|below|over|under|greater|less|at least|at most|more than|fewer than|between)\b|[<>!=]',
        'temporal_filter': r'\b(before|after|since|until|during|yesterday|today|last month|last year)\b',
        'ordering_or_limit': r'\b(top|bottom|first|last|limit|sorted|ascending|descending|order by)\b',
        'unsupported_quantile_or_uniqueness': r'\b(percentile|distinct|unique)\b',
        'write_or_instruction': r'\b(delete|drop|insert|update|alter|truncate|execute|overwrite|ignore instructions)\b',
        'conditional_clause': r'\b(where|having|only|whose|such that)\b',
    }
    for label, pattern in patterns.items():
        if re.search(pattern, modifiers):
            failures.append(label)
    if re.search(r'\b(positive|negative|zero)\b', q) and (plan.operation != 'negative_fares' or re.search(r'\b(positive|zero)\b',q)):
        failures.append('unsupported_sign_filter')
    if plan.operation == 'negative_fares' and not re.search(r'\bnegative\s+fares?\b',q):
        failures.append('negative_fare_predicate_invented')
    # Current grouping operations have specific measures and grouping keys.
    grouped = bool(re.search(r'\b(by|per|each)\b', q))
    if grouped:
        valid_group = (catalog.source == 'chinook' and
                       ((plan.operation == 'revenue_by_country' and re.search(r'\bcountr\w*\b', q)) or
                        (plan.operation == 'tracks_by_genre' and re.search(r'\bgenres?\b', q))))
        if not valid_group:
            failures.append('unrepresented_grouping')
    if re.search(r'\b(median|midpoint)\b', q) and plan.operation not in ('median','median_fare'):
        failures.append('changed_statistic_median')
    if re.search(r'\b(average|mean)\b', q) and plan.operation not in ('average_salary','mean','mean_fare','mean_distance'):
        failures.append('changed_statistic_mean')
    if re.search(r'\b(standard deviation|stddev)\b', q) and plan.operation != 'stdev':
        failures.append('changed_statistic_stdev')
    if re.search(r'\bcorrelation\b', q) and plan.operation != 'correlation':
        failures.append('changed_statistic_correlation')
    if catalog.source == 'synthetic':
        department_text = re.sub(r'\b(total|sum)\s+sales\b', '', q)
        departments = [d for d in catalog.domains['department'] if re.search(r'\b'+re.escape(d.lower())+r'\b',department_text)]
        requested = departments[0] if len(departments) == 1 else None
        explicit_scope = bool(departments or re.search(r'\b(employees?|salary|revenue|sales|headcount|team)\b',q))
        if len(departments)>1 or (explicit_scope and plan.parameters.get('department') != requested):
            failures.append('department_filter_dropped_changed_or_invented')
        names = [n for n in catalog.domains['employee'] if re.search(r'\b'+re.escape(n.lower())+r'\b',q)]
        names.sort(key=lambda n: re.search(r'\b'+re.escape(n.lower())+r'\b',q).start())
        if plan.operation in ('shortest_path','descendants'):
            expected = [plan.parameters['source']] + ([plan.parameters['target']] if plan.operation == 'shortest_path' else [])
            # Repeated same-person path questions legitimately mention a name twice.
            occurrences = re.findall(r'\b(?:'+ '|'.join(re.escape(n.lower()) for n in catalog.domains['employee']) +r')\b',q)
            if occurrences != [n.lower() for n in expected]:
                failures.append('relationship_endpoints_dropped_changed_or_invented')
        elif names:
            failures.append('unrepresented_employee_filter')
    numbers = re.findall(r'(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])',q)
    if catalog.source == 'ldbc' and plan.operation in ('friends','shortest_path'):
        expected = [plan.parameters['source']] + ([plan.parameters['target']] if plan.operation == 'shortest_path' else [])
        if numbers != expected:
            failures.append('person_ids_dropped_changed_or_extra_numeric_constraint')
    elif numbers:
        failures.append('unrepresented_numeric_constraint')
    # These tabular operations have no filter parameters at all.
    if catalog.source in ('chinook','taxi') and re.search(r'\b(for|from|with|in|excluding)\s+',q):
        # Explicitly scoped whole-file wording is harmless; arbitrary filters are not.
        residual = re.sub(r'\b(?:in|from) (?:the )?(?:database|dataset|file)\b','',q)
        if re.search(r'\b(for|from|with|in|excluding)\s+',residual):
            failures.append('unrepresented_tabular_qualifier')
    return ConstraintCheck(not failures, tuple(dict.fromkeys(failures)) or ('No known constraint mismatch detected.',))
