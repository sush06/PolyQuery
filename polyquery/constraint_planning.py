"""Bounded request extraction and adapter-declared plan semantics.

Extraction never reads a proposed plan. Semantics never reads model annotations.
"""
import re
from dataclasses import dataclass

from .canonical import CanonicalRequest, Constraint, Literal, Predicate, validate_preservation


@dataclass(frozen=True)
class Extraction:
    request: CanonicalRequest
    recognized_kinds: tuple[str,...]
    unresolved: tuple[str,...] = ()

    def to_dict(self):
        return {'request':self.request.to_dict(),'recognized_kinds':list(self.recognized_kinds),
                'unresolved':list(self.unresolved),'method':'bounded deterministic extraction v1'}


def implemented_constraints(plan,source):
    if plan.backend=='abstain':
        return CanonicalRequest(source)
    op=plan.operation
    scopes={
        'synthetic':{'revenue':'sales'},
        'chinook':{'count_tracks':'Track','count_customers':'Customer','tracks_by_genre':'Track'},
        'taxi':{},'ldbc':{},
    }
    default={'synthetic':'employees','chinook':'Invoice','taxi':'trips','ldbc':'person'}
    scope=scopes[source].get(op,default[source])
    facts=[Constraint('scope',scope,'rows')]
    measures={
        'synthetic':{'count':('count','employees.*'),'average_salary':('mean','employees.salary'),
                     'revenue':('sum','sales.amount'),'median':('median','employees.salary'),
                     'stdev':('sample_stdev','employees.salary'),'correlation':('pearson','employees.salary,employees.experience')},
        'chinook':{'count_tracks':('count','Track.*'),'count_customers':('count','Customer.*'),
                   'count_invoices':('count','Invoice.*'),'revenue':('sum','Invoice.Total'),
                   'mean':('mean','Invoice.Total'),'median':('median','Invoice.Total'),
                   'revenue_by_country':('sum','Invoice.Total'),'tracks_by_genre':('count','Track.*')},
        'taxi':{'count_trips':('count','trips.*'),'negative_fares':('count','trips.*'),
                'sum_fare':('sum','trips.fare_amount'),'sum_total':('sum','trips.total_amount'),
                'mean_distance':('mean','trips.trip_distance'),'mean_fare':('mean','trips.fare_amount'),
                'median_fare':('median','trips.fare_amount')},
        'ldbc':{'count_people':('count','person.*')},
    }
    if op in measures[source]:
        function,field=measures[source][op]
        facts.append(Constraint('aggregation',field,function))
    if op=='list' and source=='synthetic':
        facts.append(Constraint('projection','employees','fields','name,department,salary,experience'))
    predicates=[]
    if source=='synthetic' and plan.parameters.get('department'):
        predicates.append(Predicate('eq',scope+'.department',Literal('string',plan.parameters['department'])))
    if source=='taxi' and op=='negative_fares':
        predicates.append(Predicate('lt','trips.fare_amount',Literal('number','0')))
    if predicates:
        facts.append(Constraint('predicate','where','where',predicate=predicates[0]))
    if source=='chinook' and op in ('revenue_by_country','tracks_by_genre'):
        field='Invoice.BillingCountry' if op=='revenue_by_country' else 'Genre.Name'
        facts.append(Constraint('grouping',field,'key'))
        if op=='tracks_by_genre': facts.append(Constraint('join','Track.GenreId','inner','Genre.GenreId'))
        # Output ordering is a documented adapter default, not a requested filter.
    if op in ('shortest_path','descendants','friends'):
        facts.append(Constraint('traversal',scope,'source',plan.parameters['source']))
        if op=='shortest_path':
            facts.extend([Constraint('traversal',scope,'target',plan.parameters['target']),
                          Constraint('traversal',scope,'strategy','shortest_undirected')])
        else:
            facts.append(Constraint('traversal',scope,'strategy','direct_undirected' if op=='friends' else 'all_downstream_directed'))
    return CanonicalRequest(source,tuple(facts))


def extract_constraints(question,catalog):
    source=catalog.source
    if not isinstance(question,str) or not question.strip():
        return Extraction(CanonicalRequest(source),(),('empty_or_nontext_question',))
    q=question.lower()
    facts,unresolved=[],[]
    aliases={
        'synthetic':{'salary':'employees.salary','experience':'employees.experience'},
        'chinook':{'invoice total':'Invoice.Total','revenue':'Invoice.Total'},
        'taxi':{'fare amount':'trips.fare_amount','fare_amount':'trips.fare_amount','fares':'trips.fare_amount',
                'fare':'trips.fare_amount','trip distance':'trips.trip_distance','trip_distance':'trips.trip_distance',
                'distance':'trips.trip_distance','total_amount':'trips.total_amount','trip amount':'trips.total_amount'},
        'ldbc':{},
    }[source]
    scope={'synthetic':'employees','chinook':'Invoice','taxi':'trips','ldbc':'person'}[source]
    if source=='synthetic' and re.search(r'\b(revenue|sales)\b',q) and not re.search(r'\bsalary\b',q):
        scope='sales'
    if source=='chinook':
        if re.search(r'\btracks?\b',q): scope='Track'
        elif re.search(r'\bcustomers?\b',q) and not re.search(r'\binvoices?\b',q): scope='Customer'
    scope_known=bool(re.search(r'\b(employees?|salary|experience|headcount|revenue|sales|invoices?|tracks?|customers?|trips?|fare|fares|distance|people|persons?|friends?|path|connection|chain|reports)\b',q))
    if scope_known: facts.append(Constraint('scope',scope,'rows'))
    functions=[]
    for name,pattern in [('mean',r'\b(average|mean)\b'),('median',r'\b(median|midpoint)\b'),
                         ('sample_stdev',r'\b(standard deviation|stddev)\b'),('pearson',r'\bcorrelation\b')]:
        if re.search(pattern,q): functions.append(name)
    if re.search(r'\b(count|how many|number of|headcount)\b',q): functions.append('count')
    elif re.search(r'\b(sum|total|revenue)\b',q) and not functions: functions.append('sum')
    if len(functions)>1: unresolved.append('multiple_aggregate_functions')
    elif functions:
        function=functions[0]
        if function=='count': field=scope+'.*'
        elif function=='pearson' and source=='synthetic': field='employees.salary,employees.experience'
        elif source=='synthetic': field='sales.amount' if scope=='sales' else 'employees.salary'
        elif source=='chinook': field='Invoice.Total'
        else:
            fields={field for alias,field in aliases.items() if re.search(r'\b'+re.escape(alias)+r'\b',q)}
            if len(fields)==1: field=fields.pop()
            elif not fields and source=='taxi' and re.search(r'\btrip charges\b',q): field='trips.total_amount'
            else: field=None; unresolved.append('ambiguous_or_unknown_measure_field')
        if field: facts.append(Constraint('aggregation',field,function))
    predicates=[]
    if source=='synthetic':
        text=re.sub(r'\b(total|sum)\s+sales\b','',q)
        departments=[d for d in sorted(catalog.domains['department']) if re.search(r'\b'+re.escape(d.lower())+r'\b',text)]
        nodes=[]
        for d in departments:
            negative=bool(re.search(r'\b(?:excluding|except|without|not)\s+'+re.escape(d.lower())+r'\b',text))
            nodes.append(Predicate('ne' if negative else 'eq',scope+'.department',Literal('string',d)))
        if len(nodes)>1:
            if re.search(r'\bor\b',q): predicates.append(Predicate('or',children=tuple(nodes)))
            else: unresolved.append('multiple_department_boolean_scope')
        else: predicates.extend(nodes)
    # Numeric comparisons preserve their field and strict/inclusive operator.
    operators={'above':'gt','greater than':'gt','over':'gt','>':'gt','at least':'ge','>=':'ge',
               'below':'lt','less than':'lt','under':'lt','<':'lt','at most':'le','<=':'le','=':'eq','==':'eq','!=':'ne'}
    consumed=[]
    for alias,field in sorted(aliases.items(),key=lambda p:-len(p[0])):
        pattern=r'\b'+re.escape(alias)+r'\s*(>=|<=|!=|==|>|<|=|greater than|less than|at least|at most|above|below|over|under)\s*(-?\d+(?:\.\d+)?)\b'
        for match in re.finditer(pattern,q):
            if any(match.start()<end and match.end()>start for start,end in consumed): continue
            consumed.append(match.span())
            predicates.append(Predicate(operators[match[1]],field,Literal('number',match[2])))
    if source=='taxi':
        sign=re.search(r'\b(?:(excluding|except|without|ignoring)\s+)?(negative|positive|zero|nonnegative|non-negative)\s+fares?\b',q)
        if sign:
            operator={'negative':'lt','positive':'gt','zero':'eq','nonnegative':'ge','non-negative':'ge'}[sign[2]]
            if sign[1]: operator={'lt':'ge','gt':'le','eq':'ne','ge':'lt'}[operator]
            predicates.append(Predicate(operator,'trips.fare_amount',Literal('number','0')))
    if len(predicates)>1 and (re.search(r'\bor\b',q) or '(' in q or ')' in q):
        unresolved.append('complex_boolean_scope_requires_explicit_annotation')
    if predicates:
        tree=predicates[0] if len(predicates)==1 else Predicate('and',children=tuple(predicates))
        facts.append(Constraint('predicate','where','where',predicate=tree.canonical()))
    if re.search(r'\b(by|per|each)\s+(?:billing\s+)?countr\w*\b',q):
        facts.append(Constraint('grouping','Invoice.BillingCountry','key'))
    if re.search(r'\b(by|per|each)\s+genres?\b',q):
        facts.append(Constraint('grouping','Genre.Name','key'))
        if source=='chinook' and scope=='Track': facts.append(Constraint('join','Track.GenreId','inner','Genre.GenreId'))
    if re.search(r'\b(distinct|unique)\b',q): facts.append(Constraint('distinct',scope,'enabled',True))
    limit=re.search(r'\b(?:top|bottom|limit|first)\s+(\d+)\b',q)
    if limit: facts.append(Constraint('limit',scope,'max_rows',int(limit[1])))
    if re.search(r'\b(before|after|since|until|during|yesterday|today)\b',q):
        unresolved.append('temporal_field_or_interval_not_canonicalized')
    if re.search(r'\b(order by|sorted|ascending|descending|highest|lowest|top|bottom)\b',q):
        unresolved.append('ordering_not_fully_canonicalized')
    if re.search(r'\b(percentile|having)\b',q): unresolved.append('unsupported_quantile_or_postaggregation_filter')
    if source in ('synthetic','ldbc') and re.search(r'\b(path|connection|chain|friends?|reports|reporting|downstream)\b',q):
        if source=='synthetic':
            names={n.lower():n for n in catalog.domains['employee']}
            endpoints=[names[m[0]] for m in re.finditer(r'\b(?:'+'|'.join(re.escape(n) for n in names)+r')\b',q)]
        else: endpoints=re.findall(r'\b\d+\b',q)
        path=bool(re.search(r'\b(path|connection|chain)\b',q))
        needed=2 if path else 1
        if len(endpoints)!=needed: unresolved.append('relationship_endpoints_ambiguous')
        else:
            facts.append(Constraint('traversal',scope,'source',endpoints[0]))
            if path: facts.append(Constraint('traversal',scope,'target',endpoints[1]))
            facts.append(Constraint('traversal',scope,'strategy','shortest_undirected' if path else 'direct_undirected' if source=='ldbc' else 'all_downstream_directed'))
    return Extraction(CanonicalRequest(source,tuple(facts)),tuple(dict.fromkeys(c.kind for c in facts)),tuple(dict.fromkeys(unresolved)))


def validate_extracted(extraction,implemented):
    # Only compare categories recognized by the bounded extractor; other categories
    # are explicitly outside its coverage, not asserted absent.
    kinds=set(extraction.recognized_kinds)
    # Once a row scope is recognized, an unrequested filter/group/limit/distinct
    # must not be invented by the plan.
    if 'scope' in kinds: kinds.update(('predicate','grouping','limit','distinct'))
    actual=CanonicalRequest(implemented.source,tuple(c for c in implemented.constraints if c.kind in kinds))
    result=validate_preservation(extraction.request,actual)
    result['unresolved']=list(extraction.unresolved)
    result['preserved']=result['preserved'] and not extraction.unresolved
    result['coverage']='partial deterministic extraction; no complete-language guarantee'
    return result
