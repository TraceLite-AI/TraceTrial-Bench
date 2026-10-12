from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness_tracer.runner import RunConfig, TraceRunner


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run(args, parser)
    parser.error("missing command")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tracer")
    subparsers = parser.add_subparsers(dest="command")
    run = subparsers.add_parser("run", help="run a harness and collect a trace")
    run.add_argument("--harness", required=True)
    run.add_argument("--model", required=True)
    run.add_argument("--input-query", required=True)
    run.add_argument("--output-dir", required=True, type=Path)
    run.add_argument("--harness-path")
    run.add_argument("--timeout", type=int)
    run.add_argument("--session-id")
    run.add_argument("--extra-arg", action="append", default=[])
    return parser


def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if not args.input_query:
        print("error: input query must not be empty", file=sys.stderr)
        return 2
    try:
        extra_args = _parse_extra_args(args.extra_arg)
        summary = TraceRunner().run(
            RunConfig(
                harness=args.harness,
                model=args.model,
                input_query=args.input_query,
                output_dir=args.output_dir,
                harness_path=args.harness_path,
                timeout=args.timeout,
                session_id=args.session_id,
                extra_args=extra_args,
            )
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: harness executable not found: {exc.filename}", file=sys.stderr)
        return 127

    print(summary["summary_path"])
    return 0 if summary["status"] == "finished" else 1


def _parse_extra_args(values: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"extra-arg must be KEY=VALUE: {value}")
        key, arg_value = value.split("=", 1)
        if not key:
            raise ValueError(f"extra-arg key must not be empty: {value}")
        parsed[key] = arg_value
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
