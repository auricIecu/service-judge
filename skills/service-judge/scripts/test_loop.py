#!/usr/bin/env python3
"""Self-check for loop.py's pure logic. Run: python3 scripts/test_loop.py"""
import contextlib
import hashlib
import io
import json
import pathlib
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
        assert not (run / "iter-01").exists()
        assert calls == []
    finally:
        loop.probe = old_probe

# regressed_ids: a first measurement is not a regression
assert loop.regressed_ids([{"id": "Q1", "score": 2}], {}) == []
assert loop.regressed_ids([{"id": "Q1", "score": 2}], {"Q1": 5}) == ["Q1"]
assert loop.regressed_ids([{"id": "Q1", "score": 2}], {"Q1": 1}) == []
assert loop.regressed_ids([{"id": "Q1", "score": 5}], {"Q1": 5}) == []

# plan_output: any full run can certify, including the baseline with empty history
base = loop.plan_output(1, "adaptive", [{"id": "Q1", "split": "dev"}], True,
                        "no_full_baseline", 0, 10, 5, [])
assert base["certification"] is True and base["split_counts"] == {"dev": 1}
focused = loop.plan_output(2, "adaptive", [{"id": "Q1", "split": "dev"}], False,
                           "focused", 5, 5, 5, [{"full": True}])
assert focused["certification"] is False and focused["selected_ids"] == ["Q1"]

print("test_loop: all assertions passed")
