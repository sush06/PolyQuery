import time

from .backends import CodeBackend, GraphBackend, SQLBackend
from .planner import RulePlanner


class Engine:
    def __init__(self, planner=None):
        self.planner = planner or RulePlanner()
        self.backends = {"sql": SQLBackend(), "graph": GraphBackend(), "code": CodeBackend()}

    def query(self, question):
        started = time.perf_counter()
        plan = self.planner.plan(question)
        response = {"question": question, "plan": plan.to_dict(), "status": "abstained", "result": None}
        if plan.backend != "abstain":
            if plan.backend not in self.backends:
                raise ValueError("Unknown backend")
            response["result"] = self.backends[plan.backend].execute(plan)
            response["status"] = "ok"
        response["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return response

    def close(self):
        self.backends["sql"].close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

