"""Optional Jev constraint-tag classifier. It cannot produce or authorize plans."""
import json
import math
import os
import hashlib
from urllib.request import Request,urlopen

from .canonical import TAXONOMY

QUESTIONS={
 'scope':'Does the query identify a particular entity, table, or population to operate on?',
 'predicate':'Does the query restrict which rows or entities qualify, including exclusions, numeric comparisons, categorical filters, or dates?',
 'aggregation':'Does the query request an aggregate such as count, sum, mean, median, standard deviation, or correlation?',
 'grouping':'Does the query require separate results for each group rather than one overall result?',
 'ordering':'Does the query specify an ordering or ranking of results?',
 'limit':'Does the query specify a maximum number of returned results?',
 'distinct':'Does the query explicitly request uniqueness or distinct values?',
 'traversal':'Does the query require traversing relationships, finding neighbors, descendants, or paths?',
 'projection':'Does the query explicitly select which attributes to return?',
 'join':'Does the query require combining related entities/tables to answer, including grouping tracks by genre?',
}


class JevClassificationError(RuntimeError):
    pass


def _request(payload,api_key,timeout):
    request=Request('https://openrouter.ai/api/alpha/decisions',data=json.dumps(payload).encode(),
                    headers={'Content-Type':'application/json','Authorization':'Bearer '+api_key})
    with urlopen(request,timeout=timeout) as response:
        return json.load(response)


class JevClassifier:
    def __init__(self,model,*,api_key=None,threshold=.5,timeout=30,transport=None):
        if type(model) is not str or not model.strip(): raise ValueError('An explicit Jev model ID is required')
        if type(threshold) not in (int,float) or not math.isfinite(threshold) or not 0<=threshold<=1:
            raise ValueError('Threshold must be a finite probability')
        if timeout<=0: raise ValueError('Timeout must be positive')
        self.key=api_key or os.environ.get('OPENROUTER_API_KEY')
        if not self.key: raise ValueError('Set OPENROUTER_API_KEY in the environment')
        self.model,self.threshold,self.timeout=model,threshold,timeout
        self.transport=transport or _request

    def classify(self,question,source):
        if type(question) is not str or not question.strip() or len(question)>2000:
            raise ValueError('Question must have 1–2000 characters')
        if source not in ('synthetic','chinook','taxi','ldbc'): raise ValueError('Unknown source')
        payload={'model':self.model,'state':{'question':question,'source':source},
                 'questions':{name:{'type':'noul','instructions':instructions} for name,instructions in QUESTIONS.items()}}
        try:
            response=self.transport(payload,self.key,self.timeout)
        except Exception as error:
            raise JevClassificationError(f'Jev request failed ({type(error).__name__})') from None
        try:
            if type(response.get('model')) is not str: raise ValueError('Missing resolved model')
            probabilities={}
            for name in TAXONOMY:
                answer=response['answers'][name]
                value=answer['noul']
                if answer['type']!='noul' or type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:
                    raise ValueError('Invalid probability')
                probabilities[name]=value
            usage=response.get('usage') or {}
            usd=usage.get('cost')
            if usd is not None and (type(usd) not in (int,float) or not math.isfinite(usd) or usd<0):
                raise ValueError('Invalid cost')
            return {'provider':'openrouter','requested_model':self.model,'model':response['model'],
                    'questions_sha256':hashlib.sha256(json.dumps(QUESTIONS,sort_keys=True).encode()).hexdigest(),
                    'probabilities':probabilities,'threshold':self.threshold,
                    'tags':[name for name,p in probabilities.items() if p>=self.threshold],
                    'usage':{'usd':usd,'input_tokens':usage.get('input_tokens'),'output_tokens':usage.get('output_tokens')},
                    'note':'Uncalibrated development threshold unless independently tuned; tags are not executable constraints or preservation evidence.'}
        except (TypeError,KeyError,AttributeError,ValueError):
            raise JevClassificationError('Jev response violated the classification contract') from None
