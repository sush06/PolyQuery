"""Command-line queries, source inspection, and policy benchmarks."""
import argparse
import json
from pathlib import Path
from functools import partial

from .engine import Engine
from .llm import LLMPlanningError


def source_options(parser, default="synthetic"):
    parser.add_argument("--source", choices=("synthetic", "ldbc", "chinook", "taxi"), default=default)
    parser.add_argument("--people", type=Path)
    parser.add_argument("--knows", type=Path)
    parser.add_argument("--data", type=Path, help="Chinook SQLite or Taxi Parquet file")


def planner_options(parser, *, allow_dag=False):
    choices = ('keyword', 'llm', 'dag', 'llm-dag') if allow_dag else ('keyword', 'llm')
    parser.add_argument('--planner', choices=choices, default='keyword')
    parser.add_argument('--llm-fallback', choices=('none', 'keyword'), default='none',
                        help='Use keyword planner on LLM failures; never overrides model abstention/refusal')
    model_options(parser)


def model_options(parser):
    parser.add_argument('--llm-timeout', type=float, default=30, help='Model request timeout in seconds')
    parser.add_argument('--llm-base-url', help='OpenAI-compatible endpoint, e.g. http://localhost:11434/v1')
    parser.add_argument('--llm-api', choices=('responses','chat'), default='responses')
    parser.add_argument('--model',help='Explicit OpenAI or local model ID for model-assisted planning')


def main():
    parser = argparse.ArgumentParser(description="PolyQuery query routing and evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    dag_comparison = sub.add_parser("compare-dag", help="Compare keyword and DAG answers on the synthetic fixture")
    dag_comparison.add_argument("--dataset", type=Path, default=Path("benchmarks/dag-dev.json"))
    dag_comparison.add_argument("--output", type=Path)
    dag_comparison.add_argument("--repeats", type=int, default=1)
    model_options(dag_comparison)
    paired=sub.add_parser('compare-routers',help='Paired keyword/LLM evaluation on draft canonical labels')
    paired.add_argument('--pool',type=Path,default=Path('benchmarks/constraint-labels-v2'))
    paired.add_argument('--sources',type=Path,default=Path('benchmarks/router-sources.json'))
    paired.add_argument('--output',type=Path,required=True,help='New local report, preferably under ignored logs/')
    paired.add_argument('--repeats',type=int,default=1)
    paired.add_argument('--keyword-only',action='store_true',help='Explicit partial run when no LLM is available')
    paired.add_argument('--input-usd-per-million',type=float)
    paired.add_argument('--output-usd-per-million',type=float)
    planner_options(paired)
    constraint_eval=sub.add_parser('evaluate-constraints')
    constraint_eval.add_argument('--dataset',type=Path,default=Path('benchmarks/constraints-dev.json'))
    constraint_eval.add_argument('--output',type=Path)
    audit_parser=sub.add_parser('audit-constraint-labels')
    audit_parser.add_argument('--pool',type=Path,default=Path('benchmarks/constraint-labels-v2'))
    classify=sub.add_parser('classify-constraints')
    classify.add_argument('question')
    classify.add_argument('--source',choices=('synthetic','chinook','taxi','ldbc'),required=True)
    classify.add_argument('--model',required=True)
    classify.add_argument('--threshold',type=float,default=.5)
    query = sub.add_parser("query")
    query.add_argument("question")
    source_options(query)
    planner_options(query, allow_dag=True)
    query.add_argument("--policy", choices=("router", "fixed-sql", "fixed-code", "fixed-graph", "cost-aware"), default="router")
    query.add_argument("--cost-profile", type=Path)
    inspect = sub.add_parser("inspect-source")
    source_options(inspect, "ldbc")
    describe = sub.add_parser('describe-source')
    source_options(describe)
    describe.add_argument('--cost-profile', type=Path)
    for name in ("evaluate", "compare", "calibrate"):
        command = sub.add_parser(name)
        source_options(command)
        if name != 'calibrate':
            planner_options(command)
        command.add_argument("--dataset", type=Path, default=Path("benchmarks/demo.json"))
        command.add_argument("--output", type=Path)
        command.add_argument("--repeats", type=int, default=1)
        command.add_argument("--warmup", type=int, default=0)
        command.add_argument("--cost-profile", type=Path)
        command.add_argument("--oracle-repeats", type=int, default=0)
        if name == "evaluate":
            command.add_argument("--policy", choices=("router", "fixed-sql", "fixed-code", "fixed-graph", "cost-aware"), default="router")
        elif name == "compare":
            command.add_argument("--reverse-order", action="store_true")
    args = parser.parse_args()
    if args.command == "compare-dag":
        from .dag_evaluation import compare_dag_planners
        try:
            cases = json.loads(args.dataset.read_text(encoding="utf-8-sig"))
            model_kwargs = (dict(model=args.model, llm_api=args.llm_api, llm_base_url=args.llm_base_url,
                                 llm_timeout=args.llm_timeout) if args.model else None)
            result = compare_dag_planners(cases, repeats=args.repeats, model_options=model_kwargs)
            rendered = json.dumps(result, indent=2) + "\n"
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered, encoding="utf-8")
        except (OSError, ValueError) as error:
            parser.error(str(error))
        print(rendered if args.output is None else json.dumps(result["overall"], indent=2))
        return
    if args.command=='compare-routers':
        from .router_comparison import compare_routers,load_sources
        if args.llm_fallback!='none':
            parser.error('Paired comparisons disable keyword fallback')
        if args.output.exists():
            parser.error('Report exists; choose a new output path')
        try:
            result=compare_routers(
                json.loads((args.pool/'labels.json').read_text(encoding='utf-8-sig')),
                json.loads((args.pool/'splits.json').read_text(encoding='utf-8-sig')),
                load_sources(args.sources),model=args.model,repeats=args.repeats,keyword_only=args.keyword_only,
                input_rate=args.input_usd_per_million,output_rate=args.output_usd_per_million,
                llm_kwargs={'llm_base_url':args.llm_base_url,'llm_api':args.llm_api,'llm_timeout':args.llm_timeout})
            args.output.parent.mkdir(parents=True,exist_ok=True)
            with args.output.open('x',encoding='utf-8') as stream:
                stream.write(json.dumps(result,indent=2)+'\n')
        except (OSError,ValueError,KeyError,LLMPlanningError) as error:
            parser.error(str(error))
        print(json.dumps({'complete_comparison':result['complete_comparison'],'summaries':result['summaries'],
                          'output':str(args.output.resolve())},indent=2))
        return
    if args.command=='audit-constraint-labels':
        from .label_review import write_review_artifacts
        try:
            report=write_review_artifacts(args.pool)
        except (OSError,ValueError,KeyError) as error:
            parser.error(str(error))
        print(json.dumps(report,indent=2))
        if not report['consistency_passed']: parser.exit(1)
        return
    if args.command=='classify-constraints':
        from .jev import JevClassifier,JevClassificationError
        try:
            print(json.dumps(JevClassifier(args.model,threshold=args.threshold).classify(args.question,args.source),indent=2))
        except (ValueError,JevClassificationError) as error:
            parser.error(str(error))
        return
    if args.command=='evaluate-constraints':
        from .constraint_evaluation import evaluate_constraint_cases
        try:
            result=evaluate_constraint_cases(json.loads(args.dataset.read_text(encoding='utf-8-sig')))
            if args.output:
                args.output.parent.mkdir(parents=True,exist_ok=True)
                args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        except (OSError,ValueError) as error:
            parser.error(str(error))
        print(json.dumps(result,indent=2))
        return
    if getattr(args,'planner','keyword') in ('llm', 'llm-dag') and not args.model:
        parser.error('Model-assisted planning requires --model')
    if args.source == "ldbc" and (args.people is None or args.knows is None):
        parser.error("LDBC requires --people and --knows")
    if args.source in ("chinook", "taxi") and args.data is None:
        parser.error("Chinook and Taxi require --data")
    if getattr(args, "llm_fallback", "none") != "none" and args.planner != "llm":
        parser.error("--llm-fallback requires --planner llm")
    llm_kwargs = dict(llm_base_url=getattr(args,'llm_base_url',None), llm_api=getattr(args,'llm_api','responses'), llm_timeout=getattr(args,'llm_timeout',30))
    kwargs = {"source": args.source, "people_path": args.people, "edges_path": args.knows, "data_path": args.data}
    try:
        if args.command == "calibrate" and args.output and (args.output.parent / "profile-manifest.json").exists():
            parser.error("This profile directory is frozen; calibrate into a new experiment directory")
        if getattr(args, "dataset", None) and (args.dataset.parent / "manifest.json").exists():
            from .costs import verify_split_directory
            verify_split_directory(args.dataset.parent)
        if args.command == "compare":
            from .evaluation import evaluate_policies
            if args.source == "synthetic":
                parser.error("Comparison currently supports ldbc, chinook, and taxi")
            policies = ["router", "fixed-sql", "fixed-graph" if args.source == "ldbc" else "fixed-code"]
            if args.cost_profile:
                policies.insert(1, "cost-aware")
            dataset = json.loads(args.dataset.read_text(encoding="utf-8-sig"))
            factories = {policy: partial(Engine, **kwargs, policy=policy, cost_profile=args.cost_profile) for policy in policies}
            if args.planner == 'llm':
                factories['llm'] = partial(Engine, **kwargs, planner_kind='llm',model=args.model,llm_fallback=args.llm_fallback, **llm_kwargs)
                if args.cost_profile:
                    factories['llm-cost-aware'] = partial(Engine, **kwargs, planner_kind='llm', model=args.model,
                                                          policy='cost-aware', cost_profile=args.cost_profile,
                                                          llm_fallback=args.llm_fallback, **llm_kwargs)
            if args.reverse_order:
                factories = dict(reversed(list(factories.items())))
            result = evaluate_policies(factories, dataset,
                                       repeats=args.repeats, warmup=args.warmup, oracle_repeats=args.oracle_repeats)
        else:
            selected_policy = 'cost-aware' if args.command == 'describe-source' and args.cost_profile else getattr(args, "policy", "router")
            with Engine(**kwargs, policy=selected_policy, cost_profile=getattr(args, "cost_profile", None),
                        planner_kind=getattr(args,'planner','keyword'),model=getattr(args,'model',None),
                        llm_fallback=getattr(args,'llm_fallback','none'), **llm_kwargs) as engine:
                if args.command == "query":
                    result = engine.query(args.question)
                elif args.command == "inspect-source":
                    result = engine.source_metadata
                elif args.command == 'describe-source':
                    result = engine.catalog.to_dict()
                elif args.command == "calibrate":
                    from .costs import calibrate
                    result = calibrate(engine, json.loads(args.dataset.read_text(encoding="utf-8-sig")), repeats=args.repeats, warmup=args.warmup)
                else:
                    from .evaluation import evaluate
                    dataset = json.loads(args.dataset.read_text(encoding="utf-8-sig"))
                    result = evaluate(engine, dataset, repeats=args.repeats, warmup=args.warmup, policy_label=('llm-cost-aware' if args.policy == 'cost-aware' else 'llm') if args.planner=='llm' else args.policy, oracle_repeats=args.oracle_repeats)
        if getattr(args, "output", None):
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, LLMPlanningError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
