#!/usr/bin/env python3
"""Harness-only service-judge iteration loop.

The script probes and grades; the active Claude Code or Codex harness judges.
Call it once to prepare a pack, let the harness write verdicts.json and
cross-analysis.json, then call it again to finalize the iteration.

Usage:
  python loop.py --run .service-judge/run-<id>/

Expects <run>/config.json:
  {
    "probe_cmd": "curl -s ... {question} ... {qid} ...",   # placeholders; stdout = answer
    "golden_set": ".service-judge/questions.golden.jsonl",
    "golden_sha256": "<sha256 of that file>",
    "anchors": "<run>/raw/anchors.snapshot.json",          # optional; absent = unanchored
    "judge": "codex",                                      # metadata only
    "max_iterations": 5
  }

Stop conditions: gates passed / max_iterations / stagnation (<2pp improvement
in 2 consecutive iterations) / regression. Harness session limits are the
only LLM limits; this script never calls a model API.
"""
import argparse
import hashlib
import json
import pathlib
import shlex
import subprocess
import sys

RUBRIC_PATH = pathlib.Path(__file__).resolve().parent.parent / "references" / "rubric.md"
CRITICAL_FLAGS = ("broken_tool", "hallucinated_narrative", "false_guardrail")
CROSS_TYPES = {
    "contradiction", "broken_tool", "hallucinated_narrative",
    "false_guardrail", "arithmetic_inconsistency",
}


# ---------- pure logic (tested by test_loop.py) ----------

def compute_grade(verdicts: list[dict], questions: list[dict], judge: str,
                  degradations: list[str],
                  cross_analysis: list[dict] | None = None) -> dict:
    """grade.json: per-question scores plus dev/holdout aggregates and gates."""
    split_of = {q["id"]: q.get("split", "dev") for q in questions}
    per_question, errors, seen = [], [], set()
    for v in verdicts:
        qid = v.get("id")
        if qid not in split_of or qid in seen:
            errors.append(v)
            continue
        seen.add(qid)
        if ("score" not in v
                or any(not isinstance(v.get(flag), bool) for flag in CRITICAL_FLAGS)):
            errors.append(v)
            continue
        per_question.append({"id": qid, "split": split_of[qid],
                             "score": v["score"], "verdict": v["verdict"],
                             "unanchored": v.get("unanchored", False),
                             **{flag: v[flag] for flag in CRITICAL_FLAGS}})
    errors.extend({"id": qid, "error": "missing_verdict"}
                  for qid in split_of.keys() - seen)
    cross_findings, cross_errors = [], []
    if cross_analysis is None:
        cross_errors.append({"error": "missing_cross_analysis"})
    else:
        for finding in cross_analysis:
            if (finding.get("type") not in CROSS_TYPES
                    or not isinstance(finding.get("ids"), list)
                    or not finding["ids"]
                    or any(qid not in split_of for qid in finding["ids"])
                    or not isinstance(finding.get("comment"), str)):
                cross_errors.append(finding)
            else:
                cross_findings.append(finding)
    def agg(rows):
        maxpts = 5 * len(rows)
        total = sum(r["score"] for r in rows)
        return {"total": total, "max": maxpts,
                "percent": round(100 * total / maxpts) if maxpts else 0}
    dev = [r for r in per_question if r["split"] == "dev"]
    holdout = [r for r in per_question if r["split"] == "holdout"]
    hard_failures = [
        {"id": r["id"], "flags": [flag for flag in CRITICAL_FLAGS if r[flag]]}
        for r in per_question if any(r[flag] for flag in CRITICAL_FLAGS)
    ]
    grade = agg(per_question) | {
        "judge": judge, "per_question": per_question,
        "dev": agg(dev), "holdout": agg(holdout),
        "gap_pp": agg(dev)["percent"] - agg(holdout)["percent"] if holdout else None,
        "hard_failures": hard_failures,
        "cross_analysis": cross_findings,
        "hard_gate": (all(r["score"] > 1 for r in per_question)
                      and not hard_failures and not cross_findings
                      and not errors and not cross_errors),
        "soft_gate": (sum(1 for r in per_question if r["score"] >= 4)
                      >= 0.95 * len(per_question)) if per_question else False,
        "degradations": (
            degradations
            + [f"{len(errors)} scoring errors"] * bool(errors)
            + [f"{len(cross_errors)} cross-analysis errors"] * bool(cross_errors)
        ),
    }
    return grade


def should_stop(history: list[dict], max_iterations: int) -> tuple[bool, str]:
    """history = list of grade dicts, oldest first. Returns (stop, reason)."""
    last = history[-1]
    if last["hard_gate"] and last["soft_gate"]:
        return True, "PASSED: hard and soft gates met"
    if len(history) >= 2 and last["dev"]["percent"] < history[-2]["dev"]["percent"]:
        return True, ("REGRESSION: dev score dropped "
                      f"{history[-2]['dev']['percent']} -> {last['dev']['percent']} "
                      "after the last fix. Reverting is your call; the loop only measures.")
    if len(history) >= 3:
        deltas = [history[i]["dev"]["percent"] - history[i - 1]["dev"]["percent"]
                  for i in (-2, -1)]
        if all(d < 2 for d in deltas):
            return True, f"STAGNATION: <2pp improvement in 2 consecutive iterations {deltas}"
    if len(history) >= max_iterations:
        return True, f"MAX_ITERATIONS: {max_iterations} reached"
    return False, ""


# ---------- side effects ----------

def probe(questions: list[dict], probe_cmd: str, timeout: int = 120) -> list[dict]:
    # shell=True is the contract: probe_cmd is a shell template authored by the
    # operator in their own config.json (same trust as a Makefile). The
    # LLM-generated values interpolated into it are shlex-quoted, so question
    # text can't inject shell syntax.
    pack = []
    for q in questions:
        cmd = probe_cmd.format(question=shlex.quote(q["question"]),
                               qid=shlex.quote(f"eval-{q['id']}"))
        try:
            out = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                 timeout=timeout)
            pack.append({"id": q["id"], "mode": q["mode"], "question": q["question"],
                         "answer": out.stdout, "tools_called": None,
                         "error": out.stderr.strip() or None if out.returncode else None})
        except subprocess.TimeoutExpired:
            pack.append({"id": q["id"], "mode": q["mode"], "question": q["question"],
                         "answer": "", "tools_called": None, "error": "probe timeout"})
    return pack


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", type=pathlib.Path, required=True, help="run directory")
    args = ap.parse_args()
    cfg = json.loads((args.run / "config.json").read_text())

    golden_path = pathlib.Path(cfg["golden_set"])
    golden_bytes = golden_path.read_bytes()
    if hashlib.sha256(golden_bytes).hexdigest() != cfg["golden_sha256"]:
        print("FATAL: golden set sha256 mismatch — the exam changed mid-run (D3)",
              file=sys.stderr)
        return 2
    questions = [json.loads(l) for l in golden_bytes.decode().splitlines() if l.strip()]

    degradations = []
    anchors_path = cfg.get("anchors")
    if anchors_path and pathlib.Path(anchors_path).exists():
        anchors_path = pathlib.Path(anchors_path)
    else:
        anchors_path = None
        degradations.append("no ground truth: anchors file absent")

    history_path = args.run / "history.json"
    history = json.loads(history_path.read_text()) if history_path.exists() else []
    max_iter = cfg.get("max_iterations", 5)
    if history:
        stop, reason = should_stop(history, max_iter)
        if stop:
            last = history[-1]
            print(json.dumps({"status": "stopped", "iterations": len(history),
                              "reason": reason, "final": last["percent"],
                              "dev": last["dev"]["percent"],
                              "holdout": last["holdout"]["percent"]}))
            return 0

    n = len(history) + 1
    iter_dir = args.run / f"iter-{n:02d}"
    raw_dir = iter_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    pack_path = raw_dir / "pack.jsonl"
    verdicts_path = iter_dir / "verdicts.json"
    cross_path = iter_dir / "cross-analysis.json"

    if not pack_path.exists():
        print(f"[iter {n}] probing {len(questions)} questions...", file=sys.stderr)
        pack = probe(questions, cfg["probe_cmd"], cfg.get("probe_timeout", 120))
        pack_path.write_text(
            "\n".join(json.dumps(r) for r in pack), encoding="utf-8")

    if not verdicts_path.exists() or not cross_path.exists():
        print(json.dumps({
            "status": "needs_judgment", "iteration": n,
            "pack": str(pack_path.resolve()),
            "anchors": str(anchors_path.resolve()) if anchors_path else None,
            "rubric": str(RUBRIC_PATH.resolve()),
            "write_verdicts": str(verdicts_path.resolve()),
            "write_cross_analysis": str(cross_path.resolve()),
        }))
        return 0

    try:
        verdicts = json.loads(verdicts_path.read_text())
        cross_analysis = json.loads(cross_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"status": "invalid_judgment", "iteration": n,
                          "error": type(exc).__name__}))
        return 2
    if not isinstance(verdicts, list) or not isinstance(cross_analysis, list):
        print(json.dumps({"status": "invalid_judgment", "iteration": n,
                          "error": "verdicts and cross-analysis must be arrays"}))
        return 2

    grade = compute_grade(
        verdicts, questions, cfg.get("judge", "current harness"),
        degradations, cross_analysis,
    )
    validation_errors = [d for d in grade["degradations"] if d.endswith(" errors")]
    if validation_errors:
        print(json.dumps({"status": "invalid_judgment", "iteration": n,
                          "errors": validation_errors}))
        return 2

    (iter_dir / "grade.json").write_text(json.dumps(grade, indent=2))
    history.append(grade)
    history_path.write_text(json.dumps(history, indent=2))
    stop, reason = should_stop(history, max_iter)
    dev_fails = [r["id"] for r in grade["per_question"]
                 if r["split"] == "dev" and r["score"] < 4]
    print(json.dumps({
        "status": "stopped" if stop else "needs_fix", "iteration": n,
        "reason": reason, "grade": grade["percent"],
        "dev": grade["dev"]["percent"],
        "holdout": grade["holdout"]["percent"],
        "hard_gate": grade["hard_gate"],
        "dev_questions_below_4": dev_fails,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
