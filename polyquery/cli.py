import argparse
import json
from pathlib import Path

from .engine import Engine


def main():
    parser = argparse.ArgumentParser(description="PolyQuery heterogeneous query routing baseline")
    sub = parser.add_subparsers(dest="command", required=True)
    query = sub.add_parser("query")
    query.add_argument("question")
    query.add_argument("--source", choices=("synthetic", "ldbc"), default="synthetic")
    query.add_argument("--people", type=Path)
    query.add_argument("--knows", type=Path)
    inspect = sub.add_parser("inspect-source")
    inspect.add_argument("--people", type=Path, required=True)
    inspect.add_argument("--knows", type=Path, required=True)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--dataset", type=Path, default=Path("benchmarks/demo.json"))
    evaluate.add_argument("--output", type=Path)
    evaluate.add_argument("--source", choices=("synthetic", "ldbc"), default="synthetic")
    evaluate.add_argument("--people", type=Path)
    evaluate.add_argument("--knows", type=Path)
    evaluate.add_argument("--repeats", type=int, default=1)
    evaluate.add_argument("--warmup", type=int, default=0)
    args = parser.parse_args()
    source = "ldbc" if args.command == "inspect-source" else getattr(args, "source", "synthetic")
    if source == "ldbc" and (args.people is None or args.knows is None):
        parser.error("--source ldbc requires --people and --knows")
    try:
        engine = Engine(source=source, people_path=getattr(args, "people", None), edges_path=getattr(args, "knows", None))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    with engine:
        if args.command == "query":
            result = engine.query(args.question)
        elif args.command == "inspect-source":
            result = engine.source_metadata
        else:
            from .evaluation import evaluate as run_evaluation
            try:
                result = run_evaluation(engine, json.loads(args.dataset.read_text(encoding="utf-8-sig")),
                                        repeats=args.repeats, warmup=args.warmup)
            except (OSError, ValueError) as error:
                parser.error(str(error))
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
