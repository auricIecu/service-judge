#!/usr/bin/env python3
"""Batch judging harness for service-judge (experimental, terminal-only).

Judges an answer pack against anchors via the Anthropic Batches API
(-50% cost) with prompt caching on the shared rubric+anchors prefix.

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

# Keep in sync with the rubric table in references/judging.md — the
# interactive and batch paths must judge with identical criteria.
RUBRIC = """Score this answer 0-5 using these dimensions:
- tool_choice (0-1): appropriate tool/path called for the question
- accuracy (0-2): numbers/facts match the anchor (no anchor: plausibility, max 1, mark unanchored)
- hallucination (0-1): no invented numbers AND no invented interpretation
- directness (0-1): answers the actual question, usable by a real user
An honest "I don't have that data" on a question whose anchor is none/trap is a good answer.
Score first; only then write the improvement comment."""

VERDICT_TOOL = {
    "name": "verdict",
    "description": "Submit the judgment for one answer.",
    "input_schema": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "score": {"type": "number", "minimum": 0, "maximum": 5},
            "verdict": {"type": "string", "enum": ["pass", "warn", "fail"]},
            "unanchored": {"type": "boolean"},
            "improvement_comment": {"type": "string"},
        },
        "required": ["id", "score", "verdict", "unanchored", "improvement_comment"],
    },
}


def build_requests(pack_path: pathlib.Path, anchors_path: pathlib.Path, model: str) -> list[dict]:
    anchors = anchors_path.read_text(encoding="utf-8")
    shared_prefix = [
        {"type": "text", "text": RUBRIC},
        {"type": "text", "text": f"GROUND-TRUTH ANCHORS:\n{anchors}",
         "cache_control": {"type": "ephemeral"}},
    ]
    requests = []
    for line in pack_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        case = (f"QUESTION {rec['id']} (mode: {rec['mode']}):\n{rec['question']}\n\n"
                f"TOOLS CALLED: {rec.get('tools_called')}\nERROR: {rec.get('error')}\n\n"
                f"ANSWER:\n{rec['answer']}")
        requests.append({
            "custom_id": rec["id"],
            "params": {
                "model": model,
                "max_tokens": 1024,
                "system": shared_prefix,
                "tools": [VERDICT_TOOL],
                "tool_choice": {"type": "tool", "name": "verdict"},
                "messages": [{"role": "user", "content": case}],
            },
        })
    return requests


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pack", type=pathlib.Path)
    ap.add_argument("--anchors", type=pathlib.Path)
    ap.add_argument("--model", default="claude-fable-5")
    ap.add_argument("--dry-run", action="store_true", help="print requests, no API call")
    ap.add_argument("--poll", metavar="BATCH_ID", help="fetch results of a submitted batch")
    args = ap.parse_args()

    if args.poll:
        import anthropic
        client = anthropic.Anthropic()
        batch = client.messages.batches.retrieve(args.poll)
        print(f"status: {batch.processing_status}", file=sys.stderr)
        if batch.processing_status != "ended":
            return 1
        for result in client.messages.batches.results(args.poll):
            if result.result.type == "succeeded":
                for block in result.result.message.content:
                    if block.type == "tool_use":
                        print(json.dumps(block.input))
            else:
                print(json.dumps({"id": result.custom_id, "error": result.result.type, "detail": getattr(getattr(result.result, "error", None), "message", None)}))
        return 0

    if not args.pack or not args.anchors:
        ap.error("--pack and --anchors are required (unless --poll)")
    requests = build_requests(args.pack, args.anchors, args.model)
    if args.dry_run:
        print(json.dumps(requests, indent=2))
        print(f"{len(requests)} requests built (dry run, nothing sent)", file=sys.stderr)
        return 0

    import anthropic
    client = anthropic.Anthropic()
    batch = client.messages.batches.create(requests=requests)
    print(f"submitted batch {batch.id} with {len(requests)} requests", file=sys.stderr)
    print(f"poll with: python {sys.argv[0]} --poll {batch.id}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
