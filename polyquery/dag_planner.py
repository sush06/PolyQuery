"""Opt-in bounded composition of source-local, read-only operations."""

import re
from dataclasses import asdict, dataclass
from typing import Literal

from .constraint_planning import extract_constraints, implemented_constraints, validate_extracted
from .constraints import check_constraints
from .dag import DAGNode, InputBinding
from .planner import Plan


def validate_question_plan(question, plan, catalog):
    """Apply the same preservation checks to legacy plans and standalone stages."""
    catalog.validate_plan(plan)
    lexical = check_constraints(question, plan, catalog)
    extracted = extract_constraints(question, catalog)
    canonical = {"extracted": extracted.to_dict(), "implemented": None,
                 "preserved": None, "status": "not_executed"}
    if plan.backend != "abstain":
        implemented = implemented_constraints(plan, catalog.source)
        canonical.update(implemented=implemented.to_dict(), **validate_extracted(extracted, implemented))
    return lexical, canonical


@dataclass(frozen=True)
class PlanningDecision:
    kind: Literal["single_plan", "dag_plan", "abstain"]
    plan: Plan | None = None
    nodes: tuple[DAGNode, ...] = ()
    outputs: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self):
        return asdict(self)


class DAGPlanner:
    """Use the keyword baseline for single intents and explicitly scoped stages."""

    _reports = re.compile(
        r"who reports to the first employee listed in (engineering|sales|research)\??",
        re.IGNORECASE | re.ASCII,
    )
    _separator = re.compile(r";\s*then\s+", re.IGNORECASE | re.ASCII)
    _compound = re.compile(r";|\bthen\b|\?\s*\S|\band\s+(?:how many|what is|who|find|count|list)\b",
                           re.IGNORECASE | re.ASCII)
    _reference = re.compile(
        r"\b(their|them|those|these|its|it|they|such|same|previous|above|result|results)\b"
        r"|\bthat\s+(department|group|person|employee|set)\b",
        re.IGNORECASE | re.ASCII,
    )

    def __init__(self, baseline, catalog):
        self.baseline = baseline
        self.catalog = catalog

    def decide(self, question):
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            return self._abstain("Provide a question of 1–2000 characters.")
        text = question.strip()
        match = self._reports.fullmatch(text) if self.catalog.source == "synthetic" else None
        if match:
            department = match[1].title()
            return PlanningDecision(
                "dag_plan",
                nodes=(
                    DAGNode("employees", "sql", "list", {"department": department}),
                    DAGNode("reports", "graph", "descendants",
                            bindings={"source": InputBinding("employees", (0, "name"))}),
                ),
                outputs=("reports",),
                reason="Select the first employee in the SQL list's name order, then traverse downstream reports.",
            )
        if self._separator.search(text):
            clauses = [clause.strip() for clause in self._separator.split(text)]
            if not 2 <= len(clauses) <= 5 or any(not clause or self._compound.search(clause) for clause in clauses):
                return self._abstain("Provide two to five standalone stages separated by '; then'.")
            nodes = []
            for index, clause in enumerate(clauses, 1):
                if self._reference.search(clause):
                    return self._abstain(f"Stage {index}: Cross-stage references require a supported binding.")
                decision = self._single(clause)
                if decision.kind == "abstain":
                    return self._abstain(f"Stage {index}: {decision.reason}")
                plan = decision.plan
                nodes.append(DAGNode(f"stage_{index}", plan.backend, plan.operation, plan.parameters,
                                     depends_on=(nodes[-1].id,) if nodes else ()))
            return PlanningDecision("dag_plan", nodes=tuple(nodes), outputs=tuple(node.id for node in nodes),
                                    reason="Execute the independently scoped stages in the requested order.")
        if self._compound.search(text):
            return self._abstain("Compound requests require supported syntax and independently scoped stages.")
        return self._single(question)

    def _single(self, question):
        plan = self.baseline.plan(question)
        lexical, canonical = validate_question_plan(question, plan, self.catalog)
        if plan.backend == "abstain":
            return PlanningDecision("abstain", plan=plan, reason=plan.reason)
        if not lexical.allowed or canonical["preserved"] is False:
            details = ", ".join(lexical.findings) if not lexical.allowed else "canonical constraint mismatch or unresolved extraction"
            return self._abstain("Constraint preservation blocked execution: " + details)
        return PlanningDecision("single_plan", plan=plan)

    @staticmethod
    def _abstain(reason):
        return PlanningDecision("abstain", plan=Plan("abstain", "unsupported", reason=reason), reason=reason)
