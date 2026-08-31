#!/usr/bin/env python3
"""Harness-only service-judge iteration loop.

The script probes and grades; the active Claude Code or Codex harness judges.
Call it once to prepare a pack, let the harness write verdicts.json and
cross-analysis.json, then call it again to finalize the iteration.

Usage:
  python loop.py --run .service-judge/run-<id>/
  python loop.py --run .service-judge/run-<id>/ --plan

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
        score = v.get("score")
        if ("score" not in v
                or isinstance(score, bool)
                or not isinstance(score, (int, float))
                or score < 0 or score > 5
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
    fulls = [h for h in history if h.get("full", True)]
    last = fulls[-1] if fulls else None
    if last is None:
        if len(history) >= max_iterations:
            return True, f"MAX_ITERATIONS: {max_iterations} reached"
        return False, ""
    if last["hard_gate"] and last["soft_gate"]:
        return True, "PASSED: hard and soft gates met"
    if len(fulls) >= 2 and last["dev"]["percent"] < fulls[-2]["dev"]["percent"]:
        return True, ("REGRESSION: dev score dropped "
                      f"{fulls[-2]['dev']['percent']} -> {last['dev']['percent']} "
                      "after the last fix. Reverting is your call; the loop only measures.")
    if len(fulls) >= 3:
        deltas = [fulls[i]["dev"]["percent"] - fulls[i - 1]["dev"]["percent"]
                  for i in (-2, -1)]
        if all(d < 2 for d in deltas):
            return True, f"STAGNATION: <2pp improvement in 2 consecutive iterations {deltas}"
    if len(history) >= max_iterations:
        return True, f"MAX_ITERATIONS: {max_iterations} reached"
    return False, ""


def validate_config(cfg: dict, n_golden: int) -> list[str]:
    errors = []
    strategy = cfg.get("probe_strategy", "full")
    if strategy not in ("full", "adaptive"):
        errors.append(f"unknown probe_strategy: {strategy}")
        return errors
    if strategy != "adaptive":
        return errors
    focused = cfg.get("focused_max_questions", 10)
    regression = cfg.get("regression_sample", 3)
    budget = cfg.get("answer_budget")
    if isinstance(focused, bool) or not isinstance(focused, int) or focused < 1:
        errors.append("focused_max_questions must be an integer >= 1")
    if isinstance(regression, bool) or not isinstance(regression, int) or regression < 0:
        errors.append("regression_sample must be a non-negative integer")
    if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
        errors.append("answer_budget must be a positive integer for adaptive")
    elif budget < 2 * n_golden:
        errors.append("answer_budget must fit the baseline and final full run")
    return errors


def budget_plan(probed_count: int, n_golden: int, cfg: dict) -> tuple[int, int, int]:
    reserved = n_golden
    return probed_count, cfg["answer_budget"] - probed_count - reserved, reserved


def latest_score_map(history: list[dict]) -> dict[str, float]:
    scores = {}
    for grade in history:
        for row in grade.get("per_question", []):
            if isinstance(row.get("score"), (int, float)) and not isinstance(row.get("score"), bool):
                scores[row["id"]] = row["score"]
    return scores


def regressed_ids(per_question: list[dict], previous: dict[str, float]) -> list[str]:
    """Questions that were passing at their last measurement and now are not.
    A question with no prior score cannot have regressed — it is a first read."""
    return [r["id"] for r in per_question
            if r["id"] in previous and previous[r["id"]] >= 4 and r["score"] < 4]


def select_questions(questions: list[dict], history: list[dict], cfg: dict,
                     iteration: int) -> tuple[list[dict], bool, str]:
    strategy = cfg.get("probe_strategy", "full")
    if strategy == "full":
        return questions, True, "full_strategy"

    max_iterations = cfg.get("max_iterations", 5)
    fulls = [h for h in history if h.get("full", True)]
    if not fulls:
        return questions, True, "no_full_baseline"
    if iteration == max_iterations:
        return questions, True, "final_iteration"

    latest = latest_score_map(history)
    q_by_id = {q["id"]: q for q in questions}
    dev = [q for q in questions if q.get("split", "dev") == "dev"]
    failures = [q for q in dev if latest.get(q["id"], 5) < 4]
    if not failures:
        return questions, True, "no_dev_failures"

    cap = cfg.get("focused_max_questions", 10)
    if len(failures) > cap:
        return questions, True, "failures_exceed_focus"

    selected, selected_ids = [], set()

    def add(q: dict) -> None:
        if q["id"] not in selected_ids:
            selected.append(q)
            selected_ids.add(q["id"])

    for q in failures:
        add(q)

    for finding in fulls[-1].get("cross_analysis", []):
        ids = finding.get("ids")
        if not isinstance(ids, list) or not ids:
            continue
        group = [q_by_id.get(qid) for qid in ids]
        if any(q is None or q.get("split", "dev") != "dev" for q in group):
            continue
        missing = [q for q in group if q["id"] not in selected_ids]
        if len(selected) + len(missing) <= cap:
            for q in missing:
                add(q)

    failure_keys = {(q.get("mode"), q.get("type", "")) for q in failures}
    for q in dev:
        if len(selected) >= cap:
            break
        if (q.get("mode"), q.get("type", "")) in failure_keys:
            add(q)

    passing = [q for q in dev if latest.get(q["id"], 5) >= 4 and q["id"] not in selected_ids]
    if passing:
        offset = (iteration - 1) % len(passing)
        rotated = passing[offset:] + passing[:offset]
        for q in rotated[:cfg.get("regression_sample", 3)]:
            if len(selected) >= cap:
                break
            add(q)

    spent, available, _ = budget_plan(cfg.get("_probed_count", 0), len(questions), cfg)
    if len(selected) > available:
        return questions, True, "focused_exceeds_budget"
    return selected, False, "focused"


# ---------- side effects ----------

def count_probed_rows(run_dir: pathlib.Path) -> int:
    total = 0
    for pack_path in sorted(run_dir.glob("iter-*/raw/pack.jsonl")):
        total += sum(1 for line in pack_path.read_text(encoding="utf-8").splitlines()
                     if line.strip())
    return total


def read_pack_ids(pack_path: pathlib.Path) -> list[str]:
    return [json.loads(line)["id"] for line in pack_path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def selection_payload(selected: list[dict], is_full: bool, reason: str,
                      strategy: str) -> dict:
    return {"selected_ids": [q["id"] for q in selected], "full": is_full,
            "reason": reason, "strategy": strategy}


def selected_from_payload(selection: dict, questions: list[dict]) -> tuple[list[dict], list[str]]:
    q_by_id = {q["id"]: q for q in questions}
    selected, missing = [], []
    for qid in selection.get("selected_ids", []):
        q = q_by_id.get(qid)
        if q is None:
            missing.append(qid)
        else:
            selected.append(q)
    return selected, missing


def holdout_percent(grade: dict) -> int | None:
    return grade["holdout"]["percent"] if grade["holdout"]["max"] else None


def plan_output(iteration: int, strategy: str, selected: list[dict], is_full: bool,
                reason: str, spent: int, available: int | None,
                reserved: int | None, history: list[dict]) -> dict:
    output = {
        "status": "plan", "iteration": iteration, "strategy": strategy,
        "spent": spent, "available": available, "reserved": reserved,
        "full": is_full, "probed": len(selected), "reason": reason,
        "certification": is_full,
    }
    if is_full:
        splits = {}
        for q in selected:
            split = q.get("split", "dev")
            splits[split] = splits.get(split, 0) + 1
        output["split_counts"] = splits
    else:
        output["selected_ids"] = [q["id"] for q in selected]
    return output


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
    ap.add_argument("--plan", action="store_true", help="print the next probe plan")
    args = ap.parse_args()
    cfg = json.loads((args.run / "config.json").read_text())

    golden_path = pathlib.Path(cfg["golden_set"])
    golden_bytes = golden_path.read_bytes()
    if hashlib.sha256(golden_bytes).hexdigest() != cfg["golden_sha256"]:
        print("FATAL: golden set sha256 mismatch — the exam changed mid-run (D3)",
              file=sys.stderr)
        return 2
    questions = [json.loads(l) for l in golden_bytes.decode().splitlines() if l.strip()]
    errors = validate_config(cfg, len(questions))
    if errors:
        print(json.dumps({"status": "invalid_config", "errors": errors}))
        return 2

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
                              "holdout": holdout_percent(last)}))
            return 0

    n = len(history) + 1
    iter_dir = args.run / f"iter-{n:02d}"
    raw_dir = iter_dir / "raw"
    pack_path = raw_dir / "pack.jsonl"
    selection_path = iter_dir / "selection.json"
    verdicts_path = iter_dir / "verdicts.json"
    cross_path = iter_dir / "cross-analysis.json"
    strategy = cfg.get("probe_strategy", "full")
    cfg_for_selection = dict(cfg)
    if strategy == "adaptive":
        cfg_for_selection["_probed_count"] = count_probed_rows(args.run)

    selection_existed = selection_path.exists()
    if selection_existed:
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        selected, missing = selected_from_payload(selection, questions)
        if missing:
            print(json.dumps({"status": "invalid_state", "iteration": n,
                              "error": "selection references unknown ids",
                              "ids": missing}))
            return 2
    else:
        selected, is_full, reason = select_questions(questions, history,
                                                     cfg_for_selection, n)
        selection = selection_payload(selected, is_full, reason, strategy)

    if args.plan:
        spent, available, reserved = (
            budget_plan(cfg_for_selection.get("_probed_count", 0), len(questions), cfg)
            if strategy == "adaptive" else (count_probed_rows(args.run), None, None)
        )
        print(json.dumps(plan_output(n, strategy, selected, selection["full"],
                                     selection["reason"], spent, available,
                                     reserved, history)))
        return 0

    if selection_existed and not pack_path.exists():
        print(json.dumps({
            "status": "in_progress", "iteration": n,
            "message": (f"{selection_path} exists but {pack_path} does not. "
                        f"Another probe may be running; delete {selection_path} "
                        "to retry this iteration."),
        }))
        return 0

    iter_dir.mkdir(parents=True, exist_ok=True)
    if not selection_existed:
        try:
            with selection_path.open("x", encoding="utf-8") as f:
                json.dump(selection, f, indent=2)
        except FileExistsError:
            print(json.dumps({
                "status": "in_progress", "iteration": n,
                "message": (f"{selection_path} already exists. Another probe may be "
                            f"running; delete {selection_path} to retry this iteration."),
            }))
            return 0
    raw_dir.mkdir(parents=True, exist_ok=True)

    if not pack_path.exists():
        print(f"[iter {n}] probing {len(selected)} questions...", file=sys.stderr)
        pack = probe(selected, cfg["probe_cmd"], cfg.get("probe_timeout", 120))
        pack_path.write_text(
            "\n".join(json.dumps(r) for r in pack), encoding="utf-8")
    pack_ids = read_pack_ids(pack_path)
    if pack_ids != selection["selected_ids"]:
        print(json.dumps({"status": "invalid_state", "iteration": n,
                          "error": "selection.json and pack.jsonl ids differ",
                          "selection_ids": selection["selected_ids"],
                          "pack_ids": pack_ids}))
        return 2

    if not verdicts_path.exists() or not cross_path.exists():
        print(json.dumps({
            "status": "needs_judgment", "iteration": n,
            "full": selection["full"], "selected_ids": selection["selected_ids"],
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
        verdicts, selected, cfg.get("judge", "current harness"),
        degradations, cross_analysis,
    )
    validation_errors = [d for d in grade["degradations"] if d.endswith(" errors")]
    if validation_errors:
        print(json.dumps({"status": "invalid_judgment", "iteration": n,
                          "errors": validation_errors}))
        return 2

    grade["full"] = selection["full"]
    grade["probed"] = len(selection["selected_ids"])
    (iter_dir / "grade.json").write_text(json.dumps(grade, indent=2))
    previous = latest_score_map(history)
    history.append(grade)
    history_path.write_text(json.dumps(history, indent=2))
    stop, reason = should_stop(history, max_iter)
    if not stop and not grade["full"] and grade["hard_gate"] and grade["soft_gate"]:
        reason = ("FOCUSED PASSED: the targeted questions pass, but a partial "
                  "evaluation cannot certify. Run again for the full evaluation.")
    dev_fails = [r["id"] for r in grade["per_question"]
                 if r["split"] == "dev" and r["score"] < 4]
    regressed = regressed_ids(grade["per_question"], previous)
    print(json.dumps({
        "status": "stopped" if stop else "needs_fix", "iteration": n,
        "reason": reason, "grade": grade["percent"],
        "dev": grade["dev"]["percent"],
        "holdout": holdout_percent(grade),
        "full": grade["full"], "probed": grade["probed"],
        "hard_gate": grade["hard_gate"],
        "dev_questions_below_4": dev_fails,
        "regressed_ids": regressed,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
