import unittest
from polyquery.jev import JevClassifier,JevClassificationError,QUESTIONS
from polyquery.canonical import TAXONOMY


class JevTests(unittest.TestCase):
    def response(self,value=.8):
        return {'model':'test-jev','answers':{k:{'type':'noul','noul':value} for k in QUESTIONS},'usage':{'cost':.001}}

    def test_tag_threshold_and_unknown_cost(self):
        captured=[]
        response=self.response()
        response['usage']={}
        def transport(payload,key,timeout):
            captured.append(payload)
            return response
        classifier=JevClassifier('test-jev',api_key='test-only',transport=transport)
        result=classifier.classify('Average fare excluding negative fares','taxi')
        self.assertEqual(set(result['tags']),set(TAXONOMY))
        self.assertIsNone(result['usage']['usd'])
        self.assertEqual(captured[0]['state']['source'],'taxi')
        self.assertNotIn('test-only',str(captured))

    def test_invalid_probabilities_and_sanitized_errors(self):
        for value in [True,-1,2,float('nan'),'0.8']:
            classifier=JevClassifier('test',api_key='test-only',transport=lambda *args:self.response(value))
            with self.subTest(value=value),self.assertRaises(JevClassificationError):
                classifier.classify('Question','taxi')
        def fail(*args): raise RuntimeError('secret-token')
        with self.assertRaises(JevClassificationError) as error:
            JevClassifier('test',api_key='test',transport=fail).classify('Question','taxi')
        self.assertNotIn('secret-token',str(error.exception))


if __name__=='__main__': unittest.main()
