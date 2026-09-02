# Rubric — score each answer 0–5

Single source of truth. Every in-session judge or harness subagent receives
this text inline in its prompt; never paraphrase it.

| Dimension | Points | What to check |
|---|---|---|
| Tool choice | 0–1 | Did it call the appropriate tool/path for this question? (1 = right tool; 0.5 = suboptimal but valid; 0 = wrong/no tool when one existed) |
| Accuracy vs anchor | 0–2 | Numbers/facts match the anchor. 2 = exact; 1 = right direction, minor error; 0 = wrong. No anchor → grade plausibility, max 1, and mark `unanchored` |
| Hallucination | 0–1 | 1 = no invented numbers AND no invented interpretation. Interpretive hallucination (narrating artifacts as real events) loses the point even if digits are correct |
| Directness | 0–1 | Answered the actual question, usable by a real user, no deflection |

Verdict bands: pass ≥4, warn 2.5–3.5, fail ≤2. Do not emit a `verdict`
field; `loop.py` derives it from `score`.

An honest "I don't have that data" on a question whose anchor is none/trap
is a good answer, not a failure.

Score FIRST, then write the improvement comment. Never adjust a score to
match a comment already written.

## Verdict object

Each verdict in `verdicts.json` must have this shape:

```json
{
  "id": "Q01",
  "dimensions": {
    "tool_choice": 1,
    "accuracy": 2,
    "hallucination_free": 1,
    "directness": 1
  },
  "score": 5,
  "unanchored": false,
  "improvement_comment": "",
  "broken_tool": false,
  "hallucinated_narrative": false,
  "false_guardrail": false
}
```

`score` must equal the sum of `dimensions`. `unanchored` is your claim about
whether the question had no usable anchor; the loop checks it against
`anchors.snapshot.json` and rejects contradictions.

## Critical findings (required booleans, independent of score)

Every verdict must also set these three fields:

- `broken_tool`: true only when the answer/tool reports a technical failure
  but the anchor shows that the requested data exists.
- `hallucinated_narrative`: true when the answer presents an invented number,
  interpretation, verification, provenance, or causal narrative as fact.
- `false_guardrail`: true when a fallback, refusal, or out-of-scope response
  blocks a legitimate answerable question.

These flags feed the binary hard gate. Set them from the evidence even when
the numeric score is greater than 1.
