"""Paired router experiments on draft labels, with independent answer scoring."""
import hashlib
import json
import math
import platform
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .canonical import CanonicalRequest, validate_preservation
from .constraint_planning import implemented_constraints
from .engine import Engine
from .evaluation import implementation_fingerprint, source_fingerprint, timing_summary
from .label_oracle import LabelOracle
from .label_review import audit_labels
from .planner import Plan

PUBLIC_CONTEXT = {'null_policy', 'date_policy', 'group_policy', 'projection', 'collation',
                  'person_id_order', 'time_basis', 'nulls', 'date_interpretation', 'timezone',
                  'calendar', 'date_literal_boundary', 'timezone_conversion', 'interval'}


def task_context(case):
    """Whitelist task policies; never include gold, witnesses, or rationales."""
    context = {key: value for key, value in case['context'].items() if key in PUBLIC_CONTEXT}
    policy = case['context'].get('answer_policy', {})
    if policy.get('scorer') == 'bounded_shortest_path':
        context['path_policy'] = {key: policy[key] for key in ('ties', 'no_path_within_bound', 'hop_unit')}
    return context


def task_input(case):
    return json.dumps({'question':case['question'],'task_context':task_context(case)},sort_keys=True)


def summarize_runs(rows):
    canonical = [r for r in rows if r['label_status'] == 'canonical']
    clarification = [r for r in rows if r['label_status'] == 'clarify']
    answered = [r for r in canonical if r['status'] == 'ok']
    def rate(values):
        return sum(values) / len(values) if values else None
    usages = [r['usage'] for r in rows]
    calls = sum(u.get('api_calls', 0) for u in usages)
    known = all(not u.get('api_calls') or (u.get('input_tokens') is not None and u.get('output_tokens') is not None) for u in usages)
    costs = [r['estimated_api_usd'] for r in rows]
    return {'runs': len(rows), 'canonical_runs': len(canonical),
            'representation_gap_runs': sum(r['label_status'] == 'representation_gap' for r in rows),
            'proposed_constraint_preservation': rate([r['constraint_preserved'] for r in canonical]),
            'executed_constraint_preservation': rate([r['constraint_preserved'] for r in answered]),
            'answer_accuracy': rate([r['answer_correct'] for r in canonical]),
            'answered_accuracy': rate([r['answer_correct'] for r in answered]),
            'clarification_abstention_recall': rate([r['status'] == 'abstained' for r in clarification]),
            'abstention_rate': rate([r['status'] == 'abstained' for r in rows]),
            'canonical_abstention_rate': rate([r['status'] == 'abstained' for r in canonical]),
            'coverage': rate([r['status'] == 'ok' for r in rows]),
            'error_rate': rate([r['status'] == 'error' for r in rows]),
            'guard_block_rate': rate([r['guard_blocked'] for r in rows]),
            'latency_ms': {field: timing_summary([r[field] for r in rows if r[field] is not None])
                           for field in ('latency_ms', 'planning_ms', 'execution_ms')},
            'correct_answer_latency_ms': timing_summary([r['latency_ms'] for r in canonical if r['answer_correct']]),
            'api_calls': calls,
            'input_tokens': sum(u.get('input_tokens') or 0 for u in usages) if known else None,
            'output_tokens': sum(u.get('output_tokens') or 0 for u in usages) if known else None,
            'estimated_api_usd': sum(costs) if all(c is not None for c in costs) else None,
            'cost_note': 'API estimate from supplied per-million-token rates; not billing evidence. Local compute, energy, and hardware costs unmeasured. Missing usage/pricing stays null.'}


def compare_routers(dataset, splits, sources, *, model=None, llm_kwargs=None,
                    repeats=1, keyword_only=False, input_rate=None, output_rate=None,
                    engine_factory=Engine, oracle_factory=LabelOracle):
    implementation = implementation_fingerprint()
    if type(repeats) is not int or repeats < 1:
        raise ValueError('Repeats must be a positive integer')
    if not keyword_only and not model:
        raise ValueError('Provide an explicit model, or use --keyword-only for a partial run')
    for value in (input_rate, output_rate):
        if value is not None and (type(value) not in (float, int) or not math.isfinite(value) or value < 0):
            raise ValueError('Token prices must be finite nonnegative USD per million')
    audit = audit_labels(dataset, splits)
    if not audit['consistency_passed']:
        raise ValueError('Label audit failed: ' + str(audit['issues']))
    required = {c['source'] for c in dataset['cases']}
    if not required <= sources.keys():
        raise ValueError('Missing source configurations: ' + ', '.join(sorted(required - sources.keys())))
    rows = []
    provenance = {}
    initialization = {}
    inputs = {c['id']: task_input(c) for c in dataset['cases']}
    for source in sorted(required):
        config = sources[source]
        oracle = oracle_factory(source, config)
        engines = {}
        try:
            engines['keyword'] = engine_factory(source=source, **config)
            if not keyword_only:
                engines['llm'] = engine_factory(source=source, planner_kind='llm', model=model,
                                                llm_fallback='none', **config, **(llm_kwargs or {}))
            hashes = {name: source_fingerprint(engine) for name, engine in engines.items()}
            if any(value != hashes['keyword'] for value in hashes.values()):
                raise ValueError('Routers loaded different data')
            provenance[source] = hashes['keyword']
            initialization[source] = {name: engine.initialization_ms for name, engine in engines.items()}
            cases = [c for c in dataset['cases'] if c['source'] == source]
            for repetition in range(repeats):
                for index, case in enumerate(cases):
                    # Alternate first router per question and repetition; same inputs,
                    # same persistent source instances, no keyword fallback or warmup.
                    names = list(engines)
                    if (index + repetition) % 2:
                        names.reverse()
                    for position, name in enumerate(names):
                        engine = engines[name]
                        started = time.perf_counter()
                        try:
                            response = engine.query(case['question'],task_context=task_context(case))
                            error = None
                        except Exception as exception:
                            response = {'status': 'error', 'latency_ms': (time.perf_counter()-started)*1000}
                            # Do not persist provider exception messages or secrets.
                            error = type(exception).__name__
                        usage = dict(response.get('planner_metadata', getattr(engine.planner, 'last_metadata', {})))
                        usage.pop('response_id', None)
                        usage.pop('error', None)
                        # A runtime guard is not the gold evaluator. Score proposed
                        # implemented semantics even if the guard blocked the plan.
                        comparison = None
                        if case['label_status'] == 'canonical':
                            proposed = response.get('proposed_plan', response.get('plan'))
                            if proposed and proposed['backend'] != 'abstain':
                                implemented = implemented_constraints(Plan.from_dict(proposed), source)
                                comparison = validate_preservation(CanonicalRequest.from_dict(case['expected']), implemented)
                        preserved = bool(comparison and comparison['preserved'])
                        answer_correct = oracle.score(case, response)
                        calls = usage.get('api_calls', 0)
                        usd = 0.0 if not calls else None
                        if calls and input_rate is not None and output_rate is not None and usage.get('input_tokens') is not None and usage.get('output_tokens') is not None:
                            usd = (usage['input_tokens'] * input_rate + usage['output_tokens'] * output_rate) / 1_000_000
                        rows.append({'id': case['id'], 'family_id': case['family_id'], 'leakage_group': case['leakage_group'],
                                     'source': source, 'phenomenon': case['phenomenon'], 'split': splits['case_assignments'][case['id']],
                                     'label_status': case['label_status'], 'router': name, 'repeat': repetition, 'position': position,
                                     'status': response['status'], 'error_type': error,
                                     'plan': response.get('plan'), 'proposed_plan': response.get('proposed_plan'),
                                     'guard_blocked': 'proposed_plan' in response,
                                     'constraint_preserved': preserved if case['label_status']=='canonical' else None,
                                     'constraint_comparison': comparison, 'answer_correct': answer_correct,
                                     'usage': usage, 'estimated_api_usd': usd,
                                     **{field: response.get(field) for field in ('latency_ms','planning_ms','execution_ms')}})
        finally:
            for engine in engines.values():
                engine.close()
            oracle.close()
    policies = sorted({r['router'] for r in rows})
    summaries = {name: summarize_runs([r for r in rows if r['router'] == name]) for name in policies}
    pairs = []
    if not keyword_only:
        lookup = {(r['id'],r['repeat'],r['router']): r for r in rows}
        for c in dataset['cases']:
            if c['label_status'] != 'canonical':
                continue
            for repetition in range(repeats):
                a, b = (lookup[(c['id'],repetition,name)] for name in ('keyword','llm'))
                pairs.append({'id':c['id'],'repeat':repetition,'family_id':c['family_id'],
                              'keyword_correct':a['answer_correct'],'llm_correct':b['answer_correct'],
                              'llm_minus_keyword_latency_ms':b['latency_ms']-a['latency_ms']})
    digest = lambda value: hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
    if implementation_fingerprint() != implementation:
        raise ValueError('Implementation changed during evaluation; rerun with stable source files')
    return {'report_version':1, 'created_at':datetime.now(timezone.utc).isoformat(),
            'python':platform.python_version(),'platform':platform.platform(),
            'llm_configuration':{key:value for key,value in (llm_kwargs or {}).items()
                                 if key in ('llm_base_url','llm_api','llm_timeout')},
            'complete_comparison':not keyword_only, 'model':model, 'fallback':'none',
            'labels_sha256':digest(dataset),'splits_sha256':digest(splits),'task_inputs_sha256':digest(inputs),
            'implementation_sha256':implementation, 'source_hashes':provenance,
            'initialization_ms':initialization, 'repeats':repeats,'warmup':0,
            'input_usd_per_million':input_rate,'output_usd_per_million':output_rate,
            'cases':len(dataset['cases']),'families':len({c['family_id'] for c in dataset['cases']}),
            'label_status_counts':dict(Counter(c['label_status'] for c in dataset['cases'])),
            'summaries':summaries,
            'by_source':{source:{name:summarize_runs([r for r in rows if r['source']==source and r['router']==name]) for name in policies} for source in sorted(required)},
            'by_family':{family:{name:summarize_runs([r for r in rows if r['family_id']==family and r['router']==name]) for name in policies} for family in sorted({r['family_id'] for r in rows})},
            'paired_answers':{'samples':len(pairs),'keyword_only_correct':sum(p['keyword_correct'] and not p['llm_correct'] for p in pairs),
                              'llm_only_correct':sum(p['llm_correct'] and not p['keyword_correct'] for p in pairs),'results':pairs},
            'notes':['Exploratory authored draft labels; no independently reviewed held-out or significance claim.',
                     'Canonical answer accuracy excludes clarify and representation gaps; errors and abstentions on canonical tasks score incorrect.',
                     'Constraint preservation is structural agreement of proposed operation semantics with authored gold, not shared extraction agreement or logical equivalence.',
                     'Clarification abstention is reported separately; gap abstentions are not counted as correct answers.',
                     'Both engines receive identical question and whitelisted policy context; keyword matching ignores context (unsupported capability), LLM receives it as task text. Guards inspect the original question. No annotation-only context is passed.',
                     'Per-source initialization and independent reference scoring excluded from query latency. Alternating order, no warmup; repeats/paraphrases are not independent tasks.',
                     'Reports intentionally omit answer payloads, provider response IDs and raw exception messages. Keep reports in ignored logs/.'],
            'results':rows}


def load_sources(path):
    path = Path(path).resolve()
    sources = json.loads(path.read_text(encoding='utf-8-sig'))
    for source, config in sources.items():
        allowed = {'people_path','edges_path'} if source == 'ldbc' else {'data_path'} if source in ('taxi','chinook') else set()
        if set(config) != allowed:
            raise ValueError('Unexpected source configuration fields: ' + source)
        for key, value in config.items():
            resolved = (path.parent / value).resolve()
            if not resolved.is_file():
                raise ValueError('Missing downloaded source: ' + str(resolved))
            config[key] = resolved
    return sources
