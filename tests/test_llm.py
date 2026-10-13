import json
import unittest
import tempfile
import sqlite3
from pathlib import Path
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock

from polyquery.engine import Engine
from polyquery.llm import LLMPlanningError


def client_for(payload=None, *, status='completed', refusal=False, error=None):
    client = Mock()
    if error:
        client.responses.create.side_effect = error
    else:
        client.responses.create.return_value = SimpleNamespace(
            status=status, output_text=json.dumps(payload), id='test-response', model='test-model',
            usage=SimpleNamespace(input_tokens=100, output_tokens=20, total_tokens=120),
            output=[SimpleNamespace(content=[SimpleNamespace(type='refusal')])] if refusal else [])
    return client


def payload(backend='sql', operation='count', parameters=None):
    return dict(backend=backend, operation=operation, parameters=parameters or [], reason='Selected supported operation.')


class LLMTests(unittest.TestCase):
    def test_validated_plan_executes_and_records_usage(self):
        client = client_for(payload(parameters=[dict(name='department', value='Research')]))
        with Engine(planner_kind='llm', model='test-model', llm_client=client) as engine:
            response = engine.query('What is the headcount of the research team?')
            self.assertEqual(response['status'], 'ok')
            self.assertEqual(response['result']['value'], 2)
            self.assertEqual(response['planner_metadata']['total_tokens'], 120)
            request = client.responses.create.call_args.kwargs
            self.assertFalse(request['store'])
            self.assertTrue(request['text']['format']['strict'])
            self.assertIn('Research', request['instructions'])
        client.close.assert_called_once()

    def test_invalid_outputs_never_execute(self):
        invalid = [[], payload('sql', 'median'), payload(parameters=[dict(name='department', value='HR')]),
                   payload(parameters=[dict(name='department', value=True)]),
                   payload(parameters=[dict(name='department', value=None), dict(name='department', value=None)]),
                   payload('abstain', 'count'), payload('graph', 'shortest_path')]
        for value in invalid:
            with self.subTest(value=value), Engine(planner_kind='llm', model='test-model', llm_client=client_for(value)) as engine:
                engine.backends['sql'].execute = Mock()
                with self.assertRaises(LLMPlanningError):
                    engine.query('Question')
                engine.backends['sql'].execute.assert_not_called()

    def test_provider_failure_is_sanitized_and_fallback_is_explicit(self):
        for fallback in ('none', 'keyword'):
            client = client_for(error=RuntimeError('secret provider credentials'))
            with Engine(planner_kind='llm', model='test-model', llm_client=client, llm_fallback=fallback) as engine:
                if fallback == 'none':
                    with self.assertRaises(LLMPlanningError) as caught:
                        engine.query('How many employees?')
                    self.assertEqual(str(caught.exception), 'OpenAI request failed (RuntimeError)')
                else:
                    response = engine.query('How many employees?')
                    self.assertEqual(response['result']['value'], 6)
                    self.assertEqual(response['planner_metadata']['fallback'], 'keyword')
                    self.assertNotIn('secret', json.dumps(response))

    def test_refusal_and_abstention_do_not_fallback(self):
        for client in (client_for(refusal=True), client_for(payload('abstain', 'unsupported'))):
            with Engine(planner_kind='llm', model='test-model', llm_client=client, llm_fallback='keyword') as engine:
                response = engine.query('How many employees?')
                self.assertEqual(response['status'], 'abstained')
                self.assertNotIn('fallback', response['planner_metadata'])

    def test_incomplete_response_and_invalid_input(self):
        client = client_for(status='incomplete')
        with Engine(planner_kind='llm', model='test-model', llm_client=client) as engine:
            for question in ('', None, 'a' * 2001):
                self.assertEqual(engine.query(question)['status'], 'abstained')
            client.responses.create.assert_not_called()
            with self.assertRaises(LLMPlanningError):
                engine.query('How many employees?')

    def test_cost_aware_llm_keeps_intent_usage_and_closes_client(self):
        from polyquery.evaluation import source_fingerprint, evaluate
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.sqlite'
            with closing(sqlite3.connect(path)) as db:
                db.executescript('CREATE TABLE Track(GenreId INTEGER); CREATE TABLE Genre(GenreId INTEGER,Name TEXT); CREATE TABLE Customer(CustomerId INTEGER); CREATE TABLE Invoice(Total REAL,BillingCountry TEXT); INSERT INTO Invoice VALUES (10, "US");')
                db.commit()
            with Engine(source='chinook', data_path=path) as engine:
                fingerprint = source_fingerprint(engine)
            profile = Path(directory) / 'cost.json'
            profile.write_text(json.dumps(dict(profile_version=1, source='chinook', source_hashes=fingerprint,
                operation_costs_ms={'mean': {'sql': 10, 'code': 1}}, fallback_costs_ms={'sql': 10, 'code': 1},
                train_dataset_sha256='training-only', created_at='test')), encoding='utf8')
            client = client_for(payload('sql', 'mean'))
            with Engine(source='chinook', data_path=path, planner_kind='llm', model='test-model',
                        llm_client=client, policy='cost-aware', cost_profile=profile) as engine:
                response = engine.query('What is the mean invoice amount?')
                self.assertEqual(response['plan']['operation'], 'mean')
                self.assertEqual(response['plan']['backend'], 'code')
                self.assertEqual(response['result']['value'], 10)
                self.assertEqual(response['planner_metadata']['total_tokens'], 120)
                report = evaluate(engine, [{'id':'mean','question':'Mean invoice amount?',
                                           'expected':10,'backends':['sql','code']}])
                self.assertEqual(report['cost']['tokens'], 120)
                client.responses.create.return_value.output_text = json.dumps(payload('abstain', 'unsupported'))
                self.assertEqual(engine.query('Unsupported')['status'], 'abstained')
                client.responses.create.return_value.output_text = json.dumps(payload('sql', 'mean', [dict(name='bad', value='US')]))
                with self.assertRaises(LLMPlanningError):
                    engine.query('Bad filter')
            client.close.assert_called_once()

    def test_chat_transport_structured_output_and_incomplete_response(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(id='local', model='qwen-test',
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=10, total_tokens=60),
            choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=json.dumps(payload()), refusal=None))])
        with Engine(planner_kind='llm', model='qwen-test', llm_client=client,
                    llm_base_url='http://localhost:11434/v1', llm_api='chat') as engine:
            response = engine.query('How many employees?')
            self.assertEqual(response['result']['value'], 6)
            self.assertEqual(response['planner_metadata']['input_tokens'], 50)
            self.assertEqual(response['planner_metadata']['provider'], 'openai-compatible')
            request = client.chat.completions.create.call_args.kwargs
            self.assertTrue(request['response_format']['json_schema']['strict'])
            self.assertEqual(request['temperature'], 0)
            client.chat.completions.create.return_value.choices[0].finish_reason = 'length'
            with self.assertRaises(LLMPlanningError):
                engine.query('How many employees?')

    def test_configuration_errors(self):
        for kwargs in (dict(model=''), dict(model='test-model', policy='fixed-sql'),
                       dict(planner_kind='keyword', llm_fallback='keyword')):
            options = dict(planner_kind='llm', llm_client=Mock())
            options.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Engine(**options)


if __name__ == '__main__':
    unittest.main()
