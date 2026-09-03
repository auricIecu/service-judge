#!/usr/bin/env python3
"""Self-check for loop.py's pure logic. Run: python3 scripts/test_loop.py"""
import contextlib
import hashlib
import io
import json
import pathlib
import shlex
import subprocess
import sys
import tempfile

import loop
from loop import (
    budget_plan,
    compute_grade,
    count_probed_rows,
    select_questions,
    should_stop,
    validate_config,
)

QS = [{"id": "Q1", "mode": "sales", "split": "dev", "question": "?"},
      {"id": "Q2", "mode": "sales", "split": "holdout", "question": "?"}]
ANCHORS = {"Q1": {"anchor": "one"}, "Q2": {"anchor": "two"}}
GOALS = {"profile": "test", "min_tool_choice_pct": 95, "min_accuracy_pct": 95,
         "min_hallucination_free_pct": 100, "min_directness_pct": 95,
         "min_pass_rate_pct": 95, "min_holdout_score_pct": 95,
         "max_dev_holdout_gap_pp": 5, "min_anchor_coverage_pct": 50}


def check(_name, condition):
    assert condition


def g(dev_pct, hard=False, goals_met=False, full=True):
    return {"percent": dev_pct, "dev": {"percent": dev_pct},
            "hard_gate": hard, "goals": {"met": goals_met}, "full": full}


def v(qid, score, verdict=None, dimensions=None, unanchored=False, **critical):
    if dimensions is None:
        dimensions = {"tool_choice": 1, "accuracy": 2,
                      "hallucination_free": 1, "directness": 1}
    row = {"id": qid, "score": score, "dimensions": dimensions,
           "unanchored": unanchored, "improvement_comment": "",
           **{flag: critical.get(flag, False) for flag in (
               "broken_tool", "hallucinated_narrative", "false_guardrail")}}
    if verdict is not None:
        row["verdict"] = verdict
    return row


# compute_grade: aggregates, splits, gates
grade = compute_grade([v("Q1", 5), v("Q2", 1, dimensions={
                          "tool_choice": 0, "accuracy": 0,
                          "hallucination_free": 0, "directness": 1})],
                      QS, "m", [], [], GOALS, ANCHORS)
assert grade["total"] == 6 and grade["max"] == 10 and grade["percent"] == 60
assert grade["dev"]["percent"] == 100 and grade["holdout"]["percent"] == 20
assert grade["gap_pp"] == 80
assert not grade["hard_gate"]          # a score <=1 breaks the hard gate
assert not grade["goals"]["met"]

perfect = compute_grade([v("Q1", 5), v("Q2", 5)],
                        QS, "m", [], [], GOALS, ANCHORS)
assert perfect["hard_gate"] and perfect["goals"]["met"]

for flag in ("broken_tool", "hallucinated_narrative", "false_guardrail"):
    critical = compute_grade([v("Q1", 5, **{flag: True}),
                              v("Q2", 5)], QS, "m", [], [], GOALS, ANCHORS)
    assert not critical["hard_gate"] and critical["hard_failures"][0]["flags"] == [flag]

err = compute_grade([v("Q1", 5),
                     {"id": "Q2", "error": "api_error", "detail": "boom"}],
                    QS, "m", [], [], GOALS, ANCHORS)
assert not err["hard_gate"] and "1 scoring errors" in err["degradations"]

old_schema = compute_grade([{"id": "Q1", "score": 5, "verdict": "pass"},
                            {"id": "Q2", "score": 5, "verdict": "pass"}],
                           QS, "m", [], [], GOALS, ANCHORS)
assert not old_schema["hard_gate"] and "2 scoring errors" in old_schema["degradations"]

incomplete = compute_grade([v("Q1", 5)], QS, "m", [], [], GOALS, ANCHORS)
assert not incomplete["hard_gate"] and "1 scoring errors" in incomplete["degradations"]

subset = compute_grade([v("Q1", 5)], [QS[0]], "m", [], [], GOALS, ANCHORS)
assert subset["hard_gate"]                              # no missing verdict for Q2

bad_scores = compute_grade([v("Q1", 50), v("Q2", "5")],
                           QS, "m", [], [], GOALS, ANCHORS)
assert not bad_scores["hard_gate"]
assert "2 scoring errors" in bad_scores["degradations"]
assert bad_scores["per_question"] == []

cross = compute_grade(
    [v("Q1", 5), v("Q2", 5)],
    QS, "m", [],
    [{"type": "contradiction", "ids": ["Q1", "Q2"], "comment": "conflict"}],
    GOALS, ANCHORS,
)
assert not cross["hard_gate"] and len(cross["cross_analysis"]) == 1

missing_cross = compute_grade(
    [v("Q1", 5), v("Q2", 5)], QS, "m", [],
    goals=GOALS, anchors=ANCHORS,
)
assert not missing_cross["hard_gate"]
assert "1 cross-analysis errors" in missing_cross["degradations"]

half_point = v("Q1", 4.5, dimensions={"tool_choice": 0.5, "accuracy": 2,
                                      "hallucination_free": 1, "directness": 1})
half_grade = compute_grade([half_point], [QS[0]], "m", [], [], GOALS, ANCHORS)
check("0.5 tool_choice sums in integer hundredths",
      half_grade["per_question"][0]["score"] == 4.5 and half_grade["hard_gate"])
bad_dim = compute_grade([v("Q1", 5, dimensions={"tool_choice": 0.25, "accuracy": 2,
                                                "hallucination_free": 1, "directness": 1.75})],
                        [QS[0]], "m", [], [], GOALS, ANCHORS)
check("invalid dimensions are rejected", bad_dim["per_question"] == [])
bad_sum = compute_grade([v("Q1", 4.4, dimensions={"tool_choice": 0.5, "accuracy": 2,
                                                  "hallucination_free": 1, "directness": 1})],
                        [QS[0]], "m", [], [], GOALS, ANCHORS)
check("dimension sum mismatch is rejected", bad_sum["per_question"] == [])
unanchored_accuracy = compute_grade([v("Q2", 4, dimensions={"tool_choice": 1,
                                                            "accuracy": 2,
                                                            "hallucination_free": 1,
                                                            "directness": 0},
                                       unanchored=True)],
                                    [QS[1]], "m", [], [], GOALS,
                                    {"Q2": {"anchor": None}})
check("accuracy above 1 on unanchored is rejected",
      unanchored_accuracy["per_question"] == [])
unanchored_contradiction = compute_grade([v("Q2", 4, dimensions={"tool_choice": 1,
                                                                 "accuracy": 1,
                                                                 "hallucination_free": 1,
                                                                 "directness": 1},
                                            unanchored=False)],
                                         [QS[1]], "m", [], [], GOALS,
                                         {"Q2": {"anchor": None}})
check("judge unanchored contradiction is rejected",
      unanchored_contradiction["per_question"] == [])
derived = compute_grade([v("Q1", 3, dimensions={"tool_choice": 1, "accuracy": 1,
                                                "hallucination_free": 1, "directness": 0})],
                        [QS[0]], "m", [], [], GOALS, ANCHORS)
check("verdict is derived when omitted",
      derived["per_question"][0]["verdict"] == "warn")
check("improvement_comment is validated but not persisted in per_question",
      "improvement_comment" not in derived["per_question"][0])

mixed_qs = [
    {"id": "Q1", "mode": "m", "split": "dev", "question": "?"},
    {"id": "Q2", "mode": "m", "split": "dev", "question": "?"},
    {"id": "Q3", "mode": "m", "split": "holdout", "question": "?"},
    {"id": "Q4", "mode": "m", "split": "holdout", "question": "?"},
]
mixed_anchors = {"Q1": {"anchor": "a"}, "Q2": {"anchor": None},
                 "Q3": {"anchor": "c"}, "Q4": {"anchor": None}}
mixed = compute_grade([
    v("Q1", 5),
    v("Q2", 2.5, dimensions={"tool_choice": 0.5, "accuracy": 1,
                             "hallucination_free": 0, "directness": 1},
      unanchored=True),
    v("Q3", 5),
    v("Q4", 2.5, dimensions={"tool_choice": 0.5, "accuracy": 1,
                             "hallucination_free": 0, "directness": 1},
      unanchored=True),
], mixed_qs, "m", [], [], GOALS, mixed_anchors)
check("accuracy and split math use anchored only",
      mixed["accuracy_pct"] == 100 and mixed["pass_rate_pct"] == 100
      and mixed["dev"]["percent"] == 100 and mixed["holdout"]["percent"] == 100
      and mixed["gap_pp"] == 0)
check("behavior dimensions use all questions",
      mixed["tool_choice_pct"] == 75 and mixed["hallucination_free_pct"] == 50
      and mixed["directness_pct"] == 100)
check("50pct anchored plus mediocre unanchored does not certify",
      mixed["hard_gate"] and not mixed["goals"]["met"])
hard_unanchored = compute_grade([v("Q2", 1, dimensions={"tool_choice": 0,
                                                       "accuracy": 0,
                                                       "hallucination_free": 0,
                                                       "directness": 1},
                                   unanchored=True)],
                                [QS[1]], "m", [], [], GOALS,
                                {"Q2": {"anchor": None}})
check("hard gate applies to unanchored questions", not hard_unanchored["hard_gate"])
loose_goals = GOALS | {"min_tool_choice_pct": 75, "min_hallucination_free_pct": 50,
                       "min_directness_pct": 100, "min_anchor_coverage_pct": 50}
met = compute_grade([v("Q1", 5), v("Q2", 5)], QS, "m", [], [], loose_goals, ANCHORS)
check("goals can be met", met["goals"]["met"])
strict = compute_grade([v("Q1", 5), v("Q2", 5)], QS, "m", [], [],
                       GOALS | {"min_anchor_coverage_pct": 101}, ANCHORS)
check("goals can fail", not strict["goals"]["met"])
no_holdout_anchor = compute_grade([v("Q1", 5), v("Q2", 4, dimensions={
                                      "tool_choice": 1, "accuracy": 1,
                                      "hallucination_free": 1, "directness": 1},
                                      unanchored=True)],
                                  QS, "m", [], [], loose_goals,
                                  {"Q1": {"anchor": "a"}, "Q2": {"anchor": None}})
check("holdout with no anchored questions cannot certify",
      not no_holdout_anchor["goals"]["met"]
      and no_holdout_anchor["holdout"]["percent"] is None)
zero_anchors = compute_grade([v("Q1", 4, dimensions={"tool_choice": 1,
                                                     "accuracy": 1,
                                                     "hallucination_free": 1,
                                                     "directness": 1},
                                unanchored=True),
                              v("Q2", 4, dimensions={"tool_choice": 1,
                                                     "accuracy": 1,
                                                     "hallucination_free": 1,
                                                     "directness": 1},
                                unanchored=True)],
                             QS, "m", [], [], loose_goals, None)
check("zero anchors cannot certify and records a reason",
      not zero_anchors["goals"]["met"]
      and any(d["metric"] == "certifiable_accuracy" and not d["met"]
              for d in zero_anchors["goals"]["detail"]))

# select_questions: adaptive full triggers and focused selection
AQ = [
    {"id": "Q1", "mode": "sales", "type": "metric", "split": "dev", "question": "?"},
    {"id": "Q2", "mode": "sales", "type": "metric", "split": "dev", "question": "?"},
    {"id": "Q3", "mode": "support", "type": "policy", "split": "dev", "question": "?"},
    {"id": "Q4", "mode": "support", "type": "policy", "split": "dev", "question": "?"},
    {"id": "Q5", "mode": "sales", "type": "metric", "split": "holdout", "question": "?"},
    {"id": "Q6", "mode": "legacy", "split": "dev", "question": "?"},
    {"id": "Q7", "mode": "legacy", "split": "dev", "question": "?"},
]
base = g(70)
base["per_question"] = [
    {"id": "Q1", "score": 3}, {"id": "Q2", "score": 5},
    {"id": "Q3", "score": 5}, {"id": "Q4", "score": 2},
    {"id": "Q5", "score": 2}, {"id": "Q6", "score": 5},
    {"id": "Q7", "score": 3},
]
base["cross_analysis"] = [
    {"type": "contradiction", "ids": ["Q3", "Q4"], "comment": "dev pair"},
    {"type": "contradiction", "ids": ["Q1", "Q5"], "comment": "mixed pair"},
]
cfg = {"probe_strategy": "adaptive", "focused_max_questions": 6,
       "regression_sample": 2, "answer_budget": 30, "_probed_count": 7,
       "max_iterations": 4}
selected, is_full, reason = select_questions(AQ, [base], cfg, 2)
assert not is_full and reason == "focused"
assert [q["id"] for q in selected] == ["Q1", "Q4", "Q7", "Q3", "Q2", "Q6"]
assert all(q["split"] == "dev" for q in selected)
assert select_questions(AQ, [base], cfg, 2)[0] == selected       # deterministic

fixed = json.loads(json.dumps(base))
for row in fixed["per_question"]:
    if row["id"] == "Q7":
        row["score"] = 4
shrunk, _, _ = select_questions(AQ, [base, fixed], cfg | {"regression_sample": 0}, 3)
assert "Q7" not in [q["id"] for q in shrunk]                    # latest score wins

full, is_full, reason = select_questions(AQ, [], cfg, 1)
assert is_full and len(full) == len(AQ) and reason == "no_full_baseline"
passing = json.loads(json.dumps(base))
for row in passing["per_question"]:
    if row["id"] != "Q5":
        row["score"] = 4
assert select_questions(AQ, [passing], cfg, 2)[1:] == (True, "no_dev_failures")
tight = cfg | {"_probed_count": 20}
assert select_questions(AQ, [base], tight, 2)[1:] == (True, "focused_exceeds_budget")
too_many = cfg | {"focused_max_questions": 2}
assert select_questions(AQ, [base], too_many, 2)[1:] == (True, "failures_exceed_focus")
assert select_questions(AQ, [base], cfg, 4)[1:] == (True, "final_iteration")

# budget/config validation
assert budget_plan(30, 30, {"answer_budget": 70}) == (30, 10, 30)
assert not validate_config({"schema_version": 2, "goals": GOALS,
                            "probe_strategy": "full"}, 30)
manual_autonomy = {
    "mode": "manual", "edit_product_code": False, "run_tests": False,
    "restart_local": False, "deploy_staging": False, "commit": False,
}
autopilot_autonomy = manual_autonomy | {
    "mode": "autopilot", "edit_product_code": True, "run_tests": True,
    "commit": True,
}
check("autonomy is optional and defaults to manual",
      not validate_config({"schema_version": 2, "goals": GOALS,
                           "probe_strategy": "full"}, 30))
check("manual and autopilot autonomy blocks are valid",
      not validate_config({"schema_version": 2, "goals": GOALS,
                           "autonomy": manual_autonomy}, 30)
      and not validate_config({"schema_version": 2, "goals": GOALS,
                               "autonomy": autopilot_autonomy}, 30))
check("unknown autonomy modes are rejected",
      "autonomy.mode must be manual or autopilot"
      in validate_config({"schema_version": 2, "goals": GOALS,
                          "autonomy": manual_autonomy | {"mode": "automatic"}}, 30))
check("boolean autonomy mode is rejected instead of treated as a string",
      "autonomy.mode must be manual or autopilot"
      in validate_config({"schema_version": 2, "goals": GOALS,
                          "autonomy": manual_autonomy | {"mode": True}}, 30))
check("autonomy actions must be booleans",
      "autonomy.commit must be a boolean"
      in validate_config({"schema_version": 2, "goals": GOALS,
                          "autonomy": autopilot_autonomy | {"commit": 1}}, 30))
for action in ("edit_product_code", "commit"):
    check(f"autopilot requires {action}",
          f"autonomy.{action} must be true for autopilot"
          in validate_config({"schema_version": 2, "goals": GOALS,
                              "autonomy": autopilot_autonomy | {action: False}}, 30))
check("autopilot does not force optional actions on a service without them",
      not validate_config({"schema_version": 2, "goals": GOALS,
                           "autonomy": autopilot_autonomy | {"run_tests": False}}, 30))
assert validate_config({"schema_version": 2}, 30)
check("v2 config must freeze goals",
      "goals must be an object" in validate_config({"schema_version": 2}, 30))
assert validate_config({"schema_version": 2, "goals": GOALS,
                        "probe_strategy": "oops"}, 30)
assert validate_config({"schema_version": 2, "goals": GOALS,
                        "probe_strategy": "adaptive", "focused_max_questions": 0,
                        "regression_sample": 3, "answer_budget": 70}, 30)
assert validate_config({"schema_version": 2, "goals": GOALS,
                        "probe_strategy": "adaptive", "focused_max_questions": 10,
                        "regression_sample": 3}, 30)
assert validate_config({"schema_version": 2, "goals": GOALS,
                        "probe_strategy": "adaptive", "focused_max_questions": 10,
                        "regression_sample": 3, "answer_budget": 59}, 30)
check("objectives out of range are rejected",
      validate_config({"schema_version": 2,
                       "goals": GOALS | {"min_accuracy_pct": 101}}, 30))

# should_stop: the four D7 conditions + gates
assert should_stop([g(50, hard=True, goals_met=True)], 5)[0]     # gates passed
assert should_stop([g(60), g(55)], 5)[1].startswith("REGRESSION")
assert should_stop([g(60), g(61), g(61.5)], 5)[1].startswith("STAGNATION")
assert not should_stop([g(60), g(65), g(70)], 5)[0]              # improving
assert should_stop([g(60), g(65)], 2)[1].startswith("MAX_ITERATIONS")
assert not should_stop([g(60)], 5)[0]                            # keep going
assert not should_stop([g(60), g(100, hard=True, goals_met=True, full=False)], 5)[0]
assert not should_stop([g(60), g(55, full=False), g(65)], 5)[0]
assert should_stop([g(60), g(65, full=False)], 2)[1].startswith("MAX_ITERATIONS")
check("grade without goals is invalid under schema v2",
      should_stop([{"percent": 100, "dev": {"percent": 100},
                    "hard_gate": True, "full": True}], 5)[1].startswith("INVALID_GRADE"))

# autopilot: dev-only brief and preflight decisions
empty_brief = {"dev": [], "regressed_ids": [], "cross_analysis": []}
check("an empty fix brief is not actionable",
      not loop.brief_is_actionable(empty_brief))
for actionable_key in ("dev", "regressed_ids", "cross_analysis"):
    check(f"a non-empty {actionable_key} makes a fix brief actionable",
          loop.brief_is_actionable(empty_brief | {actionable_key: ["fix"]}))

brief_verdicts = [
    v("Q1", 3, dimensions={"tool_choice": 1, "accuracy": 1,
                            "hallucination_free": 1, "directness": 0})
    | {"improvement_comment": "fix the dev answer"},
    v("Q2", 2, dimensions={"tool_choice": 0, "accuracy": 1,
                            "hallucination_free": 1, "directness": 0})
    | {"improvement_comment": "secret holdout diagnosis"},
]
brief_grade = compute_grade(
    brief_verdicts, QS, "m", [],
    [{"type": "contradiction", "ids": ["Q1"], "comment": "dev-only finding",
      "extra_prose": "must not reach the fixer"},
     {"type": "contradiction", "ids": ["Q1", "Q2"],
      "comment": "mixed finding must be hidden"}],
    GOALS, ANCHORS,
)
BRIEF_AUTH = {"repo": "/srv/service",
              "allowed_actions": {"edit_product_code": True, "run_tests": False,
                                  "restart_local": False, "deploy_staging": False,
                                  "commit": True}}
brief = loop.build_fix_brief(brief_verdicts, QS, brief_grade, ["Q1", "Q2"],
                             BRIEF_AUTH)
brief_text = json.dumps(brief)
check("fix brief includes only failing dev verdicts and dev regressions",
      brief["dev"] == [{"id": "Q1", "score": 3,
                         "improvement_comment": "fix the dev answer"}]
      and brief["regressed_ids"] == ["Q1"])
check("fix brief excludes holdout ids and comments",
      "Q2" not in brief_text and "secret holdout diagnosis" not in brief_text)
passing_qs = QS + [{"id": "Q3", "mode": "sales", "split": "dev", "question": "?"}]
passing_verdicts = brief_verdicts + [v("Q3", 5) | {
    "improvement_comment": "passing dev note is not a fix input"}]
passing_brief = loop.build_fix_brief(
    passing_verdicts, passing_qs,
    compute_grade(passing_verdicts, passing_qs, "m", [], [], GOALS,
                  ANCHORS | {"Q3": {"anchor": "three"}}),
    [], BRIEF_AUTH)
check("fix brief excludes dev questions that already pass",
      [row["id"] for row in passing_brief["dev"]] == ["Q1"]
      and "passing dev note" not in json.dumps(passing_brief))
check("fix brief excludes mixed dev-holdout cross-analysis",
      brief["cross_analysis"] == [
          {"type": "contradiction", "ids": ["Q1"],
           "comment": "dev-only finding"}]
      and "must not reach the fixer" not in brief_text)
check("fix brief exposes only aggregate holdout and gate results",
      brief["holdout"] == {"percent": 40, "gap_pp": 20}
      and brief["gates"] == {"hard_gate": False, "goals_met": False})
check("fix brief carries the authorized repo and action map to the fixer",
      brief["repo"] == "/srv/service"
      and brief["allowed_actions"] == BRIEF_AUTH["allowed_actions"]
      and brief["allowed_actions"]["run_tests"] is False)

ok, reason = loop.git_preflight_decision(True, False, True, True)
check("dirty git tree blocks autopilot start", not ok and "dirty" in reason)


# the git collector against real repos: every other preflight test mocks it away
def git(root, *commands):
    base = ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t"]
    for command in commands:
        subprocess.run(base + list(command), check=True, capture_output=True)


with tempfile.TemporaryDirectory() as d:
    root = pathlib.Path(d)
    run_branch = "service-judge/run-1"
    check("a directory that is not a git repo fails the preflight",
          loop.collect_git_preflight(root, run_branch)[0] is False)
    git(root, ["init", "-b", "work"])
    (root / "app.py").write_text("x = 1\n")
    git(root, ["add", "app.py"], ["commit", "-m", "base"])
    check("a clean attached checkout passes the preflight",
          loop.collect_git_preflight(root, run_branch) == (True, True, True, True))
    (root / "app.py").write_text("x = 2\n")
    check("an uncommitted product change fails the preflight",
          loop.collect_git_preflight(root, run_branch)[1] is False)
    git(root, ["checkout", "--", "app.py"], ["branch", run_branch])
    check("a run branch that already exists elsewhere cannot be created",
          loop.collect_git_preflight(root, run_branch)[3] is False)
    git(root, ["switch", run_branch])
    (root / ".service-judge" / "run-1").mkdir(parents=True)
    (root / ".service-judge" / "run-1" / "grade.json").write_text("{}")
    check("loop artifacts on the run branch do not count as a dirty product tree",
          loop.collect_git_preflight(root, run_branch) == (True, True, True, True))
    git(root, ["switch", "--detach", "HEAD"])
    check("a detached HEAD cannot create the run branch",
          loop.collect_git_preflight(root, "service-judge/run-2")[3] is False)
    git(root, ["switch", "work"],
        ["worktree", "add", "-b", "spare", str(root / "linked")])
    check("a linked worktree cannot host the run branch",
          loop.collect_git_preflight(root / "linked", "service-judge/run-3")[3] is False)


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def make_run(root, questions, cfg_extra=None, history=None):
    golden = root / "questions.golden.jsonl"
    write_jsonl(golden, questions)
    run = root / "run"
    run.mkdir()
    cfg = {
        "probe_cmd": "printf answer",
        "golden_set": str(golden),
        "golden_sha256": hashlib.sha256(golden.read_bytes()).hexdigest(),
        "judge": "codex",
        "max_iterations": 4,
        "schema_version": 2,
        "goals": GOALS,
        "anchors": str(root / "raw" / "anchors.snapshot.json"),
    } | (cfg_extra or {})
    write_json(run / "config.json", cfg)
    (root / "raw").mkdir(exist_ok=True)
    write_json(root / "raw" / "anchors.snapshot.json",
               {q["id"]: {"anchor": "a"} for q in questions})
    if history is not None:
        write_json(run / "history.json", history)
    return run


def run_main(run, *extra):
    old_argv = sys.argv
    out = io.StringIO()
    err = io.StringIO()
    sys.argv = ["loop.py", "--run", str(run), *extra]
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = loop.main()
    finally:
        sys.argv = old_argv
    return rc, out.getvalue(), err.getvalue()


def authorize(run, root):
    write_json(run / "authorization.json", {
        "timestamp": "2026-09-02T10:00:00Z",
        "scope": "service product code",
        "repo": str(root),
        "environment": "staging",
        "allowed_actions": {key: value for key, value in autopilot_autonomy.items()
                            if key != "mode"},
        "approved_text": "Approved autopilot for this run.",
    })


def make_fake_judge(root, mode="valid", secret="PACK-ANSWER-SECRET"):
    judge = root / "fake judge's executable.py"
    counter = root / "judge-count.txt"
    command = " ".join((
        shlex.quote(str(judge)), "{prompt}", "{pack}", "{rubric}",
        "{anchors}", "{out}", shlex.quote(str(counter)),
    ))
    judge.write_text(f'''#!/usr/bin/env python3
import json
import pathlib
import shlex
import sys

prompt, pack, rubric, anchors, out, counter = map(pathlib.Path, sys.argv[1:7])
count = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(count))
assert {secret!r} not in " ".join(sys.argv)
assert {secret!r} not in prompt.read_text()
mode = {mode!r}
if mode == "invalid":
    out.write_text("not json")
    raise SystemExit(0)
if mode == "nonzero":
    sys.stderr.write("fake judge failed")
    raise SystemExit(7)
if mode == "echo_command":
    sys.stderr.write(" ".join(sys.argv))
    sys.stderr.write(shlex.join(sys.argv))
    raise SystemExit(7)
if mode == "partial":
    out.write_text("{{")
    sys.stderr.write("fake judge died after partial output")
    raise SystemExit(7)
if mode == "nonutf8":
    out.write_bytes(bytes([255]))
    raise SystemExit(0)
anchor_rows = json.loads(anchors.read_text())
verdicts = []
for line in pack.read_text().splitlines():
    row = json.loads(line)
    unanchored = anchor_rows[row["id"]]["anchor"] is None
    dimensions = {{"tool_choice": 1, "accuracy": 1 if unanchored else 2,
                   "hallucination_free": 1, "directness": 1}}
    score = 1 if mode == "wrong_score" else sum(dimensions.values())
    verdicts.append({{"id": row["id"], "dimensions": dimensions,
                     "score": score, "unanchored": unanchored,
                     "improvement_comment": "", "broken_tool": False,
                     "hallucinated_narrative": False, "false_guardrail": False}})
text = json.dumps({{"verdicts": verdicts, "cross_analysis": []}})
out.write_text("```json\\n" + text + "\\n```" if mode == "fenced" else text)
''', encoding="utf-8")
    judge.chmod(0o755)
    return command, counter


# external judge helpers and config validation
check("external judge helpers exist",
      all(hasattr(loop, name) for name in (
          "judge_fingerprint", "judge_command", "judge_drift", "judge_prompt_text")))
plain_fingerprint = loop.judge_fingerprint({"judge": "current"})
assert plain_fingerprint == {"label": "current", "cmd_sha256": None}
assert loop.judge_fingerprint({"judge": ""}) == {
    "label": "current harness", "cmd_sha256": None,
}
external_fingerprint = loop.judge_fingerprint(
    {"judge": "codex/gpt", "judge_cmd": "codex {prompt} {out}"})
assert external_fingerprint == {
    "label": "codex/gpt",
    "cmd_sha256": hashlib.sha256(b"codex {prompt} {out}").hexdigest(),
}
assert loop.judge_drift([], external_fingerprint) is None
assert loop.judge_drift([{"judge": "legacy"}], external_fingerprint) is None
assert loop.judge_drift([{"judge": external_fingerprint}], external_fingerprint) is None
assert loop.judge_drift(
    [{"judge": {"label": "old", "cmd_sha256": "old"}}],
    external_fingerprint,
)
assert "unknown judge_cmd placeholder: {question}" in validate_config(
    {"schema_version": 2, "goals": GOALS,
     "judge_cmd": "judge {question} {out}"}, 30)
assert "judge_cmd must be a non-empty string" in validate_config(
    {"schema_version": 2, "goals": GOALS, "judge_cmd": ""}, 30)
assert not validate_config(
    {"schema_version": 2, "goals": GOALS,
     "judge_cmd": "printf '{\"ok\": true}' > {out}"}, 30)
for bad_timeout in (True, 0, -1, 1.5, "900"):
    assert "judge_timeout must be a positive integer" in validate_config(
        {"schema_version": 2, "goals": GOALS,
         "judge_cmd": "judge {out}", "judge_timeout": bad_timeout}, 30)


# no judge_cmd preserves the exact needs_judgment handoff
with tempfile.TemporaryDirectory() as d:
    root = pathlib.Path(d)
    run = make_run(root, QS)
    rc, out, _ = run_main(run)
    iter_dir = run / "iter-01"
    expected = {
        "status": "needs_judgment", "iteration": 1,
        "full": True, "selected_ids": ["Q1", "Q2"],
        "pack": str((iter_dir / "raw/pack.jsonl").resolve()),
        "anchors": str((root / "raw/anchors.snapshot.json").resolve()),
        "rubric": str(loop.RUBRIC_PATH.resolve()),
        "write_verdicts": str((iter_dir / "verdicts.json").resolve()),
        "write_cross_analysis": str((iter_dir / "cross-analysis.json").resolve()),
    }
    assert rc == 0 and out == json.dumps(expected) + "\n"


# an external judge receives quoted paths, tolerates one JSON fence, and grades now
with tempfile.TemporaryDirectory() as d:
    root = pathlib.Path(d) / "judge run's files"
    root.mkdir()
    command, counter = make_fake_judge(root, "fenced")
    run = make_run(root, QS, {
        "probe_cmd": "printf PACK-ANSWER-SECRET",
        "judge": "fake/external", "judge_cmd": command,
    })
    rc, out, _ = run_main(run)
    msg = json.loads(out)
    grade_text = (run / "iter-01/grade.json").read_text()
    history_text = (run / "history.json").read_text()
    fingerprint = {
        "label": "fake/external",
        "cmd_sha256": hashlib.sha256(command.encode()).hexdigest(),
    }
    assert rc == 0 and msg["status"] == "stopped" and counter.read_text() == "1"
    assert json.loads(grade_text)["judge"] == fingerprint
    assert json.loads(history_text)[0]["judge"] == fingerprint
    assert command not in out and command not in grade_text and command not in history_text
    assert "PACK-ANSWER-SECRET" not in (run / "iter-01/raw/judge-prompt.md").read_text()
    assert (run / "iter-01/verdicts.json").exists()
    assert (run / "iter-01/cross-analysis.json").exists()


# a judgment that parses but breaks the rubric contract is never published, so
# the next invocation re-judges instead of grading the same rejected verdicts
with tempfile.TemporaryDirectory() as d:
    root = pathlib.Path(d)
    command, counter = make_fake_judge(root, "wrong_score")
    run = make_run(root, QS, {"judge_cmd": command})
    rc, out, _ = run_main(run)
    assert rc == 2 and json.loads(out)["status"] == "invalid_judgment"
    assert not (run / "iter-01/verdicts.json").exists()
    assert not (run / "iter-01/cross-analysis.json").exists()
    assert not (run / "history.json").exists() and counter.read_text() == "1"
    rc, out, _ = run_main(run)
    assert rc == 2 and json.loads(out)["status"] == "invalid_judgment"
    assert counter.read_text() == "2"          # the judge is invoked again


# invalid output retries exactly once and does not consume the iteration
with tempfile.TemporaryDirectory() as d:
    root = pathlib.Path(d)
    command, counter = make_fake_judge(root, "invalid")
    run = make_run(root, QS, {"judge_cmd": command})
    rc, out, _ = run_main(run)
    msg = json.loads(out)
    assert rc == 2 and msg["status"] == "judge_failed" and msg["attempts"] == 2
    assert counter.read_text() == "2" and command not in out
    assert not (run / "history.json").exists()
    assert not (run / "iter-01/grade.json").exists()
    assert not (run / "iter-01/verdicts.json").exists()
    assert not (run / "iter-01/cross-analysis.json").exists()


# non-zero and partial-output failures pause without publishing judgment files
for failure_mode in ("nonzero", "echo_command", "partial", "nonutf8"):
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        command, counter = make_fake_judge(root, failure_mode)
        if failure_mode == "echo_command":
            command += " --header TOKEN-DEADBEEF"
        run = make_run(root, QS, {"judge_cmd": command})
        rc, out, _ = run_main(run)
        msg = json.loads(out)
        assert rc == 2 and msg["status"] == "judge_failed"
        assert msg["attempts"] == 2 and counter.read_text() == "2"
        assert command not in out and command not in msg["stderr"]
        assert "TOKEN-DEADBEEF" not in out and "TOKEN-DEADBEEF" not in msg["stderr"]
        assert not (run / "history.json").exists()
        assert not (run / "iter-01/verdicts.json").exists()
        assert not (run / "iter-01/cross-analysis.json").exists()


# a malformed judge_cmd still returns a status object instead of a traceback
with tempfile.TemporaryDirectory() as d:
    run = make_run(pathlib.Path(d), QS, {"judge_cmd": "echo it's {out}"})
    rc, out, _ = run_main(run)
    assert rc == 2 and json.loads(out)["status"] == "judge_failed"


# anchors placeholders fail explicitly instead of becoming empty shell arguments
with tempfile.TemporaryDirectory() as d:
    root = pathlib.Path(d)
    command, counter = make_fake_judge(root)
    run = make_run(root, QS, {"judge_cmd": command})
    pathlib.Path(json.loads((run / "config.json").read_text())["anchors"]).unlink()
    rc, out, _ = run_main(run)
    msg = json.loads(out)
    assert rc == 2 and msg["status"] == "judge_failed"
    assert "anchors snapshot" in msg["error"] and not counter.exists()


# drift compares with the first grade, not the most recent one
with tempfile.TemporaryDirectory() as d:
    root = pathlib.Path(d)
    current_command, counter = make_fake_judge(root)
    first_sha = hashlib.sha256(b"first judge {out}").hexdigest()
    current_sha = hashlib.sha256(current_command.encode()).hexdigest()
    history = [
        g(50) | {"judge": {"label": "first", "cmd_sha256": first_sha}},
        g(50) | {"judge": {"label": "latest", "cmd_sha256": current_sha}},
    ]
    run = make_run(root, QS, {"judge_cmd": current_command}, history)
    rc, out, _ = run_main(run)
    msg = json.loads(out)
    assert rc == 2 and msg["status"] == "judge_drift"
    assert msg["expected_sha256"] == first_sha and msg["actual_sha256"] == current_sha
    assert not counter.exists() and not (run / "iter-03/grade.json").exists()


# deleting judge_cmd mid-run is drift too, and drift never costs a pack
with tempfile.TemporaryDirectory() as d:
    root = pathlib.Path(d)
    probed = root / "probed.txt"
    external_sha = hashlib.sha256(b"codex exec {prompt} {out}").hexdigest()
    run = make_run(root, QS, {
        "probe_cmd": f"printf x >> {shlex.quote(str(probed))}; printf answer",
    }, [g(50) | {"judge": {"label": "codex/gpt", "cmd_sha256": external_sha}}])
    rc, out, _ = run_main(run)
    msg = json.loads(out)
    assert rc == 2 and msg["status"] == "judge_drift"
    assert msg["expected_sha256"] == external_sha and msg["actual_sha256"] is None
    assert not probed.exists()          # detected before spending answers


# an in-session run that never had a judge_cmd is not drift
with tempfile.TemporaryDirectory() as d:
    run = make_run(pathlib.Path(d), QS, {}, [
        g(50) | {"judge": {"label": "current harness", "cmd_sha256": None}},
    ])
    rc, out, _ = run_main(run)
    assert rc == 0 and json.loads(out)["status"] == "needs_judgment"


# drift is checked before consuming judgment files left from an interrupted run
with tempfile.TemporaryDirectory() as d:
    root = pathlib.Path(d)
    current_command, counter = make_fake_judge(root)
    first_sha = hashlib.sha256(b"first judge {out}").hexdigest()
    run = make_run(root, QS, {"judge_cmd": current_command}, [
        g(50) | {"judge": {"label": "first", "cmd_sha256": first_sha}},
    ])
    iter_dir = run / "iter-02"
    (iter_dir / "raw").mkdir(parents=True)
    write_json(iter_dir / "selection.json",
               {"selected_ids": ["Q1", "Q2"], "full": True,
                "reason": "fixture", "strategy": "full"})
    write_jsonl(iter_dir / "raw/pack.jsonl",
                [{"id": q["id"], "mode": q["mode"], "question": q["question"],
                  "answer": "fixture"} for q in QS])
    write_json(iter_dir / "verdicts.json", [v("Q1", 5), v("Q2", 5)])
    write_json(iter_dir / "cross-analysis.json", [])
    rc, out, _ = run_main(run)
    msg = json.loads(out)
    assert rc == 2 and msg["status"] == "judge_drift" and not counter.exists()


# filesystem state: selection is authoritative for each iteration
with tempfile.TemporaryDirectory() as d:
    calls = []
    old_probe = loop.probe

    def fake_probe(questions, probe_cmd, timeout=120):
        calls.append([q["id"] for q in questions])
        return [{"id": q["id"], "mode": q["mode"], "question": q["question"],
                 "answer": "ok", "tools_called": None, "error": None}
                for q in questions]

    loop.probe = fake_probe
    try:
        root = pathlib.Path(d)
        run = make_run(root, QS, {"probe_strategy": "adaptive",
                                  "focused_max_questions": 1,
                                  "regression_sample": 0,
                                  "answer_budget": 4})
        rc, out, _ = run_main(run)
        msg = json.loads(out)
        assert rc == 0 and msg["status"] == "needs_judgment"
        assert msg["full"] is True and msg["selected_ids"] == ["Q1", "Q2"]
        assert calls == [["Q1", "Q2"]]
        sel = json.loads((run / "iter-01" / "selection.json").read_text())
        assert sel["selected_ids"] == ["Q1", "Q2"] and sel["full"] is True

        calls.clear()
        (run / "iter-02").mkdir()
        write_json(run / "iter-02" / "selection.json",
                   {"selected_ids": ["Q1"], "full": False,
                    "reason": "focused", "strategy": "adaptive"})
        write_json(run / "history.json", [g(50) | {"full": True, "probed": 2}])
        rc, out, _ = run_main(run)
        assert rc == 0 and json.loads(out)["status"] == "in_progress"
        assert calls == []

        (run / "iter-02" / "raw").mkdir()
        write_jsonl(run / "iter-02" / "raw" / "pack.jsonl",
                    [{"id": "Q2", "mode": "sales", "question": "?", "answer": ""}])
        rc, out, _ = run_main(run)
        assert rc == 2 and json.loads(out)["status"] == "invalid_state"
    finally:
        loop.probe = old_probe

with tempfile.TemporaryDirectory() as d:
    calls = []
    old_probe = loop.probe
    loop.probe = lambda questions, probe_cmd, timeout=120: calls.append(questions) or []
    try:
        root = pathlib.Path(d)
        run = make_run(root, AQ, cfg | {"focused_max_questions": 1}, [base])
        baseline_raw = run / "iter-01" / "raw"
        baseline_raw.mkdir(parents=True)
        write_jsonl(baseline_raw / "pack.jsonl",
                    [{"id": q["id"], "mode": q["mode"], "question": q["question"],
                      "answer": "baseline"} for q in AQ])
        iter_dir = run / "iter-02"
        raw_dir = iter_dir / "raw"
        raw_dir.mkdir(parents=True)
        write_json(iter_dir / "selection.json",
                   {"selected_ids": ["Q1"], "full": False,
                    "reason": "focused", "strategy": "adaptive"})
        write_jsonl(raw_dir / "pack.jsonl",
                    [{"id": "Q1", "mode": "sales", "question": "?", "answer": "ok"}])
        write_json(iter_dir / "verdicts.json", [v("Q1", 5)])
        write_json(iter_dir / "cross-analysis.json", [])
        assert count_probed_rows(run) == 8       # 7 baseline rows + 1 focused row
        rc, out, _ = run_main(run)
        assert rc == 0 and json.loads(out)["probed"] == 1
        grade = json.loads((iter_dir / "grade.json").read_text())
        assert grade["full"] is False and grade["probed"] == 1
        assert [r["id"] for r in grade["per_question"]] == ["Q1"]
        check("focused grades do not evaluate goals", "goals" not in grade)
        check("passing focused run reports focused-passed reason",
              json.loads(out)["reason"].startswith("FOCUSED PASSED"))
        check("manual mode does not write an automatic fix brief",
              not (iter_dir / "fix-brief.json").exists()
              and "fix_brief" not in json.loads(out))
        assert calls == []                       # no reselect/reprobe during finalize
    finally:
        loop.probe = old_probe

with tempfile.TemporaryDirectory() as d:
    root = pathlib.Path(d)
    run = make_run(root, QS, {"schema_version": 1})
    rc, out, _ = run_main(run)
    msg = json.loads(out)
    check("config v1 is rejected cleanly",
          rc == 2 and msg["status"] == "unsupported_schema")

with tempfile.TemporaryDirectory() as d:
    root = pathlib.Path(d)
    run = make_run(root, QS, {"autonomy": autopilot_autonomy})
    rc, out, _ = run_main(run)
    msg = json.loads(out)
    check("autopilot refuses to start without authorization audit record",
          rc == 2 and msg["status"] == "autopilot_blocked"
          and not (run / "iter-01").exists())

with tempfile.TemporaryDirectory() as d:
    old_collect = loop.collect_git_preflight
    loop.collect_git_preflight = lambda repo, branch: (True, False, True, True)
    try:
        root = pathlib.Path(d)
        run = make_run(root, QS, {"autonomy": autopilot_autonomy})
        authorize(run, root)
        rc, out, _ = run_main(run)
        msg = json.loads(out)
        check("dirty preflight prevents any autopilot iteration",
              rc == 2 and msg["status"] == "autopilot_blocked"
              and msg["manual_available"] is True
              and not (run / "iter-01").exists())
    finally:
        loop.collect_git_preflight = old_collect

with tempfile.TemporaryDirectory() as d:
    calls = []
    old_probe = loop.probe
    loop.probe = lambda questions, probe_cmd, timeout=120: calls.append(questions) or []
    try:
        root = pathlib.Path(d)
        run = make_run(root, QS)
        iter_dir = run / "iter-01"
        raw_dir = iter_dir / "raw"
        raw_dir.mkdir(parents=True)
        write_json(iter_dir / "selection.json",
                   {"selected_ids": ["Q1", "Q2"], "full": True,
                    "reason": "handoff", "strategy": "full"})
        write_jsonl(raw_dir / "pack.jsonl",
                    [{"id": q["id"], "mode": q["mode"], "question": q["question"],
                      "answer": "prefab"} for q in QS])
        write_json(iter_dir / "verdicts.json", [v("Q1", 5), v("Q2", 5)])
        write_json(iter_dir / "cross-analysis.json", [])
        rc, out, _ = run_main(run)
        msg = json.loads(out)
        check("iter-01 handoff finalizes without probing",
              rc == 0 and msg["status"] == "stopped" and calls == [])
    finally:
        loop.probe = old_probe

with tempfile.TemporaryDirectory() as d:
    old_collect = getattr(loop, "collect_git_preflight", None)
    loop.collect_git_preflight = lambda repo, branch: (True, True, True, True)
    try:
        root = pathlib.Path(d)
        previous_focused = g(100, full=False)
        previous_focused["per_question"] = [
            {"id": "Q1", "score": 5}, {"id": "Q2", "score": 5},
        ]
        run = make_run(root, QS, {"autonomy": autopilot_autonomy},
                       [previous_focused])
        authorize(run, root)
        iter_dir = run / "iter-02"
        raw_dir = iter_dir / "raw"
        raw_dir.mkdir(parents=True)
        write_json(iter_dir / "selection.json",
                   {"selected_ids": ["Q1", "Q2"], "full": True,
                    "reason": "fixture", "strategy": "full"})
        write_jsonl(raw_dir / "pack.jsonl",
                    [{"id": q["id"], "mode": q["mode"], "question": q["question"],
                      "answer": "fixture"} for q in QS])
        write_json(iter_dir / "verdicts.json", brief_verdicts)
        write_json(iter_dir / "cross-analysis.json", [])
        rc, out, _ = run_main(run)
        msg = json.loads(out)
        saved = json.loads((iter_dir / "fix-brief.json").read_text())
        check("autopilot needs_fix writes the redacted brief",
              rc == 0 and msg["status"] == "needs_fix"
              and saved["dev"][0]["id"] == "Q1"
              and "Q2" not in json.dumps(saved))
        check("the written brief tells the fixer the repo and what it may do",
              saved["repo"] == str(root)
              and saved["allowed_actions"] == {
                  key: value for key, value in autopilot_autonomy.items()
                  if key != "mode"})
        check("needs_fix reports only dev failure and regression counts",
              msg["reason"].startswith("NEEDS_FIX:")
              and "1 dev question" in msg["reason"]
              and "1 dev regression" in msg["reason"]
              and "Q2" not in msg["reason"])
        check("the run keeps a raw/ directory for the anchors snapshot",
              (run / "raw").is_dir())
    finally:
        if old_collect is None:
            del loop.collect_git_preflight
        else:
            loop.collect_git_preflight = old_collect

with tempfile.TemporaryDirectory() as d:
    old_collect = loop.collect_git_preflight
    loop.collect_git_preflight = lambda repo, branch: (True, True, True, True)
    try:
        root = pathlib.Path(d)
        holdout_failure = [v("Q1", 5), v("Q2", 3, dimensions={
            "tool_choice": 1, "accuracy": 1,
            "hallucination_free": 1, "directness": 0,
        })]

        run = make_run(root, QS, {"autonomy": autopilot_autonomy})
        authorize(run, root)
        iter_dir = run / "iter-01"
        raw_dir = iter_dir / "raw"
        raw_dir.mkdir(parents=True)
        write_json(iter_dir / "selection.json",
                   {"selected_ids": ["Q1", "Q2"], "full": True,
                    "reason": "fixture", "strategy": "full"})
        write_jsonl(raw_dir / "pack.jsonl",
                    [{"id": q["id"], "mode": q["mode"],
                      "question": q["question"], "answer": "fixture"}
                     for q in QS])
        write_json(iter_dir / "verdicts.json", holdout_failure)
        write_json(iter_dir / "cross-analysis.json", [])
        rc, out, _ = run_main(run)
        msg = json.loads(out)
        check("full autopilot stops when only hidden work remains",
              rc == 0 and msg["status"] == "stopped"
              and msg["reason"].startswith("NOTHING_ACTIONABLE:")
              and "fix_brief" not in msg
              and not (iter_dir / "fix-brief.json").exists()
              and (iter_dir / "grade.json").exists()
              and (run / "history.json").exists())

        manual_root = root / "manual"
        manual_root.mkdir()
        manual_run = make_run(manual_root, QS)
        manual_iter = manual_run / "iter-01"
        manual_raw = manual_iter / "raw"
        manual_raw.mkdir(parents=True)
        write_json(manual_iter / "selection.json",
                   {"selected_ids": ["Q1", "Q2"], "full": True,
                    "reason": "fixture", "strategy": "full"})
        write_jsonl(manual_raw / "pack.jsonl",
                    [{"id": q["id"], "mode": q["mode"],
                      "question": q["question"], "answer": "fixture"}
                     for q in QS])
        write_json(manual_iter / "verdicts.json", holdout_failure)
        write_json(manual_iter / "cross-analysis.json", [])
        rc, out, _ = run_main(manual_run)
        check("manual mode still returns needs_fix for hidden-only failures",
              rc == 0 and json.loads(out)["status"] == "needs_fix")

        focused_root = root / "focused"
        focused_root.mkdir()
        focused_run = make_run(focused_root, QS,
                               {"autonomy": autopilot_autonomy})
        authorize(focused_run, focused_root)
        focused_iter = focused_run / "iter-01"
        focused_raw = focused_iter / "raw"
        focused_raw.mkdir(parents=True)
        write_json(focused_iter / "selection.json",
                   {"selected_ids": ["Q1"], "full": False,
                    "reason": "focused", "strategy": "adaptive"})
        write_jsonl(focused_raw / "pack.jsonl",
                    [{"id": "Q1", "mode": "sales", "question": "?",
                      "answer": "fixture"}])
        write_json(focused_iter / "verdicts.json", [v("Q1", 5)])
        write_json(focused_iter / "cross-analysis.json", [])
        rc, out, _ = run_main(focused_run)
        msg = json.loads(out)
        check("focused autopilot never stops as nothing actionable",
              rc == 0 and msg["status"] == "needs_fix"
              and msg["reason"].startswith("FOCUSED PASSED"))
    finally:
        loop.collect_git_preflight = old_collect

with tempfile.TemporaryDirectory() as d:
    root = pathlib.Path(d)
    run = make_run(root, QS)
    pathlib.Path(json.loads((run / "config.json").read_text())["anchors"]).write_text(
        "[", encoding="utf-8")
    rc, out, _ = run_main(run, "--plan")
    msg = json.loads(out)
    check("malformed anchors snapshot degrades during planning",
          rc == 0 and msg["status"] == "plan")

with tempfile.TemporaryDirectory() as d:
    root = pathlib.Path(d)
    run = make_run(root, QS)
    pathlib.Path(json.loads((run / "config.json").read_text())["anchors"]).unlink()
    rc, out, _ = run_main(run, "--plan")
    msg = json.loads(out)
    check("absent anchors snapshot degrades during planning",
          rc == 0 and msg["status"] == "plan")

with tempfile.TemporaryDirectory() as d:
    old_probe = loop.probe
    calls = []
    loop.probe = lambda questions, probe_cmd, timeout=120: calls.append(questions) or []
    try:
        run = make_run(pathlib.Path(d), QS, {"probe_strategy": "adaptive",
                                             "focused_max_questions": 1,
                                             "regression_sample": 0,
                                             "answer_budget": 4})
        rc, out, _ = run_main(run, "--plan")
        msg = json.loads(out)
        assert rc == 0 and msg["status"] == "plan"
        assert msg["strategy"] == "adaptive" and msg["full"] is True
        assert msg["autopilot_preflight"] == "not_applicable"
        assert not (run / "iter-01").exists()
        assert calls == []
    finally:
        loop.probe = old_probe

with tempfile.TemporaryDirectory() as d:
    old_collect = loop.collect_git_preflight
    loop.collect_git_preflight = lambda repo, branch: (True, True, True, True)
    try:
        root = pathlib.Path(d)
        run = make_run(root, QS, {"autonomy": autopilot_autonomy})
        authorize(run, root)
        rc, out, _ = run_main(run, "--plan")
        msg = json.loads(out)
        check("autopilot plan reports a passed preflight",
              rc == 0 and msg["status"] == "plan"
              and msg["autopilot_preflight"] == "passed")
    finally:
        loop.collect_git_preflight = old_collect

# regressed_ids: a first measurement is not a regression
assert loop.regressed_ids([{"id": "Q1", "score": 2}], {}) == []
assert loop.regressed_ids([{"id": "Q1", "score": 2}], {"Q1": 5}) == ["Q1"]
assert loop.regressed_ids([{"id": "Q1", "score": 2}], {"Q1": 1}) == []
assert loop.regressed_ids([{"id": "Q1", "score": 5}], {"Q1": 5}) == []

# plan_output: any full run can certify, including the baseline with empty history
base = loop.plan_output(1, "adaptive", [{"id": "Q1", "split": "dev"}], True,
                        "no_full_baseline", 0, 10, 5, [])
assert (base["certification"] is True and base["split_counts"] == {"dev": 1}
        and base["autopilot_preflight"] == "not_applicable")
focused = loop.plan_output(2, "adaptive", [{"id": "Q1", "split": "dev"}], False,
                           "focused", 5, 5, 5, [{"full": True}])
assert focused["certification"] is False and focused["selected_ids"] == ["Q1"]

print("test_loop: all assertions passed")
