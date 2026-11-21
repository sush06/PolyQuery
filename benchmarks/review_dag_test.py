"""Export blind DAG labels for review and check the returned decisions."""

import argparse
import hashlib
import json
import math
import random
import re
from pathlib import Path

from polyquery.evaluation import equivalent


ROOT = Path(__file__).parent
DRAFT = ROOT / "dag-test-v2-draft.json"
PRIOR_DATASETS = ("demo.json", "dag-dev.json", "dag-test-v1.json", "qwen-dag-smoke.json")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _normalized(question):
    return " ".join(re.findall(r"\w+", question.casefold()))


def load_draft():
    cases = _read(DRAFT)
    ids = {case["id"] for case in cases}
    questions = {_normalized(case["question"]) for case in cases}
    if len(ids) != len(cases) or len(questions) != len(cases):
        raise ValueError("The draft contains duplicate IDs or normalized questions.")
    overlap = [
        filename for filename in PRIOR_DATASETS
        if questions & {_normalized(case["question"]) for case in _read(ROOT / filename)}
    ]
    if overlap:
        raise ValueError(f"The draft overlaps earlier benchmarks: {', '.join(overlap)}")
    return cases


def _review_order(cases):
    shuffled = list(cases)
    random.Random(20261120).shuffle(shuffled)
    return {f"q-{index:03d}": case for index, case in enumerate(shuffled, 1)}


def blind_review(cases):
    prompts = [{"id": review_id, "question": case["question"], "expected_status": "",
                "expected": None, "capability": "", "notes": ""}
               for review_id, case in _review_order(cases).items()]
    return {"reviewer": "", "draft_sha256": hashlib.sha256(DRAFT.read_bytes()).hexdigest(), "cases": prompts}


def _valid_answer(value):
    if isinstance(value, list):
        return all(_valid_answer(item) for item in value)
    return value is None or type(value) in (str, int) or (type(value) is float and math.isfinite(value))


def review_disagreements(cases, review):
    if review.get("draft_sha256") != hashlib.sha256(DRAFT.read_bytes()).hexdigest():
        raise ValueError("The review belongs to a different draft.")
    if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        raise ValueError("Record the independent reviewer's name or identifier.")
    reviewed = review.get("cases")
    if not isinstance(reviewed, list):
        raise ValueError("The review must contain a list of cases.")
    if any(not isinstance(item, dict) or type(item.get("id")) is not str for item in reviewed):
        raise ValueError("Each review row requires a string ID.")
    by_id = {item["id"]: item for item in reviewed}
    order = _review_order(cases)
    if len(by_id) != len(reviewed) or by_id.keys() != order.keys():
        raise ValueError("The review must contain every review ID exactly once.")
    differences = []
    for review_id, case in order.items():
        item = by_id[review_id]
        status = item.get("expected_status")
        if (status not in ("ok", "abstained", "unclear") or "expected" not in item
                or not _valid_answer(item["expected"]) or item.get("question") != case["question"]
                or item.get("capability", "") not in ("", "available", "gap")
                or (status != "ok" and item["expected"] is not None)
                or (status == "ok" and "capability" in case and not item.get("capability"))):
            raise ValueError(f"Missing or invalid review for {review_id}")
        fields = [field for field in ("expected_status", "expected")
                  if not equivalent(item.get(field), case.get(field))]
        if item.get("capability", "") != case.get("capability", ""):
            fields.append("capability")
        if fields:
            differences.append({"id": case["id"], "review_id": review_id, "fields": fields,
                                "notes": item.get("notes", "")})
    return differences


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--export", type=Path)
    action.add_argument("--check", type=Path)
    args = parser.parse_args()
    try:
        cases = load_draft()
        if args.export:
            with args.export.open("x", encoding="utf-8") as stream:
                json.dump(blind_review(cases), stream, indent=2)
                stream.write("\n")
            print(f"Exported {len(cases)} unlabeled questions to {args.export}")
        else:
            differences = review_disagreements(cases, _read(args.check))
            print(json.dumps({"cases": len(cases), "disagreements": differences}, indent=2))
            if differences:
                parser.exit(1)
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
