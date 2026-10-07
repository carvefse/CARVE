#!/usr/bin/env python3
"""CARVE's single command-line entry point."""
import argparse
import json
import sys
import re
import yaml

from evidence import assess, save
from runner import run_attempts
from scenario import resolve
from plan_view import format_plan


def main():
    parser = argparse.ArgumentParser(description="CARVE: controlled and validated AAOS experiments")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "plan", "run"):
        sub = commands.add_parser(name)
        sub.add_argument("scenario")
        if name == "plan":
            sub.add_argument("--output", help="save the full JSON plan; also print the readable preview")
        if name == "run":
            sub.add_argument("--serial", required=True)
            sub.add_argument("--output", required=True)
            sub.add_argument("--adb", default="adb")
            sub.add_argument("--trace-processor")
            sub.add_argument("--repeat", type=int, default=1)
    check = commands.add_parser("check")
    check.add_argument("directory")
    check.add_argument("--trace-processor")
    args = parser.parse_args()
    try:
        if args.command == "check":
            result = assess(args.directory, args.trace_processor)
            print(json.dumps(result, indent=2))
            return 0 if result["analysis_eligible"] else 2
        plan = resolve(args.scenario)
        if args.command == "validate":
            print("Validation passed.")
        elif args.command == "plan":
            if args.output:
                save(args.output, plan)
            print(format_plan(plan))
            if args.output:
                print(f"\nFull JSON plan saved to: {args.output}")
        else:
            results = run_attempts(plan, args.output, args.serial, args.adb, args.trace_processor, args.repeat)
            return 0 if len(results) == args.repeat and all(r["analysis_eligible"] for r in results) else 2
        return 0
    except (ValueError, KeyError, TypeError, OSError, re.error, yaml.YAMLError) as exc:
        print(f"CARVE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
