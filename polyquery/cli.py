import argparse
import json
from pathlib import Path

from .engine import Engine


def main():
    parser = argparse.ArgumentParser(description="PolyQuery heterogeneous query routing baseline")
    sub = parser.add_subparsers(dest="command", required=True)
    query = sub.add_parser("query")
    query.add_argument("question")
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--dataset", type=Path, default=Path("benchmarks/demo.json"))
    evaluate.add_argument("--output", type=Path)
    args = parser.parse_args()
    with Engine() as engine:
        if args.command == "query":
            result = engine.query(args.question)
        else:
            from .evaluation import evaluate as run_evaluation
            result = run_evaluation(engine, json.loads(args.dataset.read_text()))
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

