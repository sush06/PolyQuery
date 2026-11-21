"""Execute explicit, source-scoped DAGs over catalogued read-only operations."""

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter

from .backends import UnsupportedPlan
from .planner import Plan


@dataclass(frozen=True)
class InputBinding:
    """Read a predecessor's value, optionally selecting a list index or row field."""

    node: str
    path: tuple[int | str, ...] = ()


@dataclass(frozen=True)
class DAGNode:
    id: str
    backend: str
    operation: str
    parameters: Mapping[str, str | None] = field(default_factory=dict)
    bindings: Mapping[str, InputBinding] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    retries: int = 0


class DAGValidationError(UnsupportedPlan):
    """An invalid DAG definition."""


class DAGExecutionError(UnsupportedPlan):
    """A failed node, with the underlying failure available as its cause."""

    def __init__(self, node_id, stage, attempts):
        self.node_id = node_id
        self.stage = stage
        self.attempts = attempts
        super().__init__(f"DAG node {node_id!r} failed during {stage} after {attempts} backend attempt(s)")


class TransientBackendError(RuntimeError):
    """A backend failure that can be retried for a read-only operation."""


class _PlanBackendAdapter:
    """Bridge SQL, graph and statistics backends through their shared Plan API."""

    def __init__(self, backend):
        self.backend = backend

    def execute(self, plan):
        try:
            return self.backend.execute(plan)
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", None)
            if code is not None and (code & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise TransientBackendError("SQLite database is busy or locked") from exc
            raise


class DAGExecutor:
    """Validate the entire DAG, then execute nodes in dependency order."""

    def __init__(self, catalog, backends):
        self.catalog = catalog
        self.backends = backends

    def validate(self, nodes):
        """Check a proposed graph without dispatching operations."""
        self._prepare(nodes)

    def _prepare(self, nodes):
        by_id = {}
        for node in nodes:
            if not isinstance(node, DAGNode) or type(node.id) is not str or not node.id.strip():
                raise DAGValidationError("Each node must have a nonempty string ID")
            if node.id in by_id:
                raise DAGValidationError(f"Duplicate node ID: {node.id!r}")
            if type(node.retries) is not int or node.retries < 0:
                raise DAGValidationError(f"Invalid retry count for node {node.id!r}")
            if not isinstance(node.parameters, Mapping) or not isinstance(node.bindings, Mapping):
                raise DAGValidationError(f"Expected named parameters and bindings for node {node.id!r}")
            if not isinstance(node.depends_on, (tuple, list)) or any(type(dep) is not str for dep in node.depends_on):
                raise DAGValidationError(f"Invalid dependencies for node {node.id!r}")
            by_id[node.id] = DAGNode(node.id, node.backend, node.operation, dict(node.parameters),
                                     dict(node.bindings), tuple(node.depends_on), node.retries)

        sorter = TopologicalSorter()
        for node_id in by_id:
            sorter.add(node_id)
        for node in by_id.values():
            try:
                dependencies = self._dependencies(node, by_id)
            except UnsupportedPlan as exc:
                raise DAGValidationError(f"Invalid DAG node {node.id!r}: {exc}") from exc
            sorter.add(node.id, *dependencies)
        try:
            return [by_id[node_id] for node_id in sorter.static_order()]
        except CycleError as exc:
            raise DAGValidationError(f"Dependency cycle: {exc.args[1]!r}") from exc

    def _operation(self, node):
        if type(node.backend) is not str or type(node.operation) is not str:
            raise UnsupportedPlan("Expected string backend and operation names")
        spec = self.catalog.operations.get(node.operation)
        if spec is None or node.backend not in spec.backends or node.backend not in self.backends:
            raise UnsupportedPlan(f"Unavailable operation: {node.backend}/{node.operation}")
        return spec

    def _dependencies(self, node, by_id):
        spec = self._operation(node)
        literals, bindings = set(node.parameters), set(node.bindings)
        if literals & bindings:
            raise UnsupportedPlan("A parameter cannot have both a literal and a binding")
        provided = literals | bindings
        if provided - spec.parameters.keys():
            raise UnsupportedPlan("Unexpected plan parameters")
        for name, parameter in spec.parameters.items():
            if parameter.required and name not in provided:
                raise UnsupportedPlan(f"Missing parameter: {name}")
            if name in literals:
                if parameter.kind == "entity_ids":
                    raise UnsupportedPlan(f"{name} must be bound to an upstream entity list")
                self.catalog.validate_parameter(name, parameter, node.parameters[name])

        dependencies = dict.fromkeys(node.depends_on)
        for name, binding in node.bindings.items():
            if not isinstance(binding, InputBinding) or type(binding.node) is not str or type(binding.path) is not tuple:
                raise UnsupportedPlan(f"Invalid binding for {name!r}")
            if binding.node not in by_id:
                raise UnsupportedPlan(f"Unknown dependency: {binding.node!r}")
            result = self._operation(by_id[binding.node]).result
            self._validate_binding(name, spec.parameters[name], binding.path, result)
            dependencies[binding.node] = None
        for dependency in dependencies:
            if dependency not in by_id:
                raise UnsupportedPlan(f"Unknown dependency: {dependency!r}")
        return dependencies

    @staticmethod
    def _validate_binding(name, parameter, path, result):
        kind = result.kind
        entity = result.entity if kind == "entity_ids" else None
        if path:
            index = path[0]
            if type(index) is not int or index < 0:
                raise UnsupportedPlan(f"Binding for {name!r} requires a nonnegative list index")
            if kind in ("entity_ids", "path") and len(path) == 1:
                kind, entity = "string", result.entity
            elif (kind == "rows" and len(path) == 2 and type(path[1]) is str
                  and path[1] in result.fields):
                kind = result.fields[path[1]].removesuffix("?")
            else:
                raise UnsupportedPlan(f"Invalid result path for binding {name!r}")
        if (kind != parameter.kind
                or (entity and parameter.domain and entity != parameter.domain)
                or (kind == "entity_ids" and entity != parameter.domain)):
            raise UnsupportedPlan(f"Incompatible result type for binding {name!r}")

    def execute(self, nodes):
        """Return validated backend result envelopes keyed by node ID in execution order."""
        results = {}
        for node in self._prepare(nodes):
            try:
                parameters = dict(node.parameters)
                for name, binding in node.bindings.items():
                    value = results[binding.node]["value"]
                    for part in binding.path:
                        value = value[part]
                    parameters[name] = value
                plan = Plan(node.backend, node.operation, parameters)
                self.catalog.validate_plan(plan)
            except (UnsupportedPlan, IndexError, KeyError, TypeError) as exc:
                raise DAGExecutionError(node.id, "input validation", 0) from exc

            adapter = _PlanBackendAdapter(self.backends[node.backend])
            for attempt in range(1, node.retries + 2):
                try:
                    result = adapter.execute(plan)
                except Exception as exc:
                    if isinstance(exc, TransientBackendError) and attempt <= node.retries:
                        continue
                    raise DAGExecutionError(node.id, "backend execution", attempt) from exc
                break
            try:
                self.catalog.validate_result(plan, result)
            except UnsupportedPlan as exc:
                raise DAGExecutionError(node.id, "result validation", attempt) from exc
            results[node.id] = result
        return results
