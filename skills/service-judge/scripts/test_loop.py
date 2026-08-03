#!/usr/bin/env python3
"""Self-check for loop.py's pure logic. Run: python3 scripts/test_loop.py"""
from loop import compute_grade, should_stop

QS = [{"id": "Q1", "mode": "sales", "split": "dev", "question": "?"},
      {"id": "Q2", "mode": "sales", "split": "holdout", "question": "?"}]


def g(dev_pct, hard=False, soft=False):
    return {"dev": {"percent": dev_pct}, "hard_gate": hard, "soft_gate": soft}


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

# should_stop: the four D7 conditions + gates
assert should_stop([g(50, hard=True, soft=True)], 5)[0]          # gates passed
assert should_stop([g(60), g(55)], 5)[1].startswith("REGRESSION")
assert should_stop([g(60), g(61), g(61.5)], 5)[1].startswith("STAGNATION")
assert not should_stop([g(60), g(65), g(70)], 5)[0]              # improving
assert should_stop([g(60), g(65)], 2)[1].startswith("MAX_ITERATIONS")
assert not should_stop([g(60)], 5)[0]                            # keep going

print("test_loop: all assertions passed")
