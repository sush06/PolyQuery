"""Command-line queries, source inspection, and policy benchmarks."""
import argparse
import json
from pathlib import Path
from functools import partial

from .engine import Engine


def source_options(parser, default="synthetic"):
    parser.add_argument("--source", choices=("synthetic", "ldbc", "chinook", "taxi"), default=default)
    parser.add_argument("--people", type=Path)
    parser.add_argument("--knows", type=Path)
    parser.add_argument("--data", type=Path, help="Chinook SQLite or Taxi Parquet file")


def main():
    parser = argparse.ArgumentParser(description="PolyQuery query routing and evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    query = sub.add_parser("query")
    query.add_argument("question")
    source_options(query)
    query.add_argument("--policy", choices=("router", "fixed-sql", "fixed-code", "fixed-graph"), default="router")
    inspect = sub.add_parser("inspect-source")
    source_options(inspect, "ldbc")
    for name in ("evaluate", "compare"):
        command = sub.add_parser(name)
        source_options(command)
        command.add_argument("--dataset", type=Path, default=Path("benchmarks/demo.json"))
        command.add_argument("--output", type=Path)
        command.add_argument("--repeats", type=int, default=1)
        command.add_argument("--warmup", type=int, default=0)
        if name == "evaluate":
            command.add_argument("--policy", choices=("router", "fixed-sql", "fixed-code", "fixed-graph"), default="router")
        else:
            command.add_argument("--reverse-order", action="store_true")
    args = parser.parse_args()
    if args.source == "ldbc" and (args.people is None or args.knows is None):
        parser.error("LDBC requires --people and --knows")
    if args.source in ("chinook", "taxi") and args.data is None:
        parser.error("Chinook and Taxi require --data")
    kwargs = {"source": args.source, "people_path": args.people, "edges_path": args.knows, "data_path": args.data}
    try:
        if args.command == "compare":
            from .evaluation import evaluate_policies
            if args.source == "synthetic":
                parser.error("Comparison currently supports ldbc, chinook, and taxi")
            policies = ["router", "fixed-sql", "fixed-graph" if args.source == "ldbc" else "fixed-code"]
            if args.reverse_order:
                policies.reverse()
            dataset = json.loads(args.dataset.read_text(encoding="utf-8-sig"))
            result = evaluate_policies({policy: partial(Engine, **kwargs, policy=policy) for policy in policies}, dataset,
                                       repeats=args.repeats, warmup=args.warmup)
        else:
            with Engine(**kwargs, policy=getattr(args, "policy", "router")) as engine:
                if args.command == "query":
                    result = engine.query(args.question)
                elif args.command == "inspect-source":
                    result = engine.source_metadata
                else:
                    from .evaluation import evaluate
                    dataset = json.loads(args.dataset.read_text(encoding="utf-8-sig"))
                    result = evaluate(engine, dataset, repeats=args.repeats, warmup=args.warmup, policy_label=args.policy)
        if getattr(args, "output", None):
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
