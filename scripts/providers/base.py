"""Scorer provider interface (LOOP-DESIGN §3, D2).

Isolation contract (D2): a scorer receives ONLY rubric + anchors + the item
itself. Callers must never pass iteration numbers, previous scores, target
thresholds, or the applied diff.

Item (one pack record):  id, mode, question, answer, tools_called?, error?
Verdict:                 id, score (0-5), verdict (pass|warn|fail),
                         unanchored (bool), improvement_comment (str)
"""
from typing import Protocol


class Scorer(Protocol):
    def score(self, items: list[dict], anchors: str, rubric: str) -> list[dict]:
        """Score every item against the anchors; one verdict dict per item."""
        ...
