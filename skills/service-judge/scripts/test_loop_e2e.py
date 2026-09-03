#!/usr/bin/env python3
"""End-to-end check for loop.py: 100 questions, no network, no paid API.

test_loop.py covers the pure logic; this drives the whole command twice per
scenario -- probe, judge, finalize -- against a synthetic 100-question golden
set (90 anchored, 10 unanchored traps). The bugs that unit tests missed in
1.5.0 were all found this way, so run both.

Run: python3 test_loop_e2e.py
"""
import contextlib
import hashlib
import io
import json
import pathlib
import sys
import tempfile

import loop

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
GOLDEN = FIXTURES / "e2e.golden.jsonl"
ANCHORS = json.loads((FIXTURES / "e2e.anchors.snapshot.json").read_text())
GOALS = {
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
ANCHORED_PERFECT = {"tool_choice": 1, "accuracy": 2,
                    "hallucination_free": 1, "directness": 1}


def run_main(run):
    """loop.py writes progress and a JSON status line to stdout; keep the JSON."""
    buf, argv = io.StringIO(), sys.argv
    sys.argv = ["loop.py", "--run", str(run)]
    try:
        with contextlib.redirect_stdout(buf):
            rc = loop.main()
    finally:
        sys.argv = argv
    return rc, json.loads(buf.getvalue().strip().splitlines()[-1])


def scenario(tmp, unanchored_dimensions, cfg_extra=None):
    """Probe, judge every question, finalize. Returns (rc, status, grade)."""
    run = pathlib.Path(tmp) / "run"
    (run / "raw").mkdir(parents=True)
    snapshot_path = run / "raw/anchors.snapshot.json"
    snapshot_path.write_text(json.dumps(ANCHORS, indent=2))
    (run / "config.json").write_text(json.dumps({
        "schema_version": 2,
        "probe_cmd": "printf %s {question}",
        "golden_set": str(GOLDEN),
        "golden_sha256": hashlib.sha256(GOLDEN.read_bytes()).hexdigest(),
        "anchors": str(snapshot_path),
        "judge": "mock-harness",
        "goals": GOALS,
        "max_iterations": 3,
    } | (cfg_extra or {}), indent=2))

    rc, status = run_main(run)
    if rc != 0:
        return run, rc, status, None
    assert status["status"] == "needs_judgment"

    pack = [json.loads(line) for line
            in (run / "iter-01/raw/pack.jsonl").read_text().splitlines()]
    assert len(pack) == 100
    verdicts = []
    for item in pack:
        unanchored = ANCHORS[item["id"]]["anchor"] is None
        dimensions = unanchored_dimensions if unanchored else ANCHORED_PERFECT
        verdicts.append({"id": item["id"], "dimensions": dimensions,
                         "score": sum(dimensions.values()),
                         "unanchored": unanchored, "improvement_comment": "",
                         "broken_tool": False, "hallucinated_narrative": False,
                         "false_guardrail": False})
    (run / "iter-01/verdicts.json").write_text(json.dumps(verdicts))
    (run / "iter-01/cross-analysis.json").write_text("[]")

    rc, status = run_main(run)
    assert rc == 0
    return run, rc, status, json.loads((run / "iter-01/grade.json").read_text())


# A perfect service certifies. Unanchored questions sit at their rubric ceiling
# of 4/5 (accuracy capped at 1) and that is no longer what blocks approval --
# the 1.5.0 soft_gate required >=4 across all questions, including these.
with tempfile.TemporaryDirectory() as tmp:
    run, _, status, clean = scenario(tmp, {"tool_choice": 1, "accuracy": 1,
                                           "hallucination_free": 1, "directness": 1})
    assert status["reason"] == "PASSED: hard gate and goals met", status
    assert "soft_gate" not in clean                    # removed in 1.6.0
    assert clean["hard_gate"] and clean["goals"]["met"]
    assert clean["judge"] == {"label": "mock-harness", "cmd_sha256": None}
    assert clean["anchor_coverage_pct"] == 90          # 90 anchored of 100 asked
    assert clean["unanchored_block"]["count"] == 10
    assert clean["accuracy_pct"] == 100 and clean["pass_rate_pct"] == 100
    assert clean["gap_pp"] == 0
    # judge prose never reaches grade.json/history.json
    assert all("improvement_comment" not in r for r in clean["per_question"])
    assert len(json.loads((run / "history.json").read_text())) == 1

# The hole this release closed: perfect on every verifiable question, mediocre
# on the rest. Accuracy still reads 100% because it counts anchored only -- but
# hallucination-free is scored over ALL questions, so it cannot certify.
with tempfile.TemporaryDirectory() as tmp:
    _, _, _, mediocre = scenario(tmp, {"tool_choice": 1, "accuracy": 1,
                                       "hallucination_free": 0, "directness": 1})
    assert mediocre["hard_gate"]                       # nothing scored <=1
    assert mediocre["accuracy_pct"] == 100             # the verifiable half is perfect
    assert mediocre["hallucination_free_pct"] == 90    # scored over all 100
    assert not mediocre["goals"]["met"]
    missed = [d["metric"] for d in mediocre["goals"]["detail"] if not d["met"]]
    assert missed == ["hallucination_free_pct"], missed

# A 1.5.0 config is rejected, not graded under rules it was never measured by.
with tempfile.TemporaryDirectory() as tmp:
    run, rc, status, _ = scenario(tmp, {}, {"schema_version": 1})
    assert rc == 2 and status["status"] == "unsupported_schema"
    assert not (run / "iter-01").exists()

print(json.dumps({"pack": 100, "anchor_coverage_pct": clean["anchor_coverage_pct"],
                  "clean_grade": clean["percent"], "clean_certifies": True,
                  "mediocre_grade": mediocre["percent"], "mediocre_certifies": False,
                  "status": "test_loop_e2e: all assertions passed"}))
