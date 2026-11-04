"""Author constraint-label families without importing or running an extractor.

Create a new output version; never overwrite existing labels or review work.
"""
import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from polyquery.canonical import CanonicalRequest, Constraint as C, Predicate as P, Literal as L


def leaf(field,op,value,kind='number'): return P(op,field,L(kind,value))
def AND(*children): return P('and',children=children)
def OR(*children): return P('or',children=children)
def NOT(child): return P('not',children=(child,))
def request(source,scope,aggregate=None,predicate=None,extras=()):
    facts=[C('scope',scope,'rows')]
    if aggregate: facts.append(C('aggregation',aggregate[1],aggregate[0]))
    if predicate: facts.append(C('predicate','where','where',predicate=predicate))
    facts.extend(extras)
    return CanonicalRequest(source,tuple(facts)).to_dict()


def build(output):
    if output.exists(): raise ValueError('Output exists; use a new version to preserve labels and reviews')
    families=[]
    def add(name,source,phenomenon,group,questions,gold,notes,*,status='canonical',context=None):
        assert len(questions)==4
        families.append({'family_id':name,'source':source,'phenomenon':phenomenon,'leakage_group':group,
                         'questions':questions,'expected':gold,'label_status':status,'annotation_notes':notes,
                         'context':deepcopy(context or {}),'review':{'consistency_check':'pending','independent_review':'pending','reviewer':None}})
    # Negation and boundary contrasts.
    for name,word,op in [('negative','negative','ge'),('zero','zero','ne'),('positive','positive','le')]:
        add('fare-exclude-'+name,'taxi','negation','fare-sign-exclusion',[
            f'Average fare excluding {word} fares',f'Calculate the mean fare after removing {word} fares.',
            f'Ignore {word} fare amounts when calculating the average fare.',f'What is the average fare among fares that are not {word}?'],
            request('taxi','trips',('mean','trips.fare_amount'),leaf('trips.fare_amount',op,0)),
            {'negative':'Keep fares >= 0: zero and positive amounts qualify; negative and null amounts do not.',
             'zero':'Keep fares != 0: negative and positive amounts qualify; zero and null amounts do not.',
             'positive':'Keep fares <= 0: negative amounts and zero qualify; positive and null amounts do not.'}[name])
    add('department-not-sales','synthetic','negation','department-membership',[
        'Average salary excluding Sales','Mean salary for employees not in Sales.',
        'What is the mean employee salary outside the Sales department?','Compute average salary after omitting Sales employees.'],
        request('synthetic','employees',('mean','employees.salary'),leaf('employees.department','ne','Sales','string')),
        'Sales is a department value; exclude exactly that category, not all sales records.')
    a=leaf('employees.salary','gt',100000); b=leaf('employees.experience','lt',4)
    add('not-both','synthetic','negation','boolean-negation-scope',[
        'Average salary excluding employees who satisfy both salary > 100000 and experience < 4.',
        'Mean salary where NOT (salary > 100000 AND experience < 4).',
        'For the salary average, omit people only when salary exceeds 100000 and experience is below 4 together.',
        'Average salary for employees for whom the conjunction of salary > 100000 and experience < 4 is false.'],
        request('synthetic','employees',('mean','employees.salary'),NOT(AND(a,b))),
        'NOT applies to the entire AND. It is not NOT(A) AND NOT(B).')
    add('not-either','synthetic','negation','boolean-negation-scope',[
        'Average salary excluding employees who have salary > 100000 or experience < 4.',
        'Mean salary where NOT (salary > 100000 OR experience < 4).',
        'For average salary, retain employees satisfying neither salary > 100000 nor experience < 4.',
        'Omit either high-salary employees above 100000 or low-experience employees below 4 before averaging salary.'],
        request('synthetic','employees',('mean','employees.salary'),NOT(OR(a,b))),
        'NOT applies to the entire OR. Both exclusion conditions must be absent.')
    # Numeric and traversal boundaries, grouped across minimal contrasts.
    for name,op,phrases in [
        ('strict','gt',['salary above 100000','salary greater than 100000','salary > 100000','salary strictly exceeding 100000']),
        ('inclusive','ge',['salary at least 100000','salary >= 100000','salary no less than 100000','salary 100000 or higher'])]:
        add('salary-'+name,'synthetic','numeric_boundary','salary-boundary',
            [f'Average salary for employees with {phrase}.' for phrase in phrases],
            request('synthetic','employees',('mean','employees.salary'),leaf('employees.salary',op,100000)),
            'The boundary is strict for above and inclusive for at least; 100000 is the contrast witness.')
    add('fare-bounded-interval','taxi','numeric_boundary','fare-interval',[
        'Average fare for 0 <= fare_amount < 20.', 'Mean fare for amounts at least zero and below 20.',
        'Average fare in the half-open interval [0, 20).','Calculate mean fare including zero but excluding 20 and all fares outside those bounds.'],
        request('taxi','trips',('mean','trips.fare_amount'),AND(leaf('trips.fare_amount','ge',0),leaf('trips.fare_amount','lt',20))),
        'Half-open interval: zero included, 20 excluded. No unit conversion.')
    path=(C('traversal','person','source','933'),C('traversal','person','target','1129'),C('traversal','person','strategy','shortest_undirected'))
    add('path-hop-bound','ldbc','numeric_boundary','ldbc-path-bounds',[
        'Shortest undirected friendship path from 933 to 1129 using at most 3 edges.',
        'Find a shortest friendship chain between 933 and 1129 with no more than three hops.',
        'Connect person 933 to person 1129 by a shortest undirected path of length <= 3.',
        'Return a minimum-hop undirected path from 933 to 1129, but only if its edge count is at most 3.'],
        request('ldbc','person',extras=path+(C('traversal','person','max_hops',3),)),
        'The bound counts edges, not vertices. A path with four vertices can qualify. IDs remain strings.')
    # Boolean precedence and parenthesization.
    c=leaf('employees.department','eq','Research','string')
    trees=[('and-around-or',AND(c,OR(a,b)),[
        'Average salary where department is Research AND (salary > 100000 OR experience < 4).',
        'Mean salary for Research employees who either earn more than 100000 or have less than four years of experience.',
        'Within Research, average salaries of employees meeting at least one of salary > 100000 and experience < 4.',
        'Average salary after filtering to Research and then requiring salary > 100000 or experience < 4.']),
       ('or-around-and',OR(AND(c,a),b),[
        'Average salary where (department is Research AND salary > 100000) OR experience < 4.',
        'Mean salary for either Research employees earning above 100000 or any employees with experience below 4.',
        'Average salaries for the union of high-paid Research employees above 100000 and all employees with less than four years experience.',
        'Average salary after applying (Research AND salary > 100000) OR (experience < 4).']),
       ('numeric-and',AND(a,b),[
        'Average salary where salary > 100000 AND experience < 4.','Mean salary for employees earning above 100000 with experience below four.',
        'Average salaries only when both salary exceeds 100000 and experience is less than 4.','Require salary > 100000 together with experience < 4 before calculating mean salary.']),
       ('numeric-or',OR(a,b),[
        'Average salary where salary > 100000 OR experience < 4.','Mean salary for employees earning above 100000 or having experience below four.',
        'Average salaries when either salary exceeds 100000 or experience is less than 4, including employees satisfying both.',
        'Use the union of salary > 100000 and experience < 4 when calculating average salary.']),
       ('not-inside-or',OR(NOT(a),b),[
        'Average salary where NOT (salary > 100000) OR experience < 4.','Mean salary when salary is not above 100000 or experience is below four.',
        'Average salary for the union of salary <= 100000 and experience < 4.','Keep employees who fail salary > 100000 or satisfy experience < 4, then average salary.']),
       ('categorical-or',OR(leaf('employees.department','eq','Sales','string'),leaf('employees.department','eq','Engineering','string')),[
        'Average salary in Sales or Engineering.','Mean salary for employees belonging to either Sales or Engineering.',
        'Combine Engineering and Sales employees and calculate their mean salary.','Average salaries across the union of the Sales and Engineering departments.'])]
    for name,tree,questions in trees:
        add(name,'synthetic','boolean_scope','boolean-precedence' if name in ('and-around-or','or-around-and') else 'boolean-numeric-combinations' if name in ('numeric-and','numeric-or','not-inside-or') else 'department-membership',
            questions,request('synthetic','employees',('mean','employees.salary'),tree),
            'Parentheses and quantifier scope are intentional. No implicit precedence is assumed beyond the stated wording.')
    # Absolute dates use explicit local/raw wall-clock midnight boundaries.
    time_context={'calendar':'ISO Gregorian','date_literal_boundary':'00:00:00 as stored','timezone_conversion':'none','interval':'explicit; do not infer from file month'}
    for name,op,bound,questions in [
        ('pickup-before','lt','2026-02-01',[
            'Count trips with pickup timestamp before 2026-02-01 at 00:00 as stored.',
            'How many pickups occurred earlier than midnight at the start of 2026-02-01, without timezone conversion?',
            'Count trips satisfying tpep_pickup_datetime < 2026-02-01 00:00 in stored wall-clock time.',
            'Return the number of trips picked up strictly before the start of February 1, 2026, using the timestamps as stored.']),
        ('pickup-on-or-after','ge','2026-02-01',[
            'Count trips with pickup timestamp at or after 2026-02-01 00:00 as stored.',
            'How many pickups are no earlier than midnight starting February 1, 2026, with no timezone conversion?',
            'Count trips satisfying tpep_pickup_datetime >= 2026-02-01 00:00 in stored wall-clock time.',
            'Include trips picked up at midnight at the start of 2026-02-01 and all later pickups; report their count.'])]:
        add(name,'taxi','dates','pickup-date-boundary',questions,
            request('taxi','trips',('count','trips.*'),leaf('trips.tpep_pickup_datetime',op,bound,'date')),
            'The timestamp comparison uses the start of the stated date, not the whole end date. Pickup, not dropoff.',context=time_context)
    add('pickup-month-interval','taxi','dates','pickup-date-interval',[
        'Count trips picked up from 2026-01-01 00:00 inclusive to 2026-02-01 00:00 exclusive, as stored.',
        'Count pickups in [2026-01-01 00:00, 2026-02-01 00:00) with no timezone conversion.',
        'How many trips have pickup timestamps at least January 1, 2026 at midnight and before February 1 at midnight?',
        'Use stored pickup time to count trips starting on or after 2026-01-01 00:00 and strictly before 2026-02-01 00:00.'],
        request('taxi','trips',('count','trips.*'),AND(leaf('trips.tpep_pickup_datetime','ge','2026-01-01','date'),leaf('trips.tpep_pickup_datetime','lt','2026-02-01','date'))),
        'A half-open calendar interval avoids double-counting an adjacent month boundary.',context=time_context)
    add('invoice-before','chinook','dates','invoice-date-boundary',[
        'Sum invoice totals for InvoiceDate before 2010-01-01 00:00, using stored time.',
        'Total invoice revenue strictly before midnight at the start of January 1, 2010, without timezone conversion.',
        'Calculate SUM(Total) for invoices satisfying InvoiceDate < 2010-01-01 00:00 as stored.',
        'Add invoice totals dated earlier than the start of 2010 in the database wall-clock timestamps.'],
        request('chinook','Invoice',('sum','Invoice.Total'),leaf('Invoice.InvoiceDate','lt','2010-01-01','date')),
        'Filter on Invoice.InvoiceDate strictly before the stated start-of-day boundary, then sum Invoice.Total over qualifying rows.',context=time_context)
    add('relative-date-unanchored','taxi','dates','relative-date-ambiguity',[
        'Count trips picked up last month.','How many pickups happened yesterday?',
        'Count recent pickups.','How many trips were picked up during the previous week?'],None,
        'No reference clock, timezone, or definition of recent is supplied; ask for clarification.',status='clarify')
    # Grouping, row filters, and post-aggregation filtering are different stages.
    country=leaf('Invoice.BillingCountry','eq','USA','string')
    add('country-group','chinook','grouping_vs_filtering','country-group-filter',[
        'Total invoice revenue by billing country.','Sum invoice totals separately for each billing country.',
        'Group invoices on BillingCountry and return the sum of Total in each group.','Break invoice revenue down by billing country.'],
        request('chinook','Invoice',('sum','Invoice.Total'),extras=(C('grouping','Invoice.BillingCountry','key'),)),
        'One aggregate per country; this is not a filter to a single country.')
    add('country-filter','chinook','grouping_vs_filtering','country-group-filter',[
        'Total invoice revenue where billing country is USA.','Sum Total only for invoices billed in USA.',
        'For BillingCountry = USA, report one overall invoice revenue total.','Restrict invoices to billing country USA, then sum their totals without grouping.'],
        request('chinook','Invoice',('sum','Invoice.Total'),country),
        'One country is selected before aggregation; no GROUP BY is requested.')
    add('country-group-and-filter','chinook','grouping_vs_filtering','country-group-filter',[
        'Sum invoice totals by billing country, excluding invoices billed in USA.',
        'Remove USA-billed invoices and then total revenue for each remaining billing country.',
        'Return SUM(Total) grouped by BillingCountry with the row filter BillingCountry != USA.',
        'Group non-USA invoices by billing country and report revenue for every resulting group.'],
        request('chinook','Invoice',('sum','Invoice.Total'),leaf('Invoice.BillingCountry','ne','USA','string'),(C('grouping','Invoice.BillingCountry','key'),)),
        'The exclusion is a row predicate; country is also a grouping key.')
    add('invoice-row-filter-before-group','chinook','grouping_vs_filtering','group-filter-stage',[
        'Sum invoice totals by billing country using only invoices whose Total exceeds 5.',
        'Discard invoice rows with Total <= 5, then group by billing country and sum the remaining totals.',
        'Report country revenue with WHERE Total > 5 before grouping.',
        'For each billing country, add only individual invoice totals above 5.'],
        request('chinook','Invoice',('sum','Invoice.Total'),leaf('Invoice.Total','gt',5),(C('grouping','Invoice.BillingCountry','key'),)),
        'Apply Total > 5 to individual rows, not to the country sum.')
    add('country-having','chinook','grouping_vs_filtering','group-filter-stage',[
        'Sum invoice totals by billing country and keep groups whose revenue exceeds 100.',
        'Return billing-country sums with HAVING SUM(Total) > 100.',
        'Calculate total revenue per billing country, then discard country totals at or below 100.',
        'Group all invoices by billing country; show only groups with summed Total above 100.'],None,
        'Unambiguous HAVING, not WHERE. Canonical v1 lacks post-aggregation predicate scope; do not encode it as a row predicate.',
        status='representation_gap',context={'intended_semantics':{'aggregate':'SUM(Invoice.Total)','group_by':'Invoice.BillingCountry','post_aggregation_predicate':{'operator':'gt','value':100}}})
    # Ordered keys and limits, including explicit null placement.
    ordering_context={'null_policy':'Exclude null ordering values when explicitly stated; otherwise null placement is unresolved.'}
    for name,field,direction,questions in [
        ('fare-descending','trips.fare_amount','desc',[
            'List fare_amount values from non-null fares ordered descending.', 'Return non-null fare amounts from largest to smallest.',
            'Sort non-null fare_amount values in descending order and list them.','Show non-null fares ordered by fare_amount DESC.']),
        ('distance-ascending','trips.trip_distance','asc',[
            'List non-null trip distances ordered ascending.','Return non-null trip_distance values from shortest to longest.',
            'Sort non-null trip distances from smallest to largest.','Show non-null trip_distance values ordered ASC.'])]:
        add(name,'taxi','ordering_limits','taxi-ordering',questions,
            request('taxi','trips',predicate=P('is_not_null',field),extras=(C('projection','trips','fields',field.split('.')[1]),C('ordering',field,direction))),
            'The selected field and direction are explicit; null ordering does not need an implicit default.',context=ordering_context)
    add('fare-top-five','taxi','ordering_limits','taxi-ordering',[
        'Return the five largest non-null fare_amount values, in descending order.',
        'List non-null fare_amount values ORDER BY fare_amount DESC LIMIT 5.',
        'Sort non-null fares largest first and keep the first five values.',
        'Show at most 5 non-null fare amounts, ordered from largest to smallest.'],
        request('taxi','trips',predicate=P('is_not_null','trips.fare_amount'),extras=(C('projection','trips','fields','fare_amount'),C('ordering','trips.fare_amount','desc'),C('limit','trips','max_rows',5))),
        'Five is a row limit, not a predicate fare_amount >= 5. Repeated amounts are retained.',context=ordering_context)
    add('employee-two-key-order','synthetic','ordering_limits','employee-ordering',[
        'List employees by salary descending, breaking ties by name ascending.',
        'Return employee rows ORDER BY salary DESC, name ASC.',
        'Sort employees from highest salary to lowest, then by case-sensitive Unicode code-point name order within salary ties.',
        'Order employee rows on descending salary followed by ascending name.'],
        request('synthetic','employees',extras=(C('projection','employees','fields','name,department,salary,experience'),C('ordering','employees.salary','desc'),C('ordering','employees.name','asc'))),
        'The two ordering keys are ordered. Reversing them changes the request.')
    add('employee-limit-only','synthetic','ordering_limits','employee-limit',[
        'List at most three employees; any order is acceptable.','Return up to 3 employee rows with no ordering requirement.',
        'Give me a maximum of three employee records, without ranking them.','List employee records with LIMIT 3; do not impose an order.'],
        request('synthetic','employees',extras=(C('projection','employees','fields','name,department,salary,experience'),C('limit','employees','max_rows',3))),
        'An unordered limit must not invent a salary/name ranking. Different valid subsets can be acceptable.')
    add('ldbc-friends-ordered','ldbc','ordering_limits','ldbc-neighbor-order',[
        'List friend IDs of person 933 in ascending numeric ID order, at most five IDs.',
        'Return up to 5 direct undirected friends of 933, sorted by numeric person ID ascending.',
        'For person 933, list the five smallest numeric IDs among direct friends.',
        'List direct friend IDs of 933 ORDER BY numeric ID ASC LIMIT 5.'],
        request('ldbc','person',extras=(C('traversal','person','source','933'),C('traversal','person','strategy','direct_undirected'),C('projection','person','fields','id'),C('ordering','person.id','asc'),C('limit','person','max_rows',5))),
        'IDs are stored as strings but ordered numerically. No friendship-path traversal is requested.',context={'person_id_order':'numeric, arbitrary precision'})
    ambiguous=[
      ('money-field','taxi','ambiguous_field',[
          'What is the average amount?','Calculate the mean charge.','What is the typical payment amount?','Average the money values.'],
          'Specify fare_amount versus total_amount or another monetary column.'),
      ('timestamp-field','taxi','ambiguous_field',[
          'Count trips before 2026-02-01.','How many trips happened on January 15, 2026?',
          'Filter trips after midnight on February 1, 2026.','Count trips dated before 2026-01-15.'],
          'Specify pickup versus dropoff timestamp, boundary semantics, and time basis.'),
      ('mean-or-median','synthetic','ambiguous_statistic',[
          'What is a typical salary?','Give the normal employee pay.','What salary is representative?','Find the usual salary.'],
          'Typical/normal/usual does not uniquely choose mean or median.'),
      ('employee-attribute','synthetic','ambiguous_field',[
          'Show the highest employees.','Rank employees by performance.','Who are the best employees?','List the top employees.'],
          'Ranking field, direction, and often limit are unspecified; performance is not a fixture field.'),
      ('invoice-country','chinook','ambiguous_field',[
          'Revenue by country.','Show country totals.','Group sales geographically by country.','Give total revenue for each country.'],
          'Under this annotation protocol, country must specify Invoice.BillingCountry versus Customer.Country; do not assume equality.'),
      ('ldbc-endpoints','ldbc','ambiguous_identity',[
          'Find a shortest path between them.','How are those two people connected?','Show the friendship chain for the pair.','Find their shortest connection.'],
          'No antecedents/entity IDs are supplied in this single-turn task.'),
      ('ldbc-name-identity','ldbc','ambiguous_identity',[
          'List friends of Aaron.','Who knows Aaron?','Show the neighbors of Aaron.','Find friends of Aaron.'],
          'The supplied people file contains two first-name Aaron matches. A first name does not select one person ID; request disambiguation.'),
      ('boolean-no-parentheses','synthetic','ambiguous_scope',[
          'Average salary for Research and Sales employees with experience below 4.',
          'Mean salary for Sales or Engineering employees earning above 100000.',
          'Average salaries of Research employees or Sales employees with salary above 100000.',
          'Mean salary for Engineering or Research employees with experience over 5.'],
          'Modifier attachment can have more than one reasonable reading. Request parentheses or explicit scope under the conservative annotation protocol.')]
    for name,source,phenomenon,questions,notes in ambiguous:
        add('ambiguous-'+name,source,phenomenon,'ambiguity-'+name,questions,None,notes,status='clarify')
    if len(families)!=40: raise AssertionError(len(families))
    by_name={family['family_id']:family for family in families}
    rationales={
        'and-around-or':'Department must be Research, and at least one numeric condition must be TRUE. A qualifying numeric condition alone cannot admit a non-Research employee.',
        'or-around-and':'The Research restriction applies only to the salary > 100000 branch. Any employee satisfying experience < 4 qualifies through the other branch.',
        'numeric-and':'Both salary > 100000 and experience < 4 must be TRUE for a row to qualify.',
        'numeric-or':'Either salary > 100000 or experience < 4 can admit a row; satisfying both does not duplicate the row.',
        'not-inside-or':'NOT applies only to the salary > 100000 comparison. Its result is OR-ed with experience < 4; NOT does not cover the OR expression.',
        'categorical-or':'Keep the union of Sales and Engineering employees. Research employees are excluded; no employee is counted twice.',
        'department-not-sales':'Keep rows where department != Sales is TRUE. Null departments yield UNKNOWN and are dropped under the explicit SQL null policy.',
        'not-both':'Negate the entire conjunction. A row qualifies only when NOT(A AND B) is TRUE; UNKNOWN is dropped, so exclusion is not a keep-null anti-filter.',
        'not-either':'Negate the entire disjunction. A row qualifies only when NOT(A OR B) is TRUE; UNKNOWN is dropped, rather than treating an unknown condition as false.',
    }
    for name,rationale in rationales.items(): by_name[name]['annotation_notes']=rationale
    null_policy={'logic':'sql_three_valued','row_retention':'only_TRUE','null_comparison':'UNKNOWN except explicit IS NULL/IS NOT NULL',
                 'negation':'NOT UNKNOWN is UNKNOWN; do not reinterpret exclusion as retaining unknown rows',
                 'aggregate_inputs':'Ignore null numeric inputs after filtering; COUNT rows includes every retained row',
                 'empty_numeric_aggregate':'null, except COUNT returns 0'}
    for family in families:
        if family['expected'] and any(c['kind']=='predicate' for c in family['expected']['constraints']):
            family['context']['null_policy']=deepcopy(null_policy)
        if family['phenomenon']=='grouping_vs_filtering':
            family['context']['group_policy']={'stage_order':['row_filter','group','aggregate','having_if_requested'],
                'empty_groups':'omit; never manufacture zero rows for groups removed by a row filter',
                'all_null_values_in_existing_group':'SUM is null, not 0', 'null_group_key':'one SQL NULL group if qualifying rows have a null key'}
            family['context']['null_policy']=deepcopy(null_policy)
    # Ground employee projections in every formulation, not just an implicit default.
    for name in ('employee-two-key-order','employee-limit-only'):
        family=by_name[name]
        family['questions']=[q.rstrip('.')+'; return exactly name, department, salary, and experience.' for q in family['questions']]
        family['context']['projection']={'fields':['name','department','salary','experience'],
            'fixture_columns':['name','department','salary','experience'],'extra_fields':'reject'}
    family=by_name['employee-two-key-order']
    family['context']['collation']={'field':'employees.name','order':'Unicode code-point','case_sensitive':True,'normalization':'none','locale':'none'}
    family['context']['intended_semantics']=family['expected']
    family['expected']=None; family['label_status']='representation_gap'
    family['annotation_notes']='Salary descending is the primary key, name ascending is the tie-breaker under the stated case-sensitive code-point rule. Canonical v1 cannot encode collation, so full encoding is deferred consistently with numeric person-ID ordering.'
    by_name['path-hop-bound']['context']['answer_policy']={'scorer':'bounded_shortest_path','ties':'any valid globally shortest path',
        'no_path_within_bound':'successful result value null; abstention/error is not a correct null result','hop_unit':'edges','max_hops':3,
        'source':'933','target':'1129','direction':'undirected'}
    by_name['path-hop-bound']['annotation_notes']='At most three edges, not three vertices. Accept any tied shortest path. Return null when the global shortest path exceeds three edges or the endpoints are disconnected.'
    by_name['employee-limit-only']['questions']=[
        'List three employees if available, otherwise all employees; any subset and order is acceptable; return exactly name, department, salary, and experience.',
        'Return 3 employee rows when at least 3 exist, otherwise every employee, with no ordering requirement; project exactly name, department, salary, and experience.',
        'Give any three distinct employees, or all if fewer than three exist, without ranking; include exactly name, department, salary, and experience.',
        'List employee records with LIMIT 3, filling the limit when possible; any order is acceptable; return exactly name, department, salary, and experience.']
    by_name['employee-limit-only']['context']['answer_policy']={'scorer':'unordered_valid_subset','identity':'name','limit':3,
        'cardinality':'min(limit, eligible_row_count)','duplicates':'reject','membership':'full projected row must match an eligible row','ordering':'ignored'}
    by_name['employee-limit-only']['annotation_notes']='Return any three distinct eligible employee rows, or all rows if fewer than three exist. Score membership and cardinality, not equality with one arbitrary reference subset or row order.'
    # Numeric ordering of string IDs needs collation/coercion metadata absent in
    # canonical v1. Preserve the intended meaning without claiming full encoding.
    family=next(f for f in families if f['family_id']=='ldbc-friends-ordered')
    family['context']['intended_semantics']=family['expected']
    family['expected']=None
    family['label_status']='representation_gap'
    family['annotation_notes']+=' Canonical v1 has no ordering collation/coercion field, so a full numeric-ID order label is deferred.'
    null_tags={
        'relative-date-unanchored':['scope','aggregation','predicate'],
        'country-having':['scope','aggregation','grouping','predicate'],
        'ldbc-friends-ordered':['scope','traversal','projection','ordering','limit'],
        'employee-two-key-order':['scope','projection','ordering'],
        'ambiguous-money-field':['scope','aggregation'], 'ambiguous-timestamp-field':['scope','predicate'],
        'ambiguous-mean-or-median':['scope','aggregation'], 'ambiguous-employee-attribute':['scope','ordering'],
        'ambiguous-invoice-country':['scope','aggregation','grouping'], 'ambiguous-ldbc-endpoints':['scope','traversal'],
        'ambiguous-ldbc-name-identity':['scope','traversal'], 'ambiguous-boolean-no-parentheses':['scope','aggregation','predicate'],
    }
    witness_specs={
        'fare-exclude-negative':('trips.fare_amount',[(-1,False),(0,True),(1,True),(None,None)]),
        'fare-exclude-zero':('trips.fare_amount',[(-1,True),(0,False),(1,True),(None,None)]),
        'fare-exclude-positive':('trips.fare_amount',[(-1,True),(0,True),(1,False),(None,None)]),
        'salary-strict':('employees.salary',[(99999,False),(100000,False),(100001,True),(None,None)]),
        'salary-inclusive':('employees.salary',[(99999,False),(100000,True),(100001,True),(None,None)]),
        'pickup-before':('trips.tpep_pickup_datetime',[('2026-01-31',True),('2026-02-01',False),('2026-02-02',False)]),
        'pickup-on-or-after':('trips.tpep_pickup_datetime',[('2026-01-31',False),('2026-02-01',True),('2026-02-02',True)]),
        'pickup-month-interval':('trips.tpep_pickup_datetime',[('2025-12-31',False),('2026-01-01',True),('2026-01-31',True),('2026-02-01',False)]),
    }
    for family in families:
        family['expected_tags']=sorted({c['kind'] for c in family['expected']['constraints']}) if family['expected'] else null_tags[family['family_id']]
        if family['family_id'] in witness_specs:
            field,examples=witness_specs[family['family_id']]
            family['witnesses']=[{'row':{field:value},'expected_truth':truth} for value,truth in examples]
        if family['family_id'] in ('and-around-or','or-around-and'):
            family['witnesses']=[{'row':{'employees.department':'Sales','employees.salary':90000,'employees.experience':3},'expected_truth':family['family_id']=='or-around-and'}]
        if family['family_id'] in ('not-both','not-either'):
            family['witnesses']=[{'row':{'employees.salary':120000,'employees.experience':6},'expected_truth':family['family_id']=='not-both'},
                                 {'row':{'employees.salary':None,'employees.experience':6},'expected_truth':True if family['family_id']=='not-both' else None}]
            family['witnesses'].extend([
                {'row':{'employees.salary':120000,'employees.experience':None},'expected_truth':None if family['family_id']=='not-both' else False},
                {'row':{'employees.salary':90000,'employees.experience':None},'expected_truth':True if family['family_id']=='not-both' else None}])
        if family['family_id']=='not-inside-or':
            family['witnesses']=[{'row':{'employees.salary':120000,'employees.experience':None},'expected_truth':None},
                                {'row':{'employees.salary':90000,'employees.experience':None},'expected_truth':True}]
        if family['family_id']=='department-not-sales':
            family['witnesses']=[{'row':{'employees.department':None},'expected_truth':None},
                                {'row':{'employees.department':'Sales'},'expected_truth':False},
                                {'row':{'employees.department':'Research'},'expected_truth':True}]
    cases=[]
    for family in families:
        for i,q in enumerate(family['questions']):
            case={k:v for k,v in family.items() if k!='questions'}
            case.update(id=family['family_id']+f'-p{i+1}',question=q)
            cases.append(case)
    metadata={'version':2,'split':'draft_pool','annotation_status':'draft; consistency and independent human review pending',
              'labels_built_from_extractor':False,'paraphrase_count_is_not_independent_sample_count':True,
              'null_semantics':'SQL three-valued row filtering; only TRUE qualifies; NOT UNKNOWN stays UNKNOWN',
              'date_semantics':'Explicit literal date denotes midnight at its start in stored wall-clock time; no timezone inference',
              'ambiguous_policy':'Do not invent identity, field, aggregate, temporal anchor, or Boolean attachment',
              'source_projection_note':'Gold describes requested meaning, not adapter execution support; some fields exist only in raw files.'}
    payload={'metadata':metadata,'cases':cases}
    output.mkdir(parents=True)
    (output/'labels.json').write_text(json.dumps(payload,indent=2)+'\n',encoding='utf-8')
    groups=sorted({c['leakage_group'] for c in cases})
    # Deterministic, size-aware group assignment; no model scores are consulted.
    targets={'train':len(cases)*.6,'validation':len(cases)*.2,'test':len(cases)*.2}
    counts={name:0 for name in targets}; assignments={}
    ordered=sorted(groups,key=lambda group:(-sum(c['leakage_group']==group for c in cases),hashlib.sha256(('constraints-v2-draft/'+group).encode()).hexdigest()))
    for group in ordered:
        size=sum(c['leakage_group']==group for c in cases)
        def score(split):
            return sum((counts[name]+(size if name==split else 0)-target)**2/target for name,target in targets.items())
        chosen=min(targets,key=lambda name:(score(name),name))
        assignments[group]=chosen; counts[chosen]+=size
    split={'status':'draft; no untouched-test or human-review claim','assignment_unit':'leakage_group, including linked minimal contrasts',
           'group_assignments':assignments,'case_assignments':{c['id']:assignments[c['leakage_group']] for c in cases}}
    (output/'splits.json').write_text(json.dumps(split,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'cases':len(cases),'families':len(families),'leakage_groups':len(groups)},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,default=Path('benchmarks/constraint-labels-v2'))
    args=parser.parse_args(); build(args.output)
