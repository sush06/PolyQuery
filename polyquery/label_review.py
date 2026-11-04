"""Audit draft canonical labels without running or tuning a planner."""
import hashlib
import json
from collections import Counter
from decimal import Decimal
from datetime import date
from pathlib import Path

from .canonical import CanonicalRequest,TAXONOMY

SCHEMA={
 'synthetic':{'employees':{'name','department','salary','experience'},'sales':{'department','amount'}},
 'taxi':{'trips':{'fare_amount','total_amount','trip_distance','passenger_count','tpep_pickup_datetime','tpep_dropoff_datetime'}},
 'chinook':{'Invoice':{'Total','BillingCountry','InvoiceDate'},'Track':{'GenreId'},'Genre':{'GenreId','Name'}},
 'ldbc':{'person':{'id','first_name','last_name'}},
}
STRING_FIELDS={'employees.name','employees.department','sales.department','Invoice.BillingCountry',
               'Genre.Name','person.id','person.first_name','person.last_name'}
DATE_FIELDS={'trips.tpep_pickup_datetime','trips.tpep_dropoff_datetime','Invoice.InvoiceDate'}


def predicate_truth(predicate,row):
    """Small independent SQL-three-valued evaluator for annotation witnesses."""
    op=predicate.operator
    if op in ('and','or','not'):
        values=[predicate_truth(child,row) for child in predicate.children]
        if op=='not': return None if values[0] is None else not values[0]
        if op=='and': return False if False in values else None if None in values else True
        return True if True in values else None if None in values else False
    left=row.get(predicate.field)
    if op=='is_null': return left is None
    if op=='is_not_null': return left is not None
    if left is None: return None
    right=predicate.value.value
    if predicate.value.kind=='number': left,right=Decimal(str(left)),Decimal(right)
    elif predicate.value.kind=='date': left,right=date.fromisoformat(left),date.fromisoformat(right)
    return {'eq':lambda:left==right,'ne':lambda:left!=right,'gt':lambda:left>right,'ge':lambda:left>=right,
            'lt':lambda:left<right,'le':lambda:left<=right}[op]()


def _check_field(source,field):
    if '.' not in field: raise ValueError('Field is not source-qualified: '+field)
    table,column=field.split('.',1)
    if table not in SCHEMA[source] or column not in SCHEMA[source][table]|{'*'}:
        raise ValueError('Field absent from annotation schema: '+field)


def audit_labels(dataset,splits):
    cases=dataset.get('cases',[])
    if not cases: raise ValueError('Empty label pool')
    ids=set(); questions=set(); families={}; issues=[]
    by_status=Counter(); by_source=Counter(); by_phenomenon=Counter(); by_split=Counter()
    split_coverage={name:Counter() for name in ('train','validation','test')}
    witnesses=0; unique_witnesses=set()
    for case in cases:
        identifier=case.get('id','<missing>')
        try:
            required={'id','source','question','family_id','leakage_group','phenomenon','expected','expected_tags','label_status','annotation_notes','review','context'}
            if not required<=case.keys(): raise ValueError('Missing annotation fields')
            if identifier in ids: raise ValueError('Duplicate ID')
            ids.add(identifier)
            normalized=' '.join(case['question'].lower().split()).rstrip('.?!')
            if normalized in questions: raise ValueError('Duplicate normalized question')
            questions.add(normalized)
            if not case['annotation_notes'].strip(): raise ValueError('Missing rationale')
            if set(case['expected_tags'])-set(TAXONOMY): raise ValueError('Unknown constraint tag')
            source=case['source']; status=case['label_status']
            if source not in SCHEMA: raise ValueError('Unknown source')
            if status not in ('canonical','clarify','representation_gap'): raise ValueError('Unknown label status')
            if status!='canonical':
                if case['expected'] is not None: raise ValueError('Unresolved/gap cases must not invent a full canonical label')
                if status=='representation_gap' and not case['context'].get('intended_semantics'):
                    raise ValueError('Representation gap needs an explicit intended-semantics description')
            else:
                request=CanonicalRequest.from_dict(case['expected'])
                if request.source!=source: raise ValueError('Gold source mismatch')
                if set(case['expected_tags'])!={c.kind for c in request.constraints}: raise ValueError('Gold tags disagree with canonical label')
                def check_predicate(predicate):
                    if predicate.field:
                        _check_field(source,predicate.field)
                        if predicate.value is not None:
                            expected_type='string' if predicate.field in STRING_FIELDS else 'date' if predicate.field in DATE_FIELDS else 'number'
                            if predicate.value.kind!=expected_type: raise ValueError('Predicate literal type disagrees with annotation field type')
                    for child in predicate.children: check_predicate(child)
                for constraint in request.constraints:
                    if constraint.kind in ('scope','limit','distinct','projection','traversal'):
                        if constraint.field not in SCHEMA[source]: raise ValueError('Unknown entity scope')
                    elif constraint.kind!='predicate':
                        for field in constraint.field.split(','): _check_field(source,field)
                    if constraint.predicate: check_predicate(constraint.predicate)
                    if constraint.kind=='join': _check_field(source,constraint.value)
                    if constraint.kind=='projection':
                        for field in constraint.value.split(','): _check_field(source,constraint.field+'.'+field)
                predicates=[c.predicate for c in request.constraints if c.kind=='predicate']
                if predicates:
                    policy=case['context'].get('null_policy',{})
                    if policy.get('logic')!='sql_three_valued' or policy.get('row_retention')!='only_TRUE':
                        raise ValueError('Predicate requires explicit SQL null policy')
                for witness in case.get('witnesses',[]):
                    if len(predicates)!=1: raise ValueError('Witness needs exactly one predicate tree')
                    actual=predicate_truth(predicates[0],witness['row'])
                    if actual is not witness['expected_truth']: raise ValueError('Boundary/Boolean witness failed')
                    witnesses+=1
                    unique_witnesses.add(json.dumps([source,predicates[0].canonical().to_dict(),witness],sort_keys=True))
            if case['family_id']=='employee-limit-only' and case['context'].get('answer_policy',{}).get('scorer')!='unordered_valid_subset':
                raise ValueError('Unordered limit requires subset validity scoring')
            if case['family_id']=='path-hop-bound' and case['context'].get('answer_policy',{}).get('scorer')!='bounded_shortest_path':
                raise ValueError('Path requires shortest-path validity scoring')
            group=case['leakage_group']
            split=splits['group_assignments'][group]
            if split not in ('train','validation','test') or splits['case_assignments'][identifier]!=split:
                raise ValueError('Case split disagrees with leakage-group split')
            signature=json.dumps({k:case[k] for k in ('source','label_status','expected','expected_tags','context','leakage_group')},sort_keys=True)
            if case['family_id'] in families and families[case['family_id']]!=signature:
                raise ValueError('Paraphrase family has conflicting labels/context/split')
            families[case['family_id']]=signature
            review=case['review']
            if review.get('independent_review')=='approved' and not review.get('reviewer'):
                raise ValueError('Approval has no independent reviewer identity')
            by_status[status]+=1; by_source[source]+=1; by_phenomenon[case['phenomenon']]+=1; by_split[split]+=1
            split_coverage[split][case['phenomenon']]+=1
        except (ValueError,KeyError,TypeError,AttributeError) as error:
            issues.append({'id':identifier,'issue':str(error)})
    if set(splits.get('case_assignments',{}))!=ids: issues.append({'id':'splits','issue':'Split IDs do not equal label-pool IDs'})
    warnings=[{'split':name,'missing_phenomena':sorted(set(by_phenomenon)-set(counts))} for name,counts in split_coverage.items() if set(by_phenomenon)-set(counts)]
    return {'cases':len(cases),'families':len(families),'leakage_groups':len(splits.get('group_assignments',{})),
            'issues':issues,'consistency_passed':not issues,'witness_checks':witnesses,'unique_witness_checks':len(unique_witnesses),
            'by_status':dict(by_status),'by_source':dict(by_source),'by_phenomenon':dict(by_phenomenon),'draft_split_counts':dict(by_split),
            'split_phenomenon_coverage':{name:dict(counts) for name,counts in split_coverage.items()},
            'coverage_warnings':warnings,
            'independently_approved_cases':sum(c.get('review',{}).get('independent_review')=='approved' and bool(c.get('review',{}).get('reviewer')) for c in cases),
            'research_ready':False,'note':'Structural, field, family, split and witness checks do not constitute independent semantic annotation review.'}


def _format_predicate(p):
    if p.operator in ('and','or'): return '('+(' '+p.operator.upper()+' ').join(_format_predicate(c) for c in p.children)+')'
    if p.operator=='not': return 'NOT '+_format_predicate(p.children[0])
    literal='' if p.value is None else ' '+p.value.kind+':'+json.dumps(p.value.value)
    return p.field+' '+p.operator+literal


def write_review_artifacts(root):
    root=Path(root)
    labels_path=root/'labels.json'; splits_path=root/'splits.json'
    dataset=json.loads(labels_path.read_text(encoding='utf-8')); splits=json.loads(splits_path.read_text(encoding='utf-8'))
    audit=audit_labels(dataset,splits)
    audit['labels_sha256']=hashlib.sha256(labels_path.read_bytes()).hexdigest()
    audit['splits_sha256']=hashlib.sha256(splits_path.read_bytes()).hexdigest()
    (root/'audit.json').write_text(json.dumps(audit,indent=2)+'\n',encoding='utf-8')
    if not audit['consistency_passed']: return audit
    lines=['# Constraint labels v2: review packet','',
           'Draft labels and split proposals. Automatic checks passed only when audit.json says so. No independent human approval is implied.', '',
           'Review meaning before model outputs. Check all variants, predicate scope, boundary/null/date policy, grouping stage, ordering, ties, identity, and unsupported fields. Record any disagreement and adjudicate before freezing a test split.', '']
    seen=set(); responses=[]
    for case in dataset['cases']:
        family=case['family_id']
        if family in seen: continue
        seen.add(family)
        variants=[c for c in dataset['cases'] if c['family_id']==family]
        lines.extend([f'## {family}', '',f"Source: {case['source']}; label: {case['label_status']}; group: {case['leakage_group']}; proposed split: {splits['case_assignments'][case['id']]}", ''])
        lines.extend(f"- `{c['id']}`: {c['question']}" for c in variants)
        lines.extend(['', 'Rationale: '+case['annotation_notes'], ''])
        if case['expected']:
            for constraint in CanonicalRequest.from_dict(case['expected']).constraints:
                value=_format_predicate(constraint.predicate) if constraint.predicate else f'{constraint.field} {constraint.operator}'+(' '+json.dumps(constraint.value) if constraint.value is not None else '')
                lines.append('- '+constraint.kind+': `'+value+'`')
        else: lines.append('No full canonical label: clarify or extend representation; do not invent a forced interpretation.')
        if case['context']: lines.extend(['','Context: `'+json.dumps(case['context'],sort_keys=True)+'`'])
        lines.extend(['','Independent review: **pending**.',''])
        responses.append({'family_id':family,'reviewer':None,'decision':'pending','disagreements':[],
                          'proposed_label_or_question_changes':None,'adjudication':None})
    (root/'review-packet.md').write_text('\n'.join(lines),encoding='utf-8')
    # Never overwrite a reviewer's decisions.
    response_path=root/'review-responses.template.json'
    if not response_path.exists(): response_path.write_text(json.dumps({'instructions':'Copy to a separate file before entering reviewer decisions. Do not mark agreement without inspecting every variant.','families':responses},indent=2)+'\n',encoding='utf-8')
    return audit
