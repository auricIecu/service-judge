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


def g(dev_pct, hard=False, soft=False, full=True):
    return {"percent": dev_pct, "dev": {"percent": dev_pct},
            "hard_gate": hard, "soft_gate": soft, "full": full}


def v(qid, score, verdict, **critical):
    return {"id": qid, "score": score, "verdict": verdict,
            **{flag: critical.get(flag, False) for flag in (
                "broken_tool", "hallucinated_narrative", "false_guardrail")}}


# compute_grade: aggregates, splits, gates
grade = compute_grade([v("Q1", 5, "pass"), v("Q2", 1, "fail")],
                      QS, "m", [], [])
assert grade["total"] == 6 and grade["max"] == 10 and grade["percent"] == 60
assert grade["dev"]["percent"] == 100 and grade["holdout"]["percent"] == 20
assert grade["gap_pp"] == 80
assert not grade["hard_gate"]          # a score <=1 breaks the hard gate
assert not grade["soft_gate"]          # only 50% of questions >=4

perfect = compute_grade([v("Q1", 5, "pass"), v("Q2", 5, "pass")],
                        QS, "m", [], [])
assert perfect["hard_gate"] and perfect["soft_gate"]

for flag in ("broken_tool", "hallucinated_narrative", "false_guardrail"):
    critical = compute_grade([v("Q1", 5, "pass", **{flag: True}),
                              v("Q2", 5, "pass")], QS, "m", [], [])
    assert not critical["hard_gate"] and critical["hard_failures"][0]["flags"] == [flag]

err = compute_grade([v("Q1", 5, "pass"),
                     {"id": "Q2", "error": "api_error", "detail": "boom"}],
                    QS, "m", [], [])
assert not err["hard_gate"] and "1 scoring errors" in err["degradations"]

old_schema = compute_grade([{"id": "Q1", "score": 5, "verdict": "pass"},
                            {"id": "Q2", "score": 5, "verdict": "pass"}],
                           QS, "m", [], [])
assert not old_schema["hard_gate"] and "2 scoring errors" in old_schema["degradations"]

incomplete = compute_grade([v("Q1", 5, "pass")], QS, "m", [], [])
assert not incomplete["hard_gate"] and "1 scoring errors" in incomplete["degradations"]

subset = compute_grade([v("Q1", 5, "pass")], [QS[0]], "m", [], [])
assert subset["hard_gate"] and subset["soft_gate"]      # no missing verdict for Q2

bad_scores = compute_grade([v("Q1", 50, "bad"), v("Q2", "5", "bad")],
                           QS, "m", [], [])
assert not bad_scores["hard_gate"]
assert "2 scoring errors" in bad_scores["degradations"]
assert bad_scores["per_question"] == []

cross = compute_grade(
    [v("Q1", 5, "pass"), v("Q2", 5, "pass")],
    QS, "m", [],
    [{"type": "contradiction", "ids": ["Q1", "Q2"], "comment": "conflict"}],
)
assert not cross["hard_gate"] and len(cross["cross_analysis"]) == 1

missing_cross = compute_grade(
    [v("Q1", 5, "pass"), v("Q2", 5, "pass")], QS, "m", [],
)
assert not missing_cross["hard_gate"]
assert "1 cross-analysis errors" in missing_cross["degradations"]

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
assert not validate_config({"probe_strategy": "full"}, 30)
assert not validate_config({}, 30)
assert validate_config({"probe_strategy": "oops"}, 30)
assert validate_config({"probe_strategy": "adaptive", "focused_max_questions": 0,
                        "regression_sample": 3, "answer_budget": 70}, 30)
assert validate_config({"probe_strategy": "adaptive", "focused_max_questions": 10,
                        "regression_sample": 3}, 30)
assert validate_config({"probe_strategy": "adaptive", "focused_max_questions": 10,
                        "regression_sample": 3, "answer_budget": 59}, 30)

# should_stop: the four D7 conditions + gates
assert should_stop([g(50, hard=True, soft=True)], 5)[0]          # gates passed
assert should_stop([g(60), g(55)], 5)[1].startswith("REGRESSION")
assert should_stop([g(60), g(61), g(61.5)], 5)[1].startswith("STAGNATION")
assert not should_stop([g(60), g(65), g(70)], 5)[0]              # improving
assert should_stop([g(60), g(65)], 2)[1].startswith("MAX_ITERATIONS")
assert not should_stop([g(60)], 5)[0]                            # keep going
assert not should_stop([g(60), g(100, hard=True, soft=True, full=False)], 5)[0]
assert not should_stop([g(60), g(55, full=False), g(65)], 5)[0]
assert should_stop([g(60), g(65, full=False)], 2)[1].startswith("MAX_ITERATIONS")


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
    } | (cfg_extra or {})
    write_json(run / "config.json", cfg)
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
        write_json(iter_dir / "verdicts.json", [v("Q1", 5, "pass")])
        write_json(iter_dir / "cross-analysis.json", [])
        assert count_probed_rows(run) == 8       # 7 baseline rows + 1 focused row
        rc, out, _ = run_main(run)
        assert rc == 0 and json.loads(out)["probed"] == 1
        grade = json.loads((iter_dir / "grade.json").read_text())
        assert grade["full"] is False and grade["probed"] == 1
        assert [r["id"] for r in grade["per_question"]] == ["Q1"]
        assert calls == []                       # no reselect/reprobe during finalize
    finally:
        loop.probe = old_probe

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
