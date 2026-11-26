"""Shared Ollama client and metrics for the sequential agent experiments."""
from collections import Counter
from time import perf_counter
from types import SimpleNamespace

from benchmarks.ldbc_sql_graph import EDGES, PEOPLE
from benchmarks.planner_model_benchmark import BASELINE, HOST, SEED, TIMEOUT, api
from polyquery.evaluation import timing_summary

CONFIG = {"people_path": PEOPLE.resolve(), "edges_path": EDGES.resolve()}


def node(key, operation, parameters, bindings=None):
    return {"id":key, "operation":operation, "backend":"graph", "parameters":parameters,
            "bindings":bindings or {}, "depends_on":[]}


class NativeClient:
    def __init__(self, think):
        self.think = think
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.draws = []
        self.replay = None

    def create(self, *, model, messages, temperature, max_tokens, response_format):
        if self.replay is not None and len(messages) == 2 and "previous response failed" not in messages[0]["content"]:
            draw = self.replay
            self.replay = None
            if "exception" in draw:
                raise draw["exception"]
            return draw["response"]
        started = perf_counter()
        draw = {"model":model}
        self.draws.append(draw)
        try:
            raw = api("/api/chat", {"model":model,"messages":messages,"stream":False,"think":self.think,
                "format":response_format["json_schema"]["schema"],
                "options":{"temperature":temperature,"num_predict":max_tokens,"seed":SEED}})
        except Exception as error:
            draw.update(exception=error, error_type=type(error).__name__,
                        request_ms=(perf_counter()-started)*1000)
            raise
        msg = raw["message"]
        input_tokens, output_tokens = raw.get("prompt_eval_count",0), raw.get("eval_count",0)
        response = SimpleNamespace(model=model, choices=[SimpleNamespace(finish_reason=raw.get("done_reason"),
            message=SimpleNamespace(content=msg.get("content")))],
            usage=SimpleNamespace(prompt_tokens=input_tokens,completion_tokens=output_tokens,
                                  total_tokens=input_tokens+output_tokens))
        draw.update({"response":response,"thinking":bool(msg.get("thinking")),
                          "inference_ms":raw.get("total_duration",0)/1e6,
                          "load_ms":raw.get("load_duration",0)/1e6,"finish_reason":raw.get("done_reason"),
                          "input_tokens":input_tokens,"output_tokens":output_tokens})
        return response


def metrics(rows):
    answered = [r for r in rows if r["expected_refusal"] is None]
    refused = [r for r in rows if r["expected_refusal"] is not None]
    diag = [r["semantic_diagnostics"] for r in rows]
    return {"answerable":len(answered),"correct_answers":sum(r["outcome"]=="correct" for r in answered),
            "refusal_cases":len(refused),"correct_refusals":sum(r["correct"] for r in refused),
            "outcomes":dict(Counter(r["outcome"] for r in rows)),
            "failure_categories":dict(Counter(r["semantic_failure"] or "provider_or_completion"
                for r in rows if r["outcome"]=="semantic_failure")),
            "binding_error_attempts":sum(a.get("validation_error_category")=="incorrect_binding"
                                         for d in diag for a in d.get("attempts",[])),
            "semantic_rejections":sum(r["semantic_reason"] is not None for r in rows),
            "repair_attempts":sum(d.get("repair_attempted",False) for d in diag),
            "model_calls":sum(r["tokens"]["api_calls"] or 0 for r in rows),
            "input_tokens":sum(r["tokens"]["input_tokens"] or 0 for r in rows),
            "output_tokens":sum(r["tokens"]["output_tokens"] or 0 for r in rows),
            "latency_ms":timing_summary([r["total_ms"] for r in rows]),"monetary_cost_usd":None}
