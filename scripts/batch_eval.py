#!/usr/bin/env python3
"""Batch judging harness for service-judge (experimental, terminal-only).

Judges an answer pack against anchors via the Anthropic Batches API
(-50% cost) with prompt caching on the shared rubric+anchors prefix.
Thin CLI over scripts/providers/anthropic_api.py.

Usage:
  python batch_eval.py --pack pack.jsonl --anchors anchors.md [--model claude-fable-5]
  python batch_eval.py --pack pack.jsonl --anchors anchors.md --dry-run   # no API call
  python batch_eval.py --poll <batch_id>                                  # fetch results

Note: without the service's tool catalog in context, the tool_choice dimension is scored from the pack's tools_called field alone (degraded).
Note: prompt caching only kicks in past the model's minimum cacheable prefix (~2k tokens); small anchor sets simply skip the cache (no error).
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from providers import anthropic_api

DEFAULT_RUBRIC = pathlib.Path(__file__).resolve().parent.parent / "references" / "rubric.md"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pack", type=pathlib.Path)
    ap.add_argument("--anchors", type=pathlib.Path)
    ap.add_argument("--model", default="claude-fable-5")
    ap.add_argument("--rubric", type=pathlib.Path, default=DEFAULT_RUBRIC,
                    help="rubric file inlined in every request (default: references/rubric.md)")
    ap.add_argument("--dry-run", action="store_true", help="print requests, no API call")
    ap.add_argument("--poll", metavar="BATCH_ID", help="fetch results of a submitted batch")
    args = ap.parse_args()

    if args.poll:
        status, verdicts = anthropic_api.fetch(args.poll)
        print(f"status: {status}", file=sys.stderr)
        if status != "ended":
            return 1
        for verdict in verdicts:
            print(json.dumps(verdict))
        return 0

    if not args.pack or not args.anchors:
        ap.error("--pack and --anchors are required (unless --poll)")
    items = anthropic_api.load_pack(args.pack.read_text(encoding="utf-8"))
    requests = anthropic_api.build_requests(
        items, args.anchors.read_text(encoding="utf-8"),
        args.rubric.read_text(encoding="utf-8"), args.model)
    if args.dry_run:
        print(json.dumps(requests, indent=2))
        print(f"{len(requests)} requests built (dry run, nothing sent)", file=sys.stderr)
        return 0

    batch_id = anthropic_api.submit(requests)
    print(f"submitted batch {batch_id} with {len(requests)} requests", file=sys.stderr)
    print(f"poll with: python {sys.argv[0]} --poll {batch_id}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
