"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .config import ConfigBundle
from .contracts import SchemaRegistry
from .demo import create_demo_clients
from .errors import ConfigurationError, StateTransitionError
from .notifications import (
    CompletionEmailOutbox,
    configured_recipient,
)
from .providers import create_host_provider_pair, create_provider
from .sourcing import (
    SourceService,
    create_source_adapter,
    source_status,
    validate_run_input,
)
from .util import load_json
from .workflow import AnalysisWorkflow


def _root(value: Optional[str]) -> Path:
    return Path(value or ".").resolve()


def _host(value: str) -> str:
    if value != "auto":
        return value
    if os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_CI"):
        return "codex"
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude"
    raise ConfigurationError(
        "cannot detect host; pass --host codex, --host claude, or --host api"
    )


def _notification_recipient(
    root: Path,
    email_to: Optional[str],
) -> str:
    config = ConfigBundle.load(root)
    return (
        email_to.strip()
        if email_to is not None
        else configured_recipient(config.workflow)
    )


def command_demo(args: argparse.Namespace) -> int:
    root = _root(args.root)
    producer, validator = create_demo_clients()
    workflow = AnalysisWorkflow(
        root,
        producer,
        validator,
        notification_recipient=_notification_recipient(root, args.email_to),
    )
    document = load_json(root / "tests" / "fixtures" / "demo_observations.json")
    if args.revision is not None:
        result = workflow.run(
            document,
            run_revision=args.revision,
        )
    else:
        for revision in range(1, 1000):
            try:
                result = workflow.run(
                    document,
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
    document = load_json(Path(args.input).resolve())
    result = _execute_model_workflow(
        root,
        args,
        document,
        args.input_profile,
    )
    print(json.dumps(result, indent=2))
    return 0


def _provider_pair(args: argparse.Namespace) -> tuple:
    host = _host(args.host)
    if host == "api":
        if not args.producer or not args.validator:
            raise ConfigurationError(
                "--host api requires --producer and --validator"
            )
        producer = create_provider(args.producer)
        validator = create_provider(args.validator)
    else:
        if args.producer or args.validator:
            raise ConfigurationError(
                "--producer/--validator are only valid with --host api"
            )
        producer, validator = create_host_provider_pair(host)
    return producer, validator


def _execute_model_workflow(
    root: Path,
    args: argparse.Namespace,
    document: Dict[str, Any],
    input_profile: str,
) -> Dict[str, Any]:
    degraded_reasons = validate_run_input(root, document, input_profile)
    producer, validator = _provider_pair(args)
    workflow = AnalysisWorkflow(
        root,
        producer,
        validator,
        notification_recipient=_notification_recipient(root, args.email_to),
        initial_degraded_reasons=degraded_reasons,
    )
    return workflow.run(
        document,
        run_revision=args.revision,
    )


def command_source(args: argparse.Namespace) -> int:
    root = _root(args.root)
    adapter = create_source_adapter(root, args.provider)
    output_path = Path(args.output).resolve() if args.output else None
    result = SourceService(root, adapter).run(
        args.session_date,
        analysis_window=args.analysis_window,
        output_path=output_path,
    )
    metadata = result["document"]["source_snapshot"]
    print(
        json.dumps(
            {
                "snapshot_id": metadata["snapshot_id"],
                "coverage_status": metadata["coverage_status"],
                "coverage": metadata["coverage"],
                "enrichments": metadata["enrichments"],
                "snapshot_path": result["snapshot_path"],
                "output_path": result["output_path"],
                "heartbeat_status": result["heartbeat"]["status"],
            },
            indent=2,
        )
    )
    return 0


def command_source_status(args: argparse.Namespace) -> int:
    result = source_status(_root(args.root))
    print(json.dumps(result, indent=2))
    failed = (
        args.require_analysis_ready and not result["analysis_allowed"]
    ) or (
        args.require_full_coverage and not result["research_source_complete"]
    )
    return int(bool(failed))


def command_analyze_live_data(args: argparse.Namespace) -> int:
    root = _root(args.root)
    adapter = create_source_adapter(root, args.provider)
    sourced = SourceService(root, adapter).run(
        args.session_date,
        analysis_window=args.analysis_window,
    )
    analysis = _execute_model_workflow(
        root,
        args,
        sourced["document"],
        "production",
    )
    print(
        json.dumps(
            {
                "source_snapshot": sourced["snapshot_path"],
                "source_coverage_status": sourced["document"]["source_snapshot"][
                    "coverage_status"
                ],
                "source_enrichments": sourced["document"]["source_snapshot"][
                    "enrichments"
                ],
                "analysis": analysis,
            },
            indent=2,
        )
    )
    return 0


def command_email_ack(args: argparse.Namespace) -> int:
    root = _root(args.root)
    registry = SchemaRegistry(root / "schemas")
    item = CompletionEmailOutbox(root, registry).acknowledge(
        args.operation_id,
        args.provider_message_id,
    )
    print(json.dumps(item, indent=2))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    root = _root(args.root)
    registry = SchemaRegistry(root / "schemas")
    registry.validate(args.schema, load_json(Path(args.path).resolve()))
    print("%s: valid %s" % (args.path, args.schema))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stocktrend")
    parser.add_argument("--root", help="repository root; defaults to current directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("demo", help="run the offline cross-vendor demo")
    demo.add_argument(
        "--revision",
        type=int,
        help="explicit run revision; defaults to the first compatible revision",
    )
    demo.add_argument(
        "--email-to",
        help="completion recipient; empty disables delivery request",
    )
    demo.set_defaults(function=command_demo)

    run = subparsers.add_parser("run", help="run with real model providers")
    run.add_argument("--input", required=True)
    run.add_argument(
        "--host",
        choices=["auto", "codex", "claude", "api"],
        default="auto",
        help="subscription host, auto-detected in Codex/Claude; api is legacy",
    )
    run.add_argument("--producer", choices=["openai", "anthropic"])
    run.add_argument("--validator", choices=["openai", "anthropic"])
    run.add_argument("--revision", type=int, default=1)
    run.add_argument(
        "--input-profile",
        choices=["production", "test"],
        default="production",
        help="production requires a fresh signed-off source snapshot; test is research-only",
    )
    run.add_argument(
        "--email-to",
        help="completion recipient; empty disables delivery request",
    )
    run.set_defaults(function=command_run)

    source = subparsers.add_parser(
        "source",
        help="build a point-in-time market-data snapshot for research",
    )
    source.add_argument("--session-date", required=True)
    source.add_argument("--analysis-window", default="close")
    source.add_argument(
        "--provider",
        help="must match production.approved_adapter; defaults to that adapter",
    )
    source.add_argument("--output")
    source.set_defaults(function=command_source)

    source_status_parser = subparsers.add_parser(
        "source-status",
        help="check sourcer heartbeat, coverage readiness, and dead-man state",
    )
    source_status_parser.add_argument(
        "--require-analysis-ready",
        action="store_true",
        help="exit nonzero when the heartbeat is missing, stale, running, or failed",
    )
    source_status_parser.add_argument(
        "--require-full-coverage",
        action="store_true",
        help="exit nonzero unless the heartbeat is fresh and every coverage gate passed",
    )
    source_status_parser.set_defaults(function=command_source_status)

    live_analysis = subparsers.add_parser(
        "analyze-live-data",
        help="source current data and run the research workflow",
    )
    live_analysis.add_argument("--session-date", required=True)
    live_analysis.add_argument("--analysis-window", default="close")
    live_analysis.add_argument(
        "--provider",
        help="must match production.approved_adapter; defaults to that adapter",
    )
    live_analysis.add_argument(
        "--host",
        choices=["auto", "codex", "claude", "api"],
        default="auto",
    )
    live_analysis.add_argument("--producer", choices=["openai", "anthropic"])
    live_analysis.add_argument("--validator", choices=["openai", "anthropic"])
    live_analysis.add_argument("--revision", type=int, default=1)
    live_analysis.add_argument("--email-to")
    live_analysis.set_defaults(function=command_analyze_live_data)

    validate = subparsers.add_parser("validate", help="validate a JSON contract")
    validate.add_argument("--schema", required=True)
    validate.add_argument("path")
    validate.set_defaults(function=command_validate)

    email_ack = subparsers.add_parser(
        "email-ack",
        help="acknowledge a connector-delivered completion email",
    )
    email_ack.add_argument("--operation-id", required=True)
    email_ack.add_argument("--provider-message-id")
    email_ack.set_defaults(function=command_email_ack)
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
