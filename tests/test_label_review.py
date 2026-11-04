import copy
import json
import unittest
from pathlib import Path

from polyquery.label_review import audit_labels,predicate_truth
from polyquery.canonical import Predicate,Literal
from polyquery.label_answers import score_label_answer


class LabelReviewTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).parents[1]/'benchmarks/constraint-labels-v2'
        self.labels=json.loads((root/'labels.json').read_text(encoding='utf-8'))
        self.splits=json.loads((root/'splits.json').read_text(encoding='utf-8'))

    def test_pool_integrity_and_grouped_splits(self):
        report=audit_labels(self.labels,self.splits)
        self.assertTrue(report['consistency_passed'],report['issues'])
        self.assertEqual(report['cases'],160)
        self.assertEqual(report['families'],40)
        self.assertEqual(report['draft_split_counts'],{'train':96,'validation':32,'test':32})
        self.assertEqual(report['unique_witness_checks'],45)
        self.assertEqual(report['independently_approved_cases'],0)
        self.assertFalse(report['research_ready'])
        family_splits={}
        for c in self.labels['cases']:
            family_splits.setdefault(c['family_id'],set()).add(self.splits['case_assignments'][c['id']])
        self.assertTrue(all(len(v)==1 for v in family_splits.values()))

    def test_duplicate_and_leakage_are_detected(self):
        labels=copy.deepcopy(self.labels)
        labels['cases'][1]['id']=labels['cases'][0]['id']
        self.assertFalse(audit_labels(labels,self.splits)['consistency_passed'])
        splits=copy.deepcopy(self.splits)
        first=self.labels['cases'][0]['id']
        splits['case_assignments'][first]='test' if splits['case_assignments'][first]!='test' else 'train'
        report=audit_labels(self.labels,splits)
        self.assertTrue(any('split' in issue['issue'].lower() for issue in report['issues']))

    def test_wrong_boundary_and_unbacked_approval_are_detected(self):
        labels=copy.deepcopy(self.labels)
        labels['cases'][0]['witnesses'][1]['expected_truth']=False
        report=audit_labels(labels,self.splits)
        self.assertTrue(any('witness' in issue['issue'].lower() for issue in report['issues']))
        labels=copy.deepcopy(self.labels)
        labels['cases'][0]['review']['independent_review']='approved'
        self.assertFalse(audit_labels(labels,self.splits)['consistency_passed'])

    def test_sql_unknown_is_not_python_negation(self):
        predicate=Predicate('gt','employees.salary',Literal('number',100000))
        self.assertIsNone(predicate_truth(Predicate('not',children=(predicate,)),{'employees.salary':None}))
        self.assertFalse(predicate_truth(predicate,{'employees.salary':100000}))
        self.assertTrue(predicate_truth(predicate,{'employees.salary':100001}))

    def family(self, name):
        return next(c for c in self.labels['cases'] if c['family_id']==name)

    def test_subset_validity(self):
        case=self.family('employee-limit-only')
        rows=[dict(name=name,department='Research',salary=100000,experience=4) for name in 'ABCDE']
        def score(value,reference=rows,status='ok'):
            return score_label_answer(case,{'status':status,'value':value},reference)
        self.assertTrue(score([rows[4],rows[2],rows[1]]))
        self.assertFalse(score([rows[0]]))
        self.assertFalse(score([rows[0],rows[0],rows[1]]))
        self.assertFalse(score([dict(rows[0],salary=1),rows[1],rows[2]]))
        self.assertFalse(score([dict(rows[0],extra=1),rows[1],rows[2]]))
        self.assertTrue(score(rows[:2],rows[:2]))
        self.assertTrue(score([],[]))
        self.assertFalse(score([],[],status='abstained'))

    def test_path_ties_bound_and_null(self):
        case=self.family('path-hop-bound')
        graph={'933':['a','b'],'a':['1129'],'b':['1129']}
        def score(value,reference=graph,status='ok'):
            return score_label_answer(case,{'status':status,'value':value},reference)
        self.assertTrue(score(['933','a','1129']))
        self.assertTrue(score(['933','b','1129']))
        self.assertFalse(score(['933','1129']))
        self.assertFalse(score(None))
        self.assertTrue(score(None,{}))
        self.assertFalse(score(None,{},status='abstained'))
        long={'933':['a'],'a':['b'],'b':['c'],'c':['1129']}
        self.assertTrue(score(None,long))
        self.assertFalse(score(['933','a','b','c','1129'],long))

    def test_missing_null_policy(self):
        labels=copy.deepcopy(self.labels)
        labels['cases'][0]['context'].pop('null_policy')
        self.assertTrue(any('null policy' in i['issue'] for i in audit_labels(labels,self.splits)['issues']))


if __name__=='__main__' : unittest.main()
