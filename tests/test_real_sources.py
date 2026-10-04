import hashlib
import importlib.util
import math
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from polyquery.engine import Engine
from polyquery.evaluation import answer_matches, equivalent, evaluate
from polyquery.planner import Plan
from polyquery.backends import UnsupportedPlan


class RealSourceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def chinook(self, totals=(1, 2, 9, 10)):
        path = self.root / "chinook.sqlite"
        with closing(sqlite3.connect(path)) as db:
            db.executescript('CREATE TABLE Track(TrackId INTEGER,GenreId INTEGER); CREATE TABLE Genre(GenreId INTEGER,Name TEXT); CREATE TABLE Customer(CustomerId INTEGER); CREATE TABLE Invoice(Total REAL,BillingCountry TEXT);')
            db.executemany('INSERT INTO Track VALUES (?,?)', [(1, 1), (2, 1), (3, 2)])
            db.executemany('INSERT INTO Genre VALUES (?,?)', [(1, 'Rock'), (2, 'Jazz')])
            db.execute('INSERT INTO Customer VALUES (1)')
            db.executemany('INSERT INTO Invoice VALUES (?,?)', ((total, 'US' if i % 2 == 0 else 'CA') for i, total in enumerate(totals)))
            db.commit()
        return path

    def test_chinook_equivalent_policies_and_read_only_file(self):
        path = self.chinook()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        from polyquery.tabular import CHINOOK_QUESTIONS
        with Engine(source='chinook', data_path=path) as engine:
            for question in CHINOOK_QUESTIONS:
                router = engine.query(question)
                plan = engine.planner.plan(question)
                sql = engine.backends['sql'].execute(Plan('sql', plan.operation))['value']
                code = engine.backends['code'].execute(Plan('code', plan.operation))['value']
                with self.subTest(question=question):
                    self.assertTrue(equivalent(sql, code), (sql, code))
                    self.assertEqual(router['status'], 'ok')
            self.assertEqual(engine.query('Median invoice total')['result']['value'], 5.5)
            for statement in ['DELETE FROM Invoice', 'DROP TABLE Track', 'PRAGMA query_only=OFF']:
                with self.assertRaises(sqlite3.DatabaseError):
                    engine.backends['sql'].db.execute(statement)
            self.assertEqual(engine.query('Total invoice revenue after 2020')['status'], 'abstained')
            with self.assertRaises(UnsupportedPlan):
                engine.backends['code'].execute(Plan('code', 'mean', {'filter': 'US'}))
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
        for policy, backend in [('fixed-sql', 'sql'), ('fixed-code', 'code')]:
            with Engine(source='chinook', data_path=path, policy=policy) as engine:
                self.assertEqual(engine.query('Median invoice total')['plan']['backend'], backend)

    def test_chinook_empty_aggregates_and_hash_binding(self):
        path = self.chinook(())
        with Engine(source='chinook', data_path=path) as engine:
            for backend in engine.backends.values():
                self.assertIsNone(backend.execute(Plan('sql', 'median'))['value'])
            dataset = {'metadata': {'source': 'chinook', 'source_hashes': {'database_file': 'wrong'}},
                       'cases': [{'id': 'a', 'question': 'How many tracks?', 'backend': 'sql', 'expected': 3}]}
            with self.assertRaises(ValueError):
                evaluate(engine, dataset)

    @unittest.skipUnless(importlib.util.find_spec('pyarrow'), 'Optional Taxi dependency not installed')
    def test_taxi_all_rows_nulls_nonfinite_negatives_and_policies(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        path = self.root / 'taxi.parquet'
        pq.write_table(pa.table({'fare_amount': [1., 3., -2., None, math.inf, math.nan],
                                 'total_amount': [2., 4., 0., None, math.inf, math.nan],
                                 'trip_distance': [0., 2., 4., None, math.inf, math.nan]}), path)
        before = path.read_bytes()
        from polyquery.tabular import TAXI_QUESTIONS
        with Engine(source='taxi', data_path=path) as engine:
            self.assertEqual(engine.source_metadata['rows'], 6)
            self.assertEqual(engine.source_metadata['nulls']['fare_amount'], 3)
            for question in TAXI_QUESTIONS:
                plan = engine.planner.plan(question)
                sql = engine.backends['sql'].execute(Plan('sql', plan.operation))['value']
                code = engine.backends['code'].execute(Plan('code', plan.operation))['value']
                with self.subTest(question=question):
                    self.assertTrue(equivalent(sql, code), (sql, code))
            self.assertEqual(engine.query('Median fare amount')['result']['value'], 1)
            self.assertEqual(engine.query('How many trips?')['result']['value'], 6)
            self.assertEqual(engine.query('How many negative fares?')['result']['value'], 1)
        self.assertEqual(path.read_bytes(), before)

    def test_ldbc_sql_reachability_ties_and_large_ids(self):
        people, edges = self.root / 'people.csv', self.root / 'edges.csv'
        huge = '99999999999999999999999'
        people.write_text('id|firstName|lastName\n1|A|One\n2|B|Two\n3|C|Three\n4|D|Four\n5|E|Five\n'+huge+'|F|Six\n')
        edges.write_text('Person.id|Person.id|creationDate\n1|2|0\n1|3|0\n2|4|0\n3|4|0\n4|'+huge+'|0\n')
        with Engine(source='ldbc', people_path=people, edges_path=edges, policy='fixed-sql') as engine:
            graph = engine.backends['graph']
            for source, target, expected in [('1', '4', ['1', '2', '4']), (huge, '1', [huge, '4', '2', '1']), ('1', '1', ['1']), ('1', '5', None)]:
                actual = engine.query(f'Shortest path from {source} to {target}')
                self.assertEqual(actual['plan']['backend'], 'sql')
                self.assertTrue(answer_matches(actual['result']['value'], {'expected': expected, 'comparison': 'shortest_path'}, graph))
            self.assertTrue(answer_matches(['1', '3', '4'], {'expected': ['1', '2', '4'], 'comparison': 'shortest_path'}, graph))
            self.assertFalse(answer_matches(['1', huge, '4'], {'expected': ['1', '2', '4'], 'comparison': 'shortest_path'}, graph))
            sql_friends = engine.query('Friends of 4')['result']['value']
            self.assertEqual(sql_friends, graph.execute(Plan('graph', 'friends', {'source': '4'}))['value'])


if __name__ == '__main__':
    unittest.main()
