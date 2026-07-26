"""Anthropic Batches API scorer (core extracted from batch_eval.py).

Builds one independent request per item (D2 isolation), with prompt caching
on the shared rubric+anchors prefix. Batches API is -50% cost.
"""
import json
import time

VERDICT_TOOL = {
    "name": "verdict",
    "description": "Submit the judgment for one answer.",
    "input_schema": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "score": {"type": "number", "minimum": 0, "maximum": 5},
            "verdict": {"type": "string", "enum": ["pass", "warn", "fail"]},
            "unanchored": {"type": "boolean"},
            "improvement_comment": {"type": "string"},
        },
        "required": ["id", "score", "verdict", "unanchored", "improvement_comment"],
    },
}


def build_requests(items: list[dict], anchors: str, rubric: str, model: str) -> list[dict]:
    shared_prefix = [
        {"type": "text", "text": rubric},
        {"type": "text", "text": f"GROUND-TRUTH ANCHORS:\n{anchors}",
         "cache_control": {"type": "ephemeral"}},
    ]
    requests = []
    for rec in items:
        case = (f"QUESTION {rec['id']} (mode: {rec['mode']}):\n{rec['question']}\n\n"
                f"TOOLS CALLED: {rec.get('tools_called')}\nERROR: {rec.get('error')}\n\n"
                f"ANSWER:\n{rec['answer']}")
        requests.append({
            "custom_id": rec["id"],
            "params": {
                "model": model,
                "max_tokens": 1024,
                "system": shared_prefix,
                "tools": [VERDICT_TOOL],
                "tool_choice": {"type": "tool", "name": "verdict"},
                "messages": [{"role": "user", "content": case}],
            },
        })
    return requests


def submit(requests: list[dict]) -> str:
    import anthropic
    client = anthropic.Anthropic()
    return client.messages.batches.create(requests=requests).id


def fetch(batch_id: str) -> tuple[str, list[dict] | None]:
    """Return (status, verdicts). verdicts is None until status == 'ended'.
    Failed items come back as {"id", "error", "detail"} records."""
    import anthropic
    client = anthropic.Anthropic()
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        return batch.processing_status, None
    verdicts = []
    for result in client.messages.batches.results(batch_id):
        if result.result.type == "succeeded":
            for block in result.result.message.content:
                if block.type == "tool_use":
                    verdicts.append(dict(block.input))
        else:
            verdicts.append({"id": result.custom_id, "error": result.result.type,
                             "detail": getattr(getattr(result.result, "error", None), "message", None)})
    return "ended", verdicts


class AnthropicBatchScorer:
    """Blocking Scorer over the Batches API (for loop.py)."""

    def __init__(self, model: str = "claude-fable-5", poll_interval: float = 30.0):
        self.model = model
        self.poll_interval = poll_interval

    def score(self, items: list[dict], anchors: str, rubric: str) -> list[dict]:
        batch_id = submit(build_requests(items, anchors, rubric, self.model))
        while True:
            status, verdicts = fetch(batch_id)
            if status == "ended":
                return verdicts
            time.sleep(self.poll_interval)


def load_pack(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]
