import time

from .backends import CodeBackend, GraphBackend, SQLBackend
from .planner import RulePlanner


class Engine:
    def __init__(self, planner=None, *, source="synthetic", people_path=None, edges_path=None):
        self.source = source
        if source == "synthetic":
            self.planner = planner or RulePlanner()
            self.backends = {"sql": SQLBackend(), "graph": GraphBackend(), "code": CodeBackend()}
            self.source_metadata = {"name": "synthetic"}
        elif source == "ldbc":
            from .ldbc import LDBCGraph, LDBCPlanner
            if people_path is None or edges_path is None:
                raise ValueError("LDBC requires people_path and edges_path")
            graph = LDBCGraph(people_path, edges_path)
            self.planner = planner or LDBCPlanner(graph)
            self.backends = {"graph": graph}
            self.source_metadata = graph.metadata
        else:
            raise ValueError(f"Unknown data source: {source}")

    def query(self, question):
        started = time.perf_counter()
        plan = self.planner.plan(question)
        response = {"question": question, "source": self.source, "plan": plan.to_dict(), "status": "abstained", "result": None}
        if plan.backend != "abstain":
            if plan.backend not in self.backends:
                raise ValueError("Unknown backend")
            response["result"] = self.backends[plan.backend].execute(plan)
            response["status"] = "ok"
        response["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return response

    def close(self):
        for backend in self.backends.values():
            if hasattr(backend, "close"):
                backend.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
