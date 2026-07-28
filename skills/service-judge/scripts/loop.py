#!/usr/bin/env python3
"""service-judge iteration loop (LOOP-DESIGN D1, D7).

Orchestrates repeated evals against a frozen golden set. The human fixes
between iterations; the loop measures (§2: no auto-fix, no auto-revert).

Usage:
  python loop.py --run .service-judge/run-<id>/

Expects <run>/config.json:
  {
    "probe_cmd": "curl -s ... {question} ... {qid} ...",   # placeholders; stdout = answer
    "golden_set": ".service-judge/questions.golden.jsonl",
    "golden_sha256": "<sha256 of that file>",
    "anchors": "<run>/raw/anchors.snapshot.json",          # optional; absent = unanchored
    "judge_model": "claude-fable-5",                        # pinned for the run (D5)
    "max_iterations": 5,
    "budget_tokens": 2000000,                               # optional; input+output across the run
    "poll_interval": 30
  }

Stop conditions (D7, all in code): gates passed / max_iterations /
stagnation (<2pp improvement in 2 consecutive iterations) / regression
(score dropped after a fix -> STOP and notify; reverting is the human's
call) / token budget exhausted.
"""
import argparse
import hashlib
import json
import pathlib
import shlex
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from providers.anthropic_api import AnthropicBatchScorer

RUBRIC_PATH = pathlib.Path(__file__).resolve().parent.parent / "references" / "rubric.md"


# ---------- pure logic (tested by test_loop.py) ----------

def compute_grade(verdicts: list[dict], questions: list[dict], judge: str,
                  degradations: list[str]) -> dict:
    """grade.json: per-question scores plus dev/holdout aggregates and gates."""
    split_of = {q["id"]: q.get("split", "dev") for q in questions}
    per_question, errors = [], []
    for v in verdicts:
        if "score" not in v:
            errors.append(v)
            continue
        per_question.append({"id": v["id"], "split": split_of.get(v["id"], "dev"),
                             "score": v["score"], "verdict": v["verdict"],
                             "unanchored": v.get("unanchored", False)})
    def agg(rows):
        maxpts = 5 * len(rows)
        total = sum(r["score"] for r in rows)
        return {"total": total, "max": maxpts,
                "percent": round(100 * total / maxpts) if maxpts else 0}
    dev = [r for r in per_question if r["split"] == "dev"]
    holdout = [r for r in per_question if r["split"] == "holdout"]
    grade = agg(per_question) | {
        "judge": judge, "per_question": per_question,
        "dev": agg(dev), "holdout": agg(holdout),
        "gap_pp": agg(dev)["percent"] - agg(holdout)["percent"] if holdout else None,
        # hard gate: the cross-analyst conditions (broken tools, hallucinated
        # narratives, false guardrails) land here when step 7 joins the loop
        "hard_gate": all(r["score"] > 1 for r in per_question) and not errors,
        "soft_gate": (sum(1 for r in per_question if r["score"] >= 4)
                      >= 0.95 * len(per_question)) if per_question else False,
        "degradations": degradations + [f"{len(errors)} scoring errors"] * bool(errors),
    }
    return grade


def should_stop(history: list[dict], max_iterations: int,
                budget_tokens: int | None, spent_tokens: int) -> tuple[bool, str]:
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
    if budget_tokens is not None and spent_tokens >= budget_tokens:
        return True, f"BUDGET: {spent_tokens} tokens spent >= {budget_tokens}"
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
    anchors = ""
    anchors_path = cfg.get("anchors")
    if anchors_path and pathlib.Path(anchors_path).exists():
        anchors = pathlib.Path(anchors_path).read_text(encoding="utf-8")
    else:
        degradations.append("no ground truth: anchors file absent")

    rubric = RUBRIC_PATH.read_text(encoding="utf-8")
    scorer = AnthropicBatchScorer(model=cfg["judge_model"],
                                  poll_interval=cfg.get("poll_interval", 30))
    history_path = args.run / "history.json"
    history = json.loads(history_path.read_text()) if history_path.exists() else []
    spent = sum(h.get("tokens", 0) for h in history)
    max_iter = cfg.get("max_iterations", 5)

    while True:
        n = len(history) + 1
        iter_dir = args.run / f"iter-{n:02d}"
        (iter_dir / "raw").mkdir(parents=True, exist_ok=True)
        print(f"[iter {n}] probing {len(questions)} questions...", file=sys.stderr)
        pack = probe(questions, cfg["probe_cmd"], cfg.get("probe_timeout", 120))
        (iter_dir / "raw" / "pack.jsonl").write_text(
            "\n".join(json.dumps(r) for r in pack), encoding="utf-8")

        print(f"[iter {n}] scoring with {cfg['judge_model']}...", file=sys.stderr)
        verdicts = scorer.score(pack, anchors, rubric)
        (iter_dir / "verdicts.json").write_text(json.dumps(verdicts, indent=2))

        grade = compute_grade(verdicts, questions, cfg["judge_model"], degradations)
        grade["tokens"] = sum(scorer.last_usage.values())
        (iter_dir / "grade.json").write_text(json.dumps(grade, indent=2))
        history.append(grade)
        history_path.write_text(json.dumps(history, indent=2))
        spent += grade["tokens"]

        # D4: per-question detail only for dev; holdout stays aggregate
        dev_fails = [r for r in grade["per_question"]
                     if r["split"] == "dev" and r["score"] < 4]
        print(f"[iter {n}] dev {grade['dev']['percent']}% · "
              f"holdout {grade['holdout']['percent']}% · gap {grade['gap_pp']}pp · "
              f"hard_gate={'OK' if grade['hard_gate'] else 'FAIL'} · "
              f"dev questions <4: {[r['id'] for r in dev_fails]}", file=sys.stderr)

        stop, reason = should_stop(history, max_iter, cfg.get("budget_tokens"), spent)
        if stop:
            print(f"[loop] STOP after iter {n}: {reason}", file=sys.stderr)
            print(json.dumps({"iterations": n, "reason": reason, "final": grade["percent"],
                              "dev": grade["dev"]["percent"],
                              "holdout": grade["holdout"]["percent"]}))
            return 0
        input(f"[loop] apply your fix, then press Enter for iter {n + 1} "
              "(Ctrl-C to abandon)... ")


if __name__ == "__main__":
    raise SystemExit(main())
