import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from polyquery.canonical import Literal, Predicate, Constraint, CanonicalRequest, validate_preservation
from polyquery.constraint_planning import extract_constraints, implemented_constraints, validate_extracted
from polyquery.engine import Engine
from polyquery.planner import Plan


class CanonicalTests(unittest.TestCase):
    def test_typed_literals_roundtrip_and_validation(self):
        a=Predicate('ge','trips.fare_amount',Literal('number','0.00'))
        b=Predicate('ge','trips.fare_amount',Literal('number',0))
        self.assertEqual(a,b)
        self.assertEqual(Literal('number','123456789012345678901234567890123456789').value,'123456789012345678901234567890123456789')
        request=CanonicalRequest('taxi',(Constraint('predicate','where','where',predicate=a),))
        self.assertEqual(CanonicalRequest.from_dict(json.loads(json.dumps(request.to_dict()))),request)
        for kind,value in [('number',True),('number','NaN'),('number','Infinity'),('boolean','true'),('date','2026-02-30')]:
            with self.subTest(kind=kind,value=value),self.assertRaises(ValueError): Literal(kind,value)
        with self.assertRaises(ValueError): Predicate('eq','x',Literal('null',None))
        with self.assertRaises(ValueError): Constraint('aggregation','x','execute')
        with self.assertRaises(ValueError): Constraint('distinct','trips','enabled',1)
        with self.assertRaises(ValueError): CanonicalRequest('taxi',version=True)

    def test_boolean_structure_and_order_are_preserved(self):
        a=Predicate('eq','employees.department',Literal('string','Sales'))
        b=Predicate('gt','employees.salary',Literal('number',100000))
        self.assertEqual(Predicate('and',children=(a,b)).canonical(),Predicate('and',children=(b,a)).canonical())
        self.assertNotEqual(Predicate('and',children=(a,b)).canonical(),Predicate('or',children=(a,b)).canonical())
        left=CanonicalRequest('x',(Constraint('ordering','salary','desc'),Constraint('ordering','name','asc')))
        right=CanonicalRequest('x',tuple(reversed(left.constraints)))
        self.assertFalse(validate_preservation(left,right)['preserved'])

    def test_zero_boundary_and_missing_invented_filters(self):
        catalog=SimpleNamespace(source='taxi')
        extracted=extract_constraints('Average fare excluding negative fares',catalog)
        predicate=next(c for c in extracted.request.constraints if c.kind=='predicate').predicate
        self.assertEqual(predicate.operator,'ge')
        self.assertEqual(predicate.value,Literal('number',0))
        plain=implemented_constraints(Plan('sql','mean_fare'),'taxi')
        result=validate_extracted(extracted,plain)
        self.assertFalse(result['preserved'])
        self.assertIn('predicate',[c['kind'] for c in result['missing']])
        wrong=CanonicalRequest('taxi',tuple(Constraint('predicate','where','where',predicate=Predicate('gt','trips.fare_amount',Literal('number',0))) if c.kind=='predicate' else c for c in extracted.request.constraints))
        self.assertFalse(validate_preservation(extracted.request,wrong)['preserved'])
        invented=validate_preservation(plain,extracted.request)
        self.assertIn('predicate',[c['kind'] for c in invented['invented']])

    def test_source_statistic_and_strict_comparison(self):
        c=SimpleNamespace(source='taxi')
        request=extract_constraints('Average fare amount above 5',c)
        predicate=next(x for x in request.request.constraints if x.kind=='predicate').predicate
        self.assertEqual(predicate,Predicate('gt','trips.fare_amount',Literal('number',5)))
        self.assertFalse(validate_preservation(CanonicalRequest('taxi'),CanonicalRequest('chinook'))['preserved'])
        mean=extract_constraints('Average fare amount',c)
        self.assertFalse(validate_extracted(mean,implemented_constraints(Plan('sql','mean_distance'),'taxi'))['preserved'])

    def test_existing_supported_plans_and_unresolved_scope(self):
        with Engine() as engine:
            q='Mean salary in Research'
            parsed=extract_constraints(q,engine.catalog)
            self.assertTrue(validate_extracted(parsed,implemented_constraints(engine.planner.plan(q),'synthetic'))['preserved'])
            unclear=extract_constraints('Average salary above 5 or experience below 2',engine.catalog)
            self.assertIn('complex_boolean_scope_requires_explicit_annotation',unclear.unresolved)
        taxi=SimpleNamespace(source='taxi')
        temporal=extract_constraints('Average fare after yesterday',taxi)
        self.assertTrue(temporal.unresolved)

    def test_field_substitution_is_blocked_before_execution(self):
        # Exercise the shared engine boundary with a tiny schema/catalog double;
        # no full Taxi import or API call is required.
        from polyquery.catalog import Catalog,OperationSpec,ResultSpec
        catalog=Catalog('taxi',{}, {'mean_distance':OperationSpec(('sql',),ResultSpec('number',True))},{})
        engine=Engine()
        self.addCleanup(engine.close)
        engine.source='taxi'; engine.catalog=catalog
        engine.planner=SimpleNamespace(plan=lambda question:Plan('sql','mean_distance'))
        execute=Mock()
        engine.backends['sql'].execute=execute
        response=engine.query('Average fare amount')
        self.assertEqual(response['status'],'abstained')
        self.assertFalse(response['canonical_validation']['preserved'])
        execute.assert_not_called()

    def test_authored_extraction_evaluation(self):
        from pathlib import Path
        from polyquery.constraint_evaluation import evaluate_constraint_cases
        dataset=json.loads((Path(__file__).parents[1]/'benchmarks/constraints-dev.json').read_text())
        report=evaluate_constraint_cases(dataset)
        self.assertEqual(report['cases'],len(dataset['cases']))
        self.assertIn('predicate',report['constraint_kind_metrics'])
        self.assertEqual(len(report['results']),report['cases'])


if __name__=='__main__': unittest.main()
