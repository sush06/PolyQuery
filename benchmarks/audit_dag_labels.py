"""Recompute answerable v1 DAG labels directly from the fixture."""

import json
from collections import Counter, deque
from pathlib import Path
from statistics import mean, median

from polyquery.data import EMPLOYEES, REPORTS, SALES


def reference_answers():
    salary = {row["name"]: row["salary"] for row in EMPLOYEES}
    engineering, research, sales = "Engineering", "Research", "Sales"

    def employees(department=None):
        return [row["name"] for row in EMPLOYEES if department is None or row["department"] == department]

    def pay(names, statistic=mean):
        return statistic(salary[name] for name in names)

    def revenue(department=None):
        return sum(amount for name, amount in SALES if department is None or name == department)

    def reports(manager, direct=False):
        children = [name for owner, name in REPORTS if owner == manager]
        if direct:
            return sorted(children)
        return sorted({*children, *(name for child in children for name in reports(child))})

    def path(source, target):
        queue = deque([[source]])
        while queue:
            route = queue.popleft()
            if route[-1] == target:
                return route
            queue.extend(
                route + [neighbor]
                for a, b in REPORTS for node, neighbor in ((a, b), (b, a))
                if node == route[-1] and neighbor not in route
            )
        raise ValueError(f"No path from {source} to {target}")

    # References follow case IDs, with scope and operation transcribed from each question.
    return {
        "single": [
            len(employees()), len(employees(research)), len(employees(sales)), len(employees(engineering)),
            pay(employees()), pay(employees(research)), pay(employees(engineering)), pay(employees(sales)),
            pay(employees(), median), pay(employees(research), median), pay(employees(engineering), median),
            revenue(research), revenue(engineering), revenue(),
            reports("Eve"), reports("Dan"), reports("Alice"),
            path("Dan", "Bob"), path("Frank", "Carol"), path("Eve", "Bob"),
        ],
        "multi_stage": [
            pay(reports("Alice")), pay(reports("Eve")), pay(reports("Carol")),
            pay(reports("Alice", True)), pay(reports("Eve", True)), pay(reports("Carol", True)),
            pay(reports("Alice"), median), pay(reports("Eve"), median),
            pay(path("Bob", "Frank")), pay(path("Alice", "Dan"), median),
            [len(employees(research)), revenue(engineering)], [len(employees(engineering)), revenue(research)],
            [reports("Eve"), pay(employees(engineering), median)], [reports("Dan"), pay(employees(research), median)],
            [path("Dan", "Frank"), pay(employees(engineering))], [path("Frank", "Bob"), pay(employees(research))],
            [len(employees()), pay(employees(engineering), median)], [pay(employees(research), median), revenue(engineering)],
            [revenue(engineering), len(employees())], reports(max(employees(research))),
        ],
        "paraphrase": [
            *([pay(reports("Alice"))] * 5),
            *([pay(reports("Alice", True))] * 5),
            *([pay(reports("Eve"), median)] * 5),
            reports(min(employees(engineering))), reports(min(employees(sales))),
            reports(min(employees(research))), reports(min(employees(sales))), reports(min(employees(research))),
        ],
    }


def main():
    cases = json.loads(Path(__file__).with_name("dag-test-v1.json").read_text(encoding="utf-8"))
    answers = {f"{group}-{index:02d}": answer for group, values in reference_answers().items()
               for index, answer in enumerate(values, 1)}
    present = {case["id"]: case["expected"] for case in cases if case["expected_status"] == "ok"}
    mismatched = sorted(key for key in answers.keys() | present.keys()
                        if key not in answers or key not in present or answers[key] != present[key])
    report = {"computed_answer_labels": len(answers), "mismatched_answer_labels": mismatched,
              "abstention_labels_by_category": dict(Counter(
                  case["group"] for case in cases if case["expected_status"] == "abstained"))}
    print(json.dumps(report, indent=2))
    if mismatched:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
