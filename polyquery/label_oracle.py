"""Reference execution of the bounded draft annotation vocabulary.

This evaluator never calls a planner or application backend. It is authored
reference code, not independently reviewed gold or a general canonical executor.
"""
import csv
import sqlite3
from itertools import zip_longest
from pathlib import Path

from .canonical import CanonicalRequest
from .evaluation import equivalent
from .label_answers import score_label_answer
from .label_review import SCHEMA


def _field(source, name):
    table, column = name.split('.')
    if table not in SCHEMA[source] or column not in SCHEMA[source][table] | {'*'}:
        raise ValueError('Unknown reference field')
    return '*' if column == '*' else '"' + column + '"'


def compile_reference(case):
    """Compile validated labels with bound literals, never natural language."""
    request = CanonicalRequest.from_dict(case['expected'])
    source = case['source']
    parts = request.constraints
    allowed = {'scope', 'predicate', 'aggregation', 'projection', 'grouping', 'ordering', 'limit'}
    if any(c.kind not in allowed for c in parts):
        raise ValueError('Reference SQL does not support this constraint kind')
    scopes = [c.field for c in parts if c.kind == 'scope']
    if len(scopes) != 1 or scopes[0] not in SCHEMA[source]:
        raise ValueError('Reference needs one known scope')
    bindings = []
    def predicate(p):
        if p.operator in ('and', 'or'):
            return '(' + (' ' + p.operator.upper() + ' ').join(predicate(c) for c in p.children) + ')'
        if p.operator == 'not':
            return '(NOT ' + predicate(p.children[0]) + ')'
        field = _field(source, p.field)
        if p.operator in ('is_null', 'is_not_null'):
            return field + (' IS NULL' if p.operator == 'is_null' else ' IS NOT NULL')
        value = p.value.value
        if p.value.kind == 'number':
            value = float(value)
        bindings.append(value)
        return field + ' ' + {'eq': '=', 'ne': '!=', 'lt': '<', 'le': '<=', 'gt': '>', 'ge': '>='}[p.operator] + ' ?'
    aggregations = [c for c in parts if c.kind == 'aggregation']
    groups = [c for c in parts if c.kind == 'grouping']
    projections = [c for c in parts if c.kind == 'projection']
    if aggregations:
        if len(aggregations) != 1:
            raise ValueError('Reference supports one aggregate')
        agg = aggregations[0]
        function = {'mean': 'AVG', 'sum': 'SUM', 'count': 'COUNT'}[agg.operator]
        select = function + '(' + _field(source, agg.field) + ')'
        if groups:
            if len(groups) != 1 or groups[0].field != 'Invoice.BillingCountry':
                raise ValueError('Unknown grouped reference contract')
            select = _field(source, groups[0].field) + ' AS country, ' + select + ' AS revenue'
    elif len(projections) == 1:
        projection = projections[0]
        select = ','.join(_field(source, projection.field + '.' + f) for f in projection.value.split(','))
    else:
        raise ValueError('Reference requires projection or aggregate')
    sql = 'SELECT ' + select + ' FROM "' + scopes[0] + '"'
    predicates = [predicate(c.predicate) for c in parts if c.kind == 'predicate']
    if predicates:
        sql += ' WHERE ' + ' AND '.join(predicates)
    if groups:
        sql += ' GROUP BY ' + ','.join(_field(source, c.field) for c in groups)
    order = [c for c in parts if c.kind == 'ordering']
    if order:
        if any(c.operator not in ('asc', 'desc') for c in order):
            raise ValueError('Unknown order direction')
        sql += ' ORDER BY ' + ','.join(_field(source, c.field) + ' ' + c.operator.upper() for c in order)
    limits = [c.value for c in parts if c.kind == 'limit']
    if limits:
        if len(limits) != 1 or type(limits[0]) is not int or limits[0] < 0:
            raise ValueError('Invalid reference limit')
        sql += ' LIMIT ?'
        bindings.append(limits[0])
    return sql, bindings, bool(aggregations), bool(groups)


class LabelOracle:
    def __init__(self, source, config):
        self.source = source
        self.db = None
        self.graph = None
        self.cache = {}
        if source == 'ldbc':
            self.graph = {}
            with Path(config['people_path']).open(encoding='utf-8-sig', newline='') as stream:
                for row in csv.DictReader(stream, delimiter='|'):
                    self.graph[row['id']] = set()
            with Path(config['edges_path']).open(encoding='utf-8-sig', newline='') as stream:
                reader = csv.reader(stream, delimiter='|')
                next(reader)
                for a, b, _ in reader:
                    self.graph[a].add(b)
                    self.graph[b].add(a)
            return
        if source == 'chinook':
            self.db = sqlite3.connect(Path(config['data_path']).resolve().as_uri() + '?mode=ro', uri=True)
        else:
            self.db = sqlite3.connect(':memory:')
            if source == 'synthetic':
                from .data import EMPLOYEES
                self.db.execute('CREATE TABLE employees(name TEXT,department TEXT,salary REAL,experience REAL)')
                self.db.executemany('INSERT INTO employees VALUES (?,?,?,?)',
                                    [(r['name'],r['department'],r['salary'],r['experience']) for r in EMPLOYEES])
            elif source == 'taxi':
                import pyarrow.parquet as pq
                import math
                columns = ['fare_amount', 'trip_distance', 'tpep_pickup_datetime']
                self.db.execute('CREATE TABLE trips(fare_amount REAL,trip_distance REAL,tpep_pickup_datetime TEXT)')
                with pq.ParquetFile(config['data_path']) as parquet:
                    for batch in parquet.iter_batches(columns=columns, batch_size=65536):
                        vectors = [batch.column(i).to_pylist() for i in range(3)]
                        def clean_rows():
                            for fare, distance, pickup in zip(*vectors):
                                yield (fare if fare is None or math.isfinite(fare) else None,
                                       distance if distance is None or math.isfinite(distance) else None,
                                       pickup.isoformat(sep=' ') if pickup is not None else None)
                        self.db.executemany('INSERT INTO trips VALUES (?,?,?)', clean_rows())
            else:
                raise ValueError('Unknown reference source')
        self.db.row_factory = sqlite3.Row

    def score(self, case, response):
        if case['label_status'] != 'canonical':
            return None
        if response['status'] != 'ok':
            return False
        actual = response['result']['value']
        if self.source == 'ldbc':
            return score_label_answer(case, {'status': 'ok', 'value': actual}, self.graph)
        if case['context'].get('answer_policy', {}).get('scorer') == 'unordered_valid_subset':
            rows = [dict(row) for row in self.db.execute('SELECT name,department,salary,experience FROM employees')]
            return score_label_answer(case, {'status': 'ok', 'value': actual}, rows)
        sql, bindings, aggregate, grouped = compile_reference(case)
        key = (sql, tuple(bindings))
        if aggregate:
            if key not in self.cache:
                rows = self.db.execute(sql, bindings).fetchall()
                self.cache[key] = [dict(row) for row in rows] if grouped else rows[0][0]
            expected = self.cache[key]
            if not grouped:
                return equivalent(actual, expected)
            if not isinstance(actual, list) or len(actual) != len(expected):
                return False
            remaining = list(actual)
            for row in expected:
                for index, candidate in enumerate(remaining):
                    if equivalent(candidate, row):
                        remaining.pop(index)
                        break
                else:
                    return False
            return True
        if not isinstance(actual, list):
            return False
        # Stream potentially millions of ordered reference values; do not log them.
        cursor = self.db.execute(sql, bindings)
        sentinel = object()
        for expected, candidate in zip_longest(cursor, actual, fillvalue=sentinel):
            if expected is sentinel or candidate is sentinel:
                return False
            expected = expected[0] if len(expected.keys()) == 1 else dict(expected)
            if not equivalent(candidate, expected):
                return False
        return True

    def close(self):
        if self.db is not None:
            self.db.close()
