"""Source-scoped operation, parameter, identity, and result contracts."""
import math
from dataclasses import asdict, dataclass, field

from .backends import UnsupportedPlan
from .planner import Plan


@dataclass(frozen=True)
class ParameterSpec:
    kind: str = 'string'
    required: bool = True
    nullable: bool = False
    domain: str | None = None


@dataclass(frozen=True)
class ResultSpec:
    kind: str
    nullable: bool = False
    fields: dict = field(default_factory=dict)
    entity: str | None = None
    semantics: str = ''


@dataclass(frozen=True)
class OperationSpec:
    backends: tuple[str, ...]
    result: ResultSpec
    parameters: dict[str, ParameterSpec] = field(default_factory=dict)
    description: str = ''


class Catalog:
    version = 1

    def __init__(self, source, schema, operations, domains, costs=None, adjacency=None):
        self.source, self.schema, self.operations = source, schema, operations
        self.domains, self.costs = domains, costs or {}
        self.adjacency = adjacency or {}

    def capable_backends(self, operation):
        spec = self.operations.get(operation)
        return spec.backends if spec else ()

    def validate_plan(self, plan):
        if not isinstance(plan, Plan):
            raise UnsupportedPlan('Planner must return a Plan; use Plan.from_dict for serialized plans')
        if not isinstance(plan.backend, str) or not isinstance(plan.operation, str) or not isinstance(plan.parameters, dict):
            raise UnsupportedPlan('Invalid plan field types')
        if type(plan.reason) is not str or not isinstance(plan.scores, dict) or any(type(k) is not str or type(v) not in (int,float) or not math.isfinite(v) for k,v in plan.scores.items()):
            raise UnsupportedPlan('Invalid reason or feature score types')
        if plan.backend == 'abstain':
            if plan.operation != 'unsupported' or plan.parameters:
                raise UnsupportedPlan('Abstention must use unsupported with no parameters')
            return
        spec = self.operations.get(plan.operation)
        if spec is None or plan.backend not in spec.backends:
            raise UnsupportedPlan(f'Unsupported source/backend operation: {self.source}/{plan.backend}/{plan.operation}')
        if set(plan.parameters) - set(spec.parameters):
            raise UnsupportedPlan('Unexpected plan parameters')
        for name, parameter in spec.parameters.items():
            if name not in plan.parameters:
                if parameter.required:
                    raise UnsupportedPlan(f'Missing parameter: {name}')
                continue
            value = plan.parameters[name]
            if value is None and parameter.nullable:
                continue
            if parameter.kind == 'null':
                raise UnsupportedPlan(f'{name} must be null or omitted')
            if type(value) is not str:
                raise UnsupportedPlan(f'{name} must be a string')
            if parameter.domain and value not in self.domains[parameter.domain]:
                raise UnsupportedPlan(f'Unknown {parameter.domain}: {value}')

    def validate_result(self, plan, result):
        if not isinstance(result, dict) or 'value' not in result:
            raise UnsupportedPlan('Backend result requires a value field')
        spec = self.operations[plan.operation].result
        value = result['value']
        def scalar(v, kind, nullable=False):
            if v is None:
                return nullable
            if kind == 'string':
                return type(v) is str
            if kind == 'integer':
                return type(v) is int and v >= 0
            if kind == 'number':
                return type(v) in (int, float) and math.isfinite(v)
            return False
        valid = value is None and spec.nullable
        if value is not None:
            if spec.kind in ('integer', 'number', 'string'):
                valid = scalar(value, spec.kind)
            elif spec.kind in ('path', 'entity_ids'):
                valid = isinstance(value, list) and all(scalar(v, 'string') and v in self.domains[spec.entity] for v in value)
                if spec.kind == 'path':
                    valid = valid and bool(value) and value[0] == plan.parameters['source'] and value[-1] == plan.parameters['target']
                    valid = valid and all(b in self.adjacency.get(a, ()) for a,b in zip(value,value[1:]))
            elif spec.kind == 'rows':
                valid = isinstance(value, list) and all(isinstance(row, dict) and row.keys() == spec.fields.keys() and
                        all(scalar(row[k], kind.removesuffix('?'), kind.endswith('?')) for k, kind in spec.fields.items()) for row in value)
                if valid and spec.entity:
                    key = 'id' if spec.entity == 'person' else 'name'
                    valid = all(row[key] in self.domains[spec.entity] for row in value)
        if not valid:
            raise UnsupportedPlan(f'Backend result violates {self.source}/{plan.operation} result contract')

    def to_dict(self):
        return {'catalog_version': self.version, 'source': self.source, 'schema': self.schema,
                'operations': {name: asdict(spec) for name, spec in self.operations.items()},
                'cost_catalog': self.costs or {'status': 'unmeasured', 'units': 'milliseconds', 'monetary_cost': None},
                'identity_rule': 'Entity identity is scoped by source and entity; IDs never imply cross-source equivalence.'}


def build_catalog(engine):
    from .data import EMPLOYEES
    source, domains, operations, adjacency = engine.source, {}, {}, {}
    number = ResultSpec('number', True, semantics='Null when no valid numeric observations; aggregate nulls ignored.')
    count = ResultSpec('integer', semantics='Nonnegative row/node count.')
    def add(names, backends, result, parameters=None, description=''):
        for name in names:
            operations[name] = OperationSpec(tuple(backends), result, parameters or {}, description)
    if source == 'synthetic':
        from .data import REPORTS
        domains = {'employee': {row['name'] for row in EMPLOYEES}, 'department': {row['department'] for row in EMPLOYEES}}
        department = {'department': ParameterSpec(required=False, nullable=True, domain='department')}
        add(['count'], ['sql'], count, department)
        add(['average_salary', 'revenue'], ['sql'], number, department)
        add(['median', 'stdev'], ['code'], number, department, 'stdev uses sample semantics')
        add(['correlation'], ['code'], number, {'department': ParameterSpec(kind='null', required=False, nullable=True)}, 'Pearson salary/experience correlation; no department filter')
        add(['list'], ['sql'], ResultSpec('rows', fields={'name':'string','department':'string','salary':'number','experience':'number'}), department)
        add(['descendants'], ['graph'], ResultSpec('entity_ids', entity='employee', semantics='All downstream reports; directed edges'), {'source':ParameterSpec(domain='employee')})
        entity = 'employee'
        adjacency = {name:set() for name in domains[entity]}
        for a,b in REPORTS:
            adjacency[a].add(b)
            adjacency[b].add(a)
        schema = {'entities': {'employee': {'key':['name'],'key_type':'string','fields':{'name':'string','department':'string','salary':'number','experience':'number'}},
                               'sales': {'key':None,'fields':{'department':'string','amount':'number'}}},
                  'relationships': [{'name':'reports','source':'employee','target':'employee','directed':True}]}
    elif source == 'ldbc':
        domains = {'person': set(engine.backends['graph'].people)}
        entity = 'person'
        adjacency = engine.backends['graph'].adjacency
        add(['count_people'], ['graph','sql'], count)
        add(['friends'], ['graph','sql'], ResultSpec('rows', fields={'id':'string','first_name':'string','last_name':'string'}, entity='person', semantics='Unique direct friends; numeric ID order'), {'source':ParameterSpec(domain='person')})
        schema = {'entities': {'person': {'key':['id'],'key_type':'string','fields':{'id':'string','first_name':'string','last_name':'string'}}},
                  'relationships':[{'name':'knows','source':'person','target':'person','directed':False,'duplicates':'collapsed'}],
                  'excluded_fields':['birthday','creationDate','gender','locationIP','browserUsed']}
    elif source == 'chinook':
        add(['count_tracks','count_customers','count_invoices'], ['sql','code'], count)
        add(['revenue','mean','median'], ['sql','code'], number)
        add(['revenue_by_country'], ['sql','code'], ResultSpec('rows',fields={'country':'string?','revenue':'number?'},semantics='Billing-country groups; country order'))
        add(['tracks_by_genre'], ['sql','code'], ResultSpec('rows',fields={'genre':'string','tracks':'integer'},semantics='Inner join on GenreId; genre-name order'))
        schema = engine.source_metadata['schema']
    else:
        add(['count_trips','negative_fares'], ['sql','code'], count)
        add(['sum_fare','sum_total','mean_distance','mean_fare','median_fare'], ['sql','code'], number)
        schema = {'entities': {'trip': {'key':None,'identity':'No stable trip ID is provided; row position is not a portable entity key',
                                      'fields':{name:'number?' for name in engine.source_metadata['columns']}}},
                  'available_file_fields':engine.source_metadata['file_schema'],
                  'projection_semantics':engine.source_metadata['projection']}
    if source in ('synthetic','ldbc'):
        backends = ['graph'] if source == 'synthetic' else ['graph','sql']
        add(['shortest_path'], backends, ResultSpec('path',True,entity=entity,semantics='Undirected shortest path; null if disconnected; ties may differ'),
            {'source':ParameterSpec(domain=entity),'target':ParameterSpec(domain=entity)})
    descriptions = {
        'synthetic': {
            'count': 'Count employees.', 'list': 'List employees with name, department, salary and experience.',
            'average_salary': 'Mean employee salary.', 'revenue': 'Sum sales.amount.',
            'median': 'Median employee salary.', 'stdev': 'Sample standard deviation of employee salary.',
        },
        'chinook': {
            'count_tracks': 'Count Track rows.', 'count_customers': 'Count Customer rows.',
            'count_invoices': 'Count Invoice rows.', 'revenue': 'Sum Invoice.Total.',
            'mean': 'Mean Invoice.Total.', 'median': 'Median Invoice.Total.',
            'revenue_by_country': 'Sum Invoice.Total grouped by Invoice.BillingCountry; country order.',
            'tracks_by_genre': 'Count tracks joined to Genre by GenreId, grouped by genre; genre-name order.',
        },
        'taxi': {
            'count_trips': 'Count all trip rows.', 'negative_fares': 'Count trips where fare_amount < 0.',
            'sum_fare': 'Sum fare_amount.', 'sum_total': 'Sum total_amount.',
            'mean_distance': 'Mean trip_distance.', 'mean_fare': 'Mean fare_amount.',
            'median_fare': 'Median fare_amount.',
        },
        'ldbc': {'count_people': 'Count all loaded people.', 'friends': 'List unique direct undirected friends of source.'},
    }
    for name, description in descriptions[source].items():
        spec = operations[name]
        operations[name] = OperationSpec(spec.backends, spec.result, spec.parameters, description)
    return Catalog(source, schema, operations, domains, adjacency=adjacency)
