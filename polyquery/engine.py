import time

from .backends import CodeBackend, GraphBackend, SQLBackend
from .catalog import build_catalog, dag_catalog
from .dag import DAGExecutor
from .dag_planner import DAGPlanner, validate_question_plan
from .planner import RulePlanner


class Engine:
    def __init__(self, planner=None, *, source="synthetic", people_path=None, edges_path=None, data_path=None, policy="router", cost_profile=None, planner_kind="keyword", model=None, llm_client=None, llm_fallback="none", llm_base_url=None, llm_api="responses", llm_timeout=30, llm_repair=False, sql_backend="sqlite", physical_sql_selector=None):
        started = time.perf_counter()
        self.source = source
        self.policy = policy
        self.dag_planner = None
        if physical_sql_selector is not None and (source != "chinook" or sql_backend != "sqlite"
                                                  or policy != "router" or not callable(physical_sql_selector)):
            raise ValueError("Physical SQL selection requires Chinook and the standard logical routing policy")
        if sql_backend not in ("sqlite", "sqlserver") or (sql_backend == "sqlserver" and source != "chinook"):
            raise ValueError("SQL Server is available for the Chinook source only")
        if sql_backend == "sqlserver" and policy == "cost-aware":
            raise ValueError("SQL Server requires a policy without a SQLite-calibrated cost profile")
        if planner_kind not in ('keyword','llm','dag','llm-dag') or (planner is not None and planner_kind != 'keyword'):
            raise ValueError('Invalid or conflicting planner configuration')
        if planner_kind in ('dag', 'llm-dag') and policy != 'router':
            raise ValueError('DAG planning requires policy=router')
        if planner_kind == 'llm' and policy not in ('router', 'cost-aware'):
            raise ValueError('LLM planning requires policy=router or cost-aware')
        if llm_fallback not in ("none", "keyword") or (llm_fallback != "none" and planner_kind != "llm"):
            raise ValueError("LLM fallback requires planner_kind=llm and none or keyword")
        if llm_repair and (planner_kind not in ("llm", "llm-dag") or llm_fallback != "none"):
            raise ValueError("Model repair requires a model planner with fallback disabled")
        execution_policy = "router" if policy == "cost-aware" else policy
        if source == "synthetic":
            self.planner = planner or RulePlanner()
            self.backends = {"sql": SQLBackend(), "graph": GraphBackend(), "code": CodeBackend()}
            self.source_metadata = {"name": "synthetic"}
        elif source == "ldbc":
            from .ldbc import LDBCGraph, LDBCPlanner
            from .ldbc_sql import LDBCSQL, LDBCPolicyPlanner
            if people_path is None or edges_path is None:
                raise ValueError("LDBC requires people_path and edges_path")
            graph = LDBCGraph(people_path, edges_path)
            self.planner = planner or LDBCPolicyPlanner(LDBCPlanner(graph), execution_policy)
            self.backends = {"graph": graph, "sql": LDBCSQL(graph)}
            self.source_metadata = graph.metadata
        elif source in ("chinook", "taxi"):
            from .tabular import TabularPlanner, chinook_backends, taxi_backends
            if data_path is None:
                raise ValueError(f"{source} requires data_path")
            self.planner = planner or TabularPlanner(source, execution_policy)
            self.backends, self.source_metadata = (chinook_backends if source == "chinook" else taxi_backends)(data_path)
            if sql_backend == "sqlserver":
                from .sqlserver import ChinookSQLServer, STATEMENTS

                try:
                    self.backends["sql"] = ChinookSQLServer(self.backends["sql"], data_path)
                except Exception:
                    self.close()
                    raise
                self.source_metadata["sql_backend"] = "sqlserver"
                self.source_metadata["sqlserver_operations"] = sorted(STATEMENTS)
        else:
            raise ValueError(f"Unknown data source: {source}")
        if source == "synthetic" and policy != "router":
            self.close()
            raise ValueError(f"Fixed policies are not implemented for {source}")
        self.catalog = build_catalog(self)
        if physical_sql_selector is not None:
            from .physical_sql import PhysicalSQLRouter
            from .sqlserver import ChinookSQLServer, STATEMENTS

            local = self.backends["sql"]
            try:
                remote = ChinookSQLServer(local, data_path)
                self.backends["sql"] = remote
                contracts = {name: self.catalog.operations[name] for name in STATEMENTS}
                self.backends["sql"] = PhysicalSQLRouter(
                    self.catalog, {"sqlite": (remote.fixture_hash, contracts, local),
                                   "sqlserver": (remote.fixture_hash, contracts, remote)},
                    physical_sql_selector)
            except Exception:
                self.close()
                raise
        self.dag_executor = DAGExecutor(dag_catalog(self.catalog), self.backends)
        self.dag_planner = DAGPlanner(self.planner, self.catalog) if planner_kind == "dag" else None
        if planner_kind == "llm-dag":
            try:
                from .llm_dag import LLMDAGPlanner
                self.dag_planner = LLMDAGPlanner(self.catalog, self.dag_executor, self.planner, model,
                                                client=llm_client, base_url=llm_base_url,
                                                api_mode=llm_api, timeout=llm_timeout, repair=llm_repair)
            except Exception:
                self.close()
                raise
        if planner_kind == 'llm':
            try:
                from .llm import LLMPlanner
                self.planner = LLMPlanner(self.catalog,model,client=llm_client,
                                          fallback=self.planner if llm_fallback == "keyword" else None,
                                          base_url=llm_base_url, api_mode=llm_api, timeout=llm_timeout,
                                          baseline=self.planner, repair=llm_repair)
            except Exception:
                self.close()
                raise
        if policy == "cost-aware":
            try:
                from .costs import CostPlanner, load_profile
                from .evaluation import source_fingerprint
                if cost_profile is None:
                    raise ValueError("Cost-aware policy requires cost_profile")
                self.cost_profile = load_profile(cost_profile)
                self.catalog.costs = {'status':'measured_training', 'units':'execution_ms',
                                      'operation_costs':self.cost_profile['operation_costs_ms'],
                                      'unseen_operation_fallback':self.cost_profile['fallback_costs_ms'],
                                      'training_dataset_sha256':self.cost_profile['train_dataset_sha256'],
                                      'source_hashes':self.cost_profile['source_hashes'],
                                      'calibrated_at':self.cost_profile['created_at'], 'monetary_cost':None}
                self.planner = CostPlanner(self.planner, self.cost_profile, source, source_fingerprint(self), self.backends, catalog=self.catalog)
            except Exception:
                self.close()
                raise
        self.initialization_ms = (time.perf_counter() - started) * 1000

    def query(self, question, *, task_context=None):
        started = time.perf_counter()
        if self.dag_planner is not None:
            return self._query_dag(question, started)
        # Keep policy metadata out of keyword matching and lexical guards. The
        # keyword planner has no policy-context parser; independent gold scoring
        # exposes that limitation instead of mutating its natural-language input.
        if task_context and hasattr(self.planner, 'plan_with_context'):
            plan = self.planner.plan_with_context(question, task_context)
        else:
            plan = self.planner.plan(question)
        constraint_check, canonical_validation = validate_question_plan(question, plan, self.catalog)
        blocked = not constraint_check.allowed or canonical_validation['preserved'] is False
        if blocked:
            from .planner import Plan
            proposed_plan = plan.to_dict()
            details = ', '.join(constraint_check.findings) if not constraint_check.allowed else 'canonical constraint mismatch or unresolved extraction'
            plan = Plan('abstain','unsupported',reason='Constraint preservation blocked execution: '+details)
        planned = time.perf_counter()
        response = {"question": question, "source": self.source, "plan": plan.to_dict(), "status": "abstained", "result": None}
        response['constraint_check'] = constraint_check.to_dict()
        response['canonical_validation'] = canonical_validation
        if blocked:
            response['proposed_plan'] = proposed_plan
        if hasattr(self.planner,'last_metadata'):
            response['planner_metadata'] = dict(self.planner.last_metadata)
            diagnostics = response['planner_metadata'].get("plan_diagnostics")
            if blocked and diagnostics is not None:
                graph_scope = any("endpoint" in finding or "person_ids" in finding for finding in constraint_check.findings)
                category = "wrong_graph_scope" if graph_scope else "constraint_preservation"
                diagnostics.setdefault("validation_error_category", category)
                diagnostics["final_validation_error_category"] = category
        if plan.backend != "abstain":
            if plan.backend not in self.backends:
                raise ValueError("Unknown backend")
            response["result"] = self.backends[plan.backend].execute(plan)
            self.catalog.validate_result(plan, response['result'])
            response["status"] = "ok"
        finished = time.perf_counter()
        response["planning_ms"] = (planned - started) * 1000
        response["execution_ms"] = (finished - planned) * 1000
        response["latency_ms"] = (finished - started) * 1000
        return response

    def _query_dag(self, question, started):
        decision = self.dag_planner.decide(question)
        planned = time.perf_counter()
        response = {"question": question, "source": self.source, "decision": decision.to_dict(),
                    "status": "abstained", "result": None}
        if hasattr(self.dag_planner, "last_metadata"):
            response["planner_metadata"] = dict(self.dag_planner.last_metadata)
        if decision.plan is not None:
            response["plan"] = decision.plan.to_dict()
        if decision.kind == "dag_plan":
            results = self.execute_dag(decision.nodes)
            values = [results[node]["value"] for node in decision.outputs]
            response.update(status="ok", steps=results, result={"value": values[0] if len(values) == 1 else values})
        elif decision.kind == "single_plan":
            plan = decision.plan
            result = self.backends[plan.backend].execute(plan)
            self.catalog.validate_result(plan, result)
            response.update(status="ok", result=result)
        finished = time.perf_counter()
        response.update(planning_ms=(planned - started) * 1000,
                        execution_ms=(finished - planned) * 1000,
                        latency_ms=(finished - started) * 1000)
        return response

    def execute_dag(self, nodes):
        """Execute structured read-only nodes against this engine's source."""
        return self.dag_executor.execute(nodes)

    def close(self):
        if hasattr(self.dag_planner, 'close'):
            self.dag_planner.close()
        if hasattr(self.planner,'close'):
            self.planner.close()
        for backend in self.backends.values():
            if hasattr(backend, "close"):
                backend.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
