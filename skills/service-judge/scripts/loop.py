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
    "schema_version": 2,
    "probe_cmd": "curl -s ... {question} ... {qid} ...",   # placeholders; stdout = answer
    "golden_set": ".service-judge/questions.golden.jsonl",
    "golden_sha256": "<sha256 of that file>",
    "anchors": "<run>/raw/anchors.snapshot.json",          # optional; absent = unanchored
    "judge": "codex",                                      # metadata only
    "goals": {"profile": "recommended-production-v1", ...},
    "max_iterations": 5
  }

Stop conditions: gates passed / max_iterations / stagnation (<2pp improvement
in 2 consecutive iterations) / regression. Harness session limits are the
only LLM limits; this script never calls a model API.
"""
import argparse
import hashlib
import json
import os
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
DEFAULT_GOALS = {
    "profile": "recommended-production-v1",
    "min_tool_choice_pct": 95,
    "min_accuracy_pct": 95,
    "min_hallucination_free_pct": 100,
    "min_directness_pct": 95,
    "min_pass_rate_pct": 95,
    "min_holdout_score_pct": 95,
    "max_dev_holdout_gap_pp": 5,
    "min_anchor_coverage_pct": 50,
}
MIN_GOAL_KEYS = (
    "min_tool_choice_pct", "min_accuracy_pct", "min_hallucination_free_pct",
    "min_directness_pct", "min_pass_rate_pct", "min_holdout_score_pct",
    "min_anchor_coverage_pct",
)
DIMENSION_VALUES = {
    "tool_choice": {0, 50, 100},
    "accuracy": {0, 100, 200},
    "hallucination_free": {0, 100},
    "directness": {0, 100},
}
AUTONOMY_ACTIONS = (
    "edit_product_code", "run_tests", "restart_local", "deploy_staging", "commit",
)


# ---------- pure logic (tested by test_loop.py) ----------

def hundredths(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(round(value * 100))


def verdict_for(score: int) -> str:
    if score >= 400:
        return "pass"
    if score >= 250:
        return "warn"
    return "fail"


def is_anchored(anchors: dict | None, qid: str) -> bool:
    row = anchors.get(qid) if isinstance(anchors, dict) else None
    return isinstance(row, dict) and row.get("anchor") is not None


def percent(points: float, max_points: float) -> int | None:
    return round(100 * points / max_points) if max_points else None


def goals_with_defaults(goals: dict | None) -> dict:
    return DEFAULT_GOALS | (goals or {})


def goal_result(metric: str, target, actual, met: bool) -> dict:
    return {"metric": metric, "target": target, "actual": actual, "met": met}


def evaluate_goals(metrics: dict, goals: dict | None,
                   anchored_dev: int, anchored_holdout: int,
                   anchored_total: int) -> dict:
    goals = goals_with_defaults(goals)
    detail = []
    for key, metric in (
        ("min_tool_choice_pct", "tool_choice_pct"),
        ("min_accuracy_pct", "accuracy_pct"),
        ("min_hallucination_free_pct", "hallucination_free_pct"),
        ("min_directness_pct", "directness_pct"),
        ("min_pass_rate_pct", "pass_rate_pct"),
        ("min_holdout_score_pct", "holdout_score_pct"),
        ("min_anchor_coverage_pct", "anchor_coverage_pct"),
    ):
        actual = metrics.get(metric)
        detail.append(goal_result(metric, goals[key], actual,
                                  actual is not None and actual >= goals[key]))
    gap = metrics.get("gap_pp")
    detail.append(goal_result("dev_holdout_gap_pp", goals["max_dev_holdout_gap_pp"],
                              gap, gap is not None and gap <= goals["max_dev_holdout_gap_pp"]))
    detail.append(goal_result("anchored_dev_questions", 1, anchored_dev, anchored_dev >= 1))
    detail.append(goal_result("anchored_holdout_questions", 1, anchored_holdout,
                              anchored_holdout >= 1))
    detail.append(goal_result("certifiable_accuracy", "at least one anchor",
                              anchored_total, anchored_total >= 1))
    return {"met": all(row["met"] for row in detail), "detail": detail}


def compute_grade(verdicts: list[dict], questions: list[dict], judge: str,
                  degradations: list[str],
                  cross_analysis: list[dict] | None = None,
                  goals: dict | None = None,
                  anchors: dict | None = None) -> dict:
    """grade.json: per-question scores plus dev/holdout aggregates and gates."""
    split_of = {q["id"]: q.get("split", "dev") for q in questions}
    per_question, errors, seen = [], [], set()
    for v in verdicts:
        qid = v.get("id")
        if qid not in split_of or qid in seen:
            errors.append(v)
            continue
        seen.add(qid)
        score = hundredths(v.get("score"))
        dimensions = v.get("dimensions")
        unanchored = not is_anchored(anchors, qid)
        if (score is None or score < 0 or score > 500
                or not isinstance(dimensions, dict)
                or set(dimensions) != set(DIMENSION_VALUES)
                or any(hundredths(dimensions[key]) not in allowed
                       for key, allowed in DIMENSION_VALUES.items())
                or sum(hundredths(dimensions[key]) for key in DIMENSION_VALUES) != score
                or not isinstance(v.get("unanchored"), bool)
                or v["unanchored"] != unanchored
                or unanchored and hundredths(dimensions["accuracy"]) > 100
                or not isinstance(v.get("improvement_comment"), str)
                or any(not isinstance(v.get(flag), bool) for flag in CRITICAL_FLAGS)):
            errors.append({"id": qid, "error": "invalid_verdict"})
            continue
        per_question.append({"id": qid, "split": split_of[qid],
                             "score": score / 100, "verdict": verdict_for(score),
                             "unanchored": unanchored,
                             "dimensions": dimensions,
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
    def agg(rows, null_empty=False):
        maxpts = 5 * len(rows)
        total = sum(r["score"] for r in rows)
        return {"total": total, "max": maxpts,
                "percent": percent(total, maxpts) if maxpts else (None if null_empty else 0)}
    anchored = [r for r in per_question if not r["unanchored"]]
    unanchored_rows = [r for r in per_question if r["unanchored"]]
    dev = [r for r in anchored if r["split"] == "dev"]
    holdout = [r for r in anchored if r["split"] == "holdout"]
    metrics = {
        "accuracy_pct": percent(sum(r["dimensions"]["accuracy"] for r in anchored),
                                2 * len(anchored)),
        "tool_choice_pct": percent(sum(r["dimensions"]["tool_choice"] for r in per_question),
                                   len(per_question)),
        "hallucination_free_pct": percent(
            sum(r["dimensions"]["hallucination_free"] for r in per_question),
            len(per_question)),
        "directness_pct": percent(sum(r["dimensions"]["directness"] for r in per_question),
                                  len(per_question)),
        "pass_rate_pct": percent(sum(1 for r in anchored if r["score"] >= 4),
                                 len(anchored)),
        "anchor_coverage_pct": percent(len(anchored), len(questions)) or 0,
    }
    hard_failures = [
        {"id": r["id"], "flags": [flag for flag in CRITICAL_FLAGS if r[flag]]}
        for r in per_question if any(r[flag] for flag in CRITICAL_FLAGS)
    ]
    dev_agg = agg(dev, True)
    holdout_agg = agg(holdout, True)
    gap = (dev_agg["percent"] - holdout_agg["percent"]
           if dev_agg["percent"] is not None and holdout_agg["percent"] is not None
           else None)
    metrics["holdout_score_pct"] = holdout_agg["percent"]
    metrics["gap_pp"] = gap
    grade = agg(per_question) | {
        "judge": judge, "per_question": per_question,
        "dev": dev_agg, "holdout": holdout_agg,
        "gap_pp": gap,
        **metrics,
        "unanchored_block": {
            "count": len(unanchored_rows),
            "percent": percent(len(unanchored_rows), len(questions)) or 0,
            "dimensions_pct": {
                "tool_choice": percent(sum(r["dimensions"]["tool_choice"]
                                           for r in unanchored_rows), len(unanchored_rows)),
                "accuracy": percent(sum(r["dimensions"]["accuracy"]
                                        for r in unanchored_rows), len(unanchored_rows)),
                "hallucination_free": percent(sum(r["dimensions"]["hallucination_free"]
                                                  for r in unanchored_rows),
                                              len(unanchored_rows)),
                "directness": percent(sum(r["dimensions"]["directness"]
                                          for r in unanchored_rows), len(unanchored_rows)),
            },
        },
        "hard_failures": hard_failures,
        "cross_analysis": cross_findings,
        "hard_gate": (all(r["score"] > 1 for r in per_question)
                      and not hard_failures and not cross_findings
                      and not errors and not cross_errors),
        "goals": evaluate_goals(metrics, goals, len(dev), len(holdout), len(anchored)),
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
    if "goals" not in last:
        return True, "INVALID_GRADE: missing goals"
    if last["hard_gate"] and last["goals"]["met"]:
        return True, "PASSED: hard gate and goals met"
    if (len(fulls) >= 2
            and isinstance(last["dev"]["percent"], (int, float))
            and isinstance(fulls[-2]["dev"]["percent"], (int, float))
            and last["dev"]["percent"] < fulls[-2]["dev"]["percent"]):
        return True, ("REGRESSION: dev score dropped "
                      f"{fulls[-2]['dev']['percent']} -> {last['dev']['percent']} "
                      "after the last fix. Reverting is your call; the loop only measures.")
    if len(fulls) >= 3:
        points = [fulls[i]["dev"]["percent"] for i in (-3, -2, -1)]
        deltas = [points[i] - points[i - 1] for i in (1, 2)
                  if isinstance(points[i], (int, float))
                  and isinstance(points[i - 1], (int, float))]
        if len(deltas) == 2 and all(d < 2 for d in deltas):
            return True, f"STAGNATION: <2pp improvement in 2 consecutive iterations {deltas}"
    if len(history) >= max_iterations:
        return True, f"MAX_ITERATIONS: {max_iterations} reached"
    return False, ""


def validate_config(cfg: dict, n_golden: int) -> list[str]:
    errors = []
    goals = cfg.get("goals")
    if not isinstance(goals, dict):
        errors.append("goals must be an object")
    else:
        if "profile" in goals and not isinstance(goals["profile"], str):
            errors.append("goals.profile must be a string")
        for key in MIN_GOAL_KEYS:
            value = goals.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
                errors.append(f"goals.{key} must be an integer 0-100")
        value = goals.get("max_dev_holdout_gap_pp")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            errors.append("goals.max_dev_holdout_gap_pp must be an integer >= 0")
    autonomy = cfg.get("autonomy")
    if autonomy is not None:
        if not isinstance(autonomy, dict):
            errors.append("autonomy must be an object")
        else:
            if (isinstance(autonomy.get("mode"), bool)
                    or autonomy.get("mode") not in ("manual", "autopilot")):
                errors.append("autonomy.mode must be manual or autopilot")
            for key in AUTONOMY_ACTIONS:
                if not isinstance(autonomy.get(key), bool):
                    errors.append(f"autonomy.{key} must be a boolean")
            if autonomy.get("mode") == "autopilot":
                # ponytail: only the two actions the design cannot work without.
                # Testing/restarting/staging stay optional — a service with no
                # test suite must not have to lie in its audit record.
                for key in ("edit_product_code", "commit"):
                    if autonomy.get(key) is not True:
                        errors.append(f"autonomy.{key} must be true for autopilot")
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


def load_anchors(cfg: dict) -> tuple[dict | None, list[str]]:
    anchors_path = cfg.get("anchors")
    if not anchors_path:
        return None, ["no ground truth: anchors file absent"]
    path = pathlib.Path(anchors_path)
    if not path.exists():
        return None, ["no ground truth: anchors file absent"]
    try:
        anchors = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return None, [f"no ground truth: anchors snapshot malformed ({type(exc).__name__})"]
    if not isinstance(anchors, dict) or any(not isinstance(row, dict)
                                           for row in anchors.values()):
        return None, ["no ground truth: anchors snapshot malformed"]
    return anchors, []


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


def build_fix_brief(verdicts: list[dict], questions: list[dict], grade: dict,
                    regressed: list[str], authorization: dict) -> dict:
    """Return the validated, dev-only input for an autopilot fixer.

    Carries the authorized repo and action map: the fixer is the only
    participant that touches the machine, and the brief is all it receives."""
    split_of = {q["id"]: q.get("split", "dev") for q in questions}
    valid = {row["id"]: row for row in grade["per_question"]}
    dev_ids = {qid for qid, split in split_of.items() if split == "dev"}
    dev = [
        {"id": v["id"], "score": valid[v["id"]]["score"],
         "improvement_comment": v["improvement_comment"]}
        for v in verdicts
        if v.get("id") in valid and v["id"] in dev_ids
        and valid[v["id"]]["score"] < 4
    ]
    return {
        "repo": authorization["repo"],
        "allowed_actions": authorization["allowed_actions"],
        "dev": dev,
        "regressed_ids": [qid for qid in regressed if qid in dev_ids],
        "cross_analysis": [
            {key: finding[key] for key in ("type", "ids", "comment")}
            for finding in grade["cross_analysis"]
            if all(qid in dev_ids for qid in finding["ids"])
        ],
        "holdout": {"percent": grade["holdout"]["percent"],
                    "gap_pp": grade["gap_pp"]},
        "gates": {"hard_gate": grade["hard_gate"],
                  "goals_met": grade.get("goals", {}).get("met")},
    }


def brief_is_actionable(brief: dict) -> bool:
    return any(brief[key] for key in ("dev", "regressed_ids", "cross_analysis"))


def git_preflight_decision(repo_present: bool, tree_clean: bool,
                           filesystem_writable: bool,
                           branch_creatable: bool) -> tuple[bool, str]:
    if not repo_present:
        return False, "authorized repo is not a git repository"
    if not tree_clean:
        return False, "authorized repo has a dirty product tree"
    if not filesystem_writable:
        return False, "authorized repo filesystem is not writable"
    if not branch_creatable:
        return False, "service-judge run branch cannot be created from this checkout"
    return True, ""


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

def load_authorization(path: pathlib.Path, autonomy: dict) -> tuple[dict | None, str]:
    message = ("authorization.json is required as an audit record; it does not grant "
               "authority. Obtain explicit authorization in this conversation or use "
               "manual mode.")
    try:
        authorization = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None, message
    allowed_actions = (authorization.get("allowed_actions")
                       if isinstance(authorization, dict) else None)
    if (not isinstance(authorization, dict)
            or any(not isinstance(authorization.get(key), str)
                   or not authorization[key]
                   for key in ("timestamp", "scope", "repo", "environment",
                               "approved_text"))
            or not isinstance(allowed_actions, dict)
            or any(not isinstance(allowed_actions.get(key), bool)
                   for key in AUTONOMY_ACTIONS)
            or allowed_actions != {
                key: autonomy[key] for key in AUTONOMY_ACTIONS
            }):
        return None, "authorization.json is incomplete or does not match config autonomy"
    return authorization, ""


def collect_git_preflight(repo: pathlib.Path, branch: str) -> tuple[bool, bool, bool, bool]:
    def git(*command):
        return subprocess.run(["git", "-C", str(repo), *command],
                              capture_output=True, text=True)

    inside = git("rev-parse", "--is-inside-work-tree")
    repo_present = inside.returncode == 0 and inside.stdout.strip() == "true"
    if not repo_present:
        return False, False, os.access(repo, os.W_OK), False
    current = git("symbolic-ref", "--quiet", "--short", "HEAD")
    current_branch = current.stdout.strip() if current.returncode == 0 else ""
    status_args = ["status", "--porcelain", "--untracked-files=normal"]
    if current_branch == branch:
        status_args += ["--", ".", ":(exclude).service-judge"]
    status = git(*status_args)
    tree_clean = status.returncode == 0 and not status.stdout.strip()
    filesystem_writable = os.access(repo, os.W_OK)
    git_dir = git("rev-parse", "--path-format=absolute", "--git-dir")
    git_common = git("rev-parse", "--path-format=absolute", "--git-common-dir")
    linked_worktree = (git_dir.returncode != 0 or git_common.returncode != 0
                       or git_dir.stdout.strip() != git_common.stdout.strip())
    refs_writable = (git_common.returncode == 0
                     and os.access(git_common.stdout.strip(), os.W_OK))
    valid = git("check-ref-format", "--branch", branch)
    exists = git("show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
    branch_creatable = (not linked_worktree and refs_writable and valid.returncode == 0
                        and (current_branch == branch
                             or bool(current_branch) and exists.returncode != 0))
    return repo_present, tree_clean, filesystem_writable, branch_creatable


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
                reserved: int | None, history: list[dict],
                autopilot_preflight: str = "not_applicable") -> dict:
    output = {
        "status": "plan", "iteration": iteration, "strategy": strategy,
        "spent": spent, "available": available, "reserved": reserved,
        "full": is_full, "probed": len(selected), "reason": reason,
        "certification": is_full,
        "autopilot_preflight": autopilot_preflight,
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
    if cfg.get("schema_version") != 2:
        print(json.dumps({
            "status": "unsupported_schema",
            "expected": 2,
            "actual": cfg.get("schema_version"),
            "message": "Start a new service-judge-loop run with schema_version 2.",
        }))
        return 2

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

    autonomy = cfg.get("autonomy") or {"mode": "manual"}
    autopilot = autonomy["mode"] == "autopilot"
    if autopilot:
        authorization, auth_error = load_authorization(
            args.run / "authorization.json", autonomy)
        if auth_error:
            print(json.dumps({"status": "autopilot_blocked", "reason": auth_error,
                              "manual_available": True}))
            return 2
        branch = f"service-judge/{args.run.name}"
        ok, reason = git_preflight_decision(
            *collect_git_preflight(pathlib.Path(authorization["repo"]), branch))
        if not ok:
            print(json.dumps({"status": "autopilot_blocked", "reason": reason,
                              "manual_available": True}))
            return 2

    # The run's ground-truth snapshot belongs here: raw/ is the only path
    # .gitignore protects, and anchors quote real customer data.
    (args.run / "raw").mkdir(parents=True, exist_ok=True)
    anchors, degradations = load_anchors(cfg)
    anchors_path = pathlib.Path(cfg["anchors"]) if cfg.get("anchors") and anchors else None

    history_path = args.run / "history.json"
    history = json.loads(history_path.read_text()) if history_path.exists() else []
    max_iter = cfg.get("max_iterations", 5)
    if history:
        stop, reason = should_stop(history, max_iter)
        if stop:
            if reason.startswith("INVALID_GRADE"):
                print(json.dumps({"status": "invalid_state", "error": reason}))
                return 2
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
                                     reserved, history,
                                     "passed" if autopilot else "not_applicable")))
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
        degradations, cross_analysis, cfg.get("goals"), anchors,
    )
    validation_errors = [d for d in grade["degradations"] if d.endswith(" errors")]
    if validation_errors:
        print(json.dumps({"status": "invalid_judgment", "iteration": n,
                          "errors": validation_errors}))
        return 2

    grade["full"] = selection["full"]
    grade["probed"] = len(selection["selected_ids"])
    if not grade["full"]:
        grade.pop("goals", None)
    (iter_dir / "grade.json").write_text(json.dumps(grade, indent=2))
    previous = latest_score_map(history)
    history.append(grade)
    history_path.write_text(json.dumps(history, indent=2))
    dev_fails = [r["id"] for r in grade["per_question"]
                 if r["split"] == "dev" and r["score"] < 4]
    stop, reason = should_stop(history, max_iter)
    if not stop and not grade["full"] and grade["hard_gate"] and not dev_fails:
        reason = ("FOCUSED PASSED: the targeted questions pass, but a partial "
                  "evaluation cannot certify. Run again for the full evaluation.")
    regressed = regressed_ids(grade["per_question"], previous)
    dev_ids = {q["id"] for q in selected if q.get("split", "dev") == "dev"}
    dev_regressed = [qid for qid in regressed if qid in dev_ids]
    fix_brief_path = None
    if not stop and autopilot:
        brief = build_fix_brief(verdicts, selected, grade, regressed, authorization)
        if grade["full"] and not brief_is_actionable(brief):
            stop = True
            reason = ("NOTHING_ACTIONABLE: all remaining failures are holdout-only "
                      "or mixed dev/holdout groups, which the fixer cannot see by "
                      "design; the next step belongs to the human.")
        else:
            fix_brief_path = iter_dir / "fix-brief.json"
            fix_brief_path.write_text(json.dumps(brief, indent=2), encoding="utf-8")
    if not stop and not reason:
        reason = (f"NEEDS_FIX: {len(dev_fails)} dev questions below 4; "
                  f"{len(dev_regressed)} dev regressions.")
    output = {
        "status": "stopped" if stop else "needs_fix", "iteration": n,
        "reason": reason, "grade": grade["percent"],
        "dev": grade["dev"]["percent"],
        "holdout": holdout_percent(grade),
        "full": grade["full"], "probed": grade["probed"],
        "hard_gate": grade["hard_gate"],
        "dev_questions_below_4": dev_fails,
        "regressed_ids": regressed,
    }
    if fix_brief_path:
        output["fix_brief"] = str(fix_brief_path.resolve())
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
