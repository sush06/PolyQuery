"""Offline extraction evaluation against explicitly authored canonical labels."""
import hashlib
import json
from types import SimpleNamespace
from .canonical import CanonicalRequest, TAXONOMY, validate_preservation
from .constraint_planning import extract_constraints


def evaluate_constraint_cases(dataset, extractor=extract_constraints):
    from .data import EMPLOYEES
    from .evaluation import implementation_fingerprint
    cases=dataset.get('cases') if isinstance(dataset,dict) else None
    if not isinstance(cases,list) or not cases:
        raise ValueError('Expected a nonempty canonical-label case list')
    results=[]; ids=set()
    counters={kind:{'tp':0,'fp':0,'fn':0} for kind in TAXONOMY}
    for case in cases:
        if not isinstance(case,dict) or not {'id','source','question','expected'}<=case.keys():
            raise ValueError('Cases require id, source, question and expected canonical request')
        if type(case['id']) is not str or not case['id'] or case['id'] in ids or type(case['question']) is not str:
            raise ValueError('Invalid or duplicate case ID/question')
        ids.add(case['id'])
        if case['source'] not in ('synthetic','chinook','taxi','ldbc'):
            raise ValueError('Unknown source')
        gold=CanonicalRequest.from_dict(case['expected'])
        if gold.source!=case['source']: raise ValueError('Gold source mismatch')
        context=SimpleNamespace(source=case['source'],domains={'department':{r['department'] for r in EMPLOYEES},'employee':{r['name'] for r in EMPLOYEES}})
        extracted=extractor(case['question'],context)
        comparison=validate_preservation(gold,extracted.request)
        expected_tags={c.kind for c in gold.constraints}; predicted_tags={c.kind for c in extracted.request.constraints}
        for kind,counts in counters.items():
            counts['tp']+=kind in expected_tags and kind in predicted_tags
            counts['fp']+=kind not in expected_tags and kind in predicted_tags
            counts['fn']+=kind in expected_tags and kind not in predicted_tags
        results.append({'id':case['id'],'source':case['source'],'question':case['question'],
                        'exact_match':comparison['preserved'] and not extracted.unresolved,
                        'missing':comparison['missing'],'invented':comparison['invented'],
                        'extraction':extracted.to_dict()})
    for counts in counters.values():
        counts['precision']=counts['tp']/(counts['tp']+counts['fp']) if counts['tp']+counts['fp'] else None
        counts['recall']=counts['tp']/(counts['tp']+counts['fn']) if counts['tp']+counts['fn'] else None
    return {'dataset_sha256':hashlib.sha256(json.dumps(dataset,sort_keys=True).encode()).hexdigest(),
            'implementation_sha256':implementation_fingerprint(),
            'cases':len(results),'exact_match_accuracy':sum(r['exact_match'] for r in results)/len(results),
            'constraint_kind_metrics':counters,'results':results,
            'note':'Authored development labels; not independently reviewed held-out evidence. Kind detection does not prove field/operator/value preservation. No model or dataset execution is needed.'}
