"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .config import ConfigBundle
from .contracts import SchemaRegistry
from .demo import create_demo_clients
from .errors import StateTransitionError
from .execution import ApprovalService, PaperBroker, RiskEngine
from .providers import create_provider
from .util import atomic_write_json, load_json
from .workflow import AnalysisWorkflow


def _root(value: Optional[str]) -> Path:
    return Path(value or ".").resolve()


def command_demo(args: argparse.Namespace) -> int:
    root = _root(args.root)
    producer, validator = create_demo_clients()
    workflow = AnalysisWorkflow(root, producer, validator)
    document = load_json(root / "tests" / "fixtures" / "demo_observations.json")
    if args.revision is not None:
        result = workflow.run(
            document,
            execution_mode=args.mode,
            run_revision=args.revision,
        )
    else:
        for revision in range(1, 1000):
            try:
                result = workflow.run(
                    document,
                    execution_mode=args.mode,
                    run_revision=revision,
                )
                break
            except StateTransitionError as exc:
                if "dependencies changed" not in str(exc):
                    raise
        else:
            raise RuntimeError("no available demo revision")
    print(json.dumps(result, indent=2))
    return 0


def command_run(args: argparse.Namespace) -> int:
    root = _root(args.root)
    producer = create_provider(args.producer)
    validator = create_provider(args.validator)
    workflow = AnalysisWorkflow(root, producer, validator)
    result = workflow.run(
        load_json(Path(args.input).resolve()),
        execution_mode=args.mode,
        run_revision=args.revision,
    )
    print(json.dumps(result, indent=2))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    root = _root(args.root)
    registry = SchemaRegistry(root / "schemas")
    registry.validate(args.schema, load_json(Path(args.path).resolve()))
    print("%s: valid %s" % (args.path, args.schema))
    return 0


def command_paper_execute(args: argparse.Namespace) -> int:
    root = _root(args.root)
    registry = SchemaRegistry(root / "schemas")
    config = ConfigBundle.load(root)
    run_dir = root / "state" / "runs" / args.run_id
    proposals = load_json(
        run_dir / "validation" / "signal_proposals.json"
    )["signal_proposals"]
    eligible = [item for item in proposals if item["execution_eligible"]]
    if not eligible:
        raise ValueError("run has no execution-eligible proposals")
    proposal = next(
        (item for item in eligible if item["signal_id"] == args.signal_id),
        eligible[0] if args.signal_id is None else None,
    )
    if proposal is None:
        raise ValueError("signal not found or not eligible")
    facts = load_json(run_dir / "normalized" / "facts_block.json")["facts"]
    quote = next(
        fact
        for fact in facts
        if fact["fact_type"] == "quote"
        and fact["instrument_id"] == proposal["instrument_id"]
    )
    as_of = args.as_of or proposal["decision_at"]
    intent = RiskEngine(config.risk, registry).create_intent(
        proposal,
        quote,
        {
            "buying_power_usd": args.buying_power,
            "position_notional_usd": 0.0,
            "portfolio_notional_usd": 0.0,
        },
        as_of=as_of,
        environment="paper",
    )
    path = root / "state" / "executions" / ("%s.json" % intent["intent_id"])
    if path.exists():
        print(
            json.dumps(
                {
                    "intent_id": intent["intent_id"],
                    "path": str(path),
                    "reused": True,
                },
                indent=2,
            )
        )
        return 0
    approval = ApprovalService(config.approval, registry).decide(
        intent,
        args.approver,
        approved=True,
        decided_at=as_of,
    )
    result = PaperBroker(registry).execute(intent, approval, as_of=as_of)
    output = {
        "intent": intent,
        "approval": approval,
        "entry_events": result.entry_events,
        "protective_exit_events": result.protective_exit_events,
    }
    atomic_write_json(path, output)
    print(
        json.dumps(
            {
                "intent_id": intent["intent_id"],
                "path": str(path),
                "reused": False,
            },
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stocktrend")
    parser.add_argument("--root", help="repository root; defaults to current directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("demo", help="run the offline cross-vendor demo")
    demo.add_argument("--mode", choices=["analysis_only", "paper"], default="analysis_only")
    demo.add_argument(
        "--revision",
        type=int,
        help="explicit run revision; defaults to the first compatible revision",
    )
    demo.set_defaults(function=command_demo)

    run = subparsers.add_parser("run", help="run with real model providers")
    run.add_argument("--input", required=True)
    run.add_argument("--producer", choices=["openai", "anthropic"], required=True)
    run.add_argument("--validator", choices=["openai", "anthropic"], required=True)
    run.add_argument("--mode", choices=["analysis_only", "paper"], default="analysis_only")
    run.add_argument("--revision", type=int, default=1)
    run.set_defaults(function=command_run)

    validate = subparsers.add_parser("validate", help="validate a JSON contract")
    validate.add_argument("--schema", required=True)
    validate.add_argument("path")
    validate.set_defaults(function=command_validate)

    paper = subparsers.add_parser(
        "paper-execute",
        help="human-approved paper execution for a finalized run",
    )
    paper.add_argument("--run-id", required=True)
    paper.add_argument("--signal-id")
    paper.add_argument("--approver", required=True)
    paper.add_argument("--buying-power", type=float, default=10000.0)
    paper.add_argument("--as-of")
    paper.set_defaults(function=command_paper_execute)
    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.function(args))
    except Exception as exc:
        print("%s: %s" % (exc.__class__.__name__, exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
