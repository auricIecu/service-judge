#!/usr/bin/env python3
"""Local HTTP integration check; no Langfuse account or paid requests needed."""
import base64
import contextlib
import hashlib
import io
import json
import os
import pathlib
import shlex
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import loop


OBS = [
    {"id": "root", "traceId": "trace-A", "sessionId": "session-A",
     "projectId": "project-A", "type": "SPAN", "parentObservationId": None,
     "startTime": "2026-09-21T10:00:00Z", "endTime": "2026-09-21T10:00:02Z"},
    {"id": "tool-1", "traceId": "trace-A", "sessionId": "session-A",
     "type": "TOOL", "name": "inventory", "parentObservationId": "root",
     "input": '{"company":"A","authorization":"Bearer private-token"}',
     "output": '{"count":-2}', "level": "ERROR", "statusMessage": "bad count",
     "startTime": "2026-09-21T10:00:00Z", "endTime": "2026-09-21T10:00:01Z"},
    {"id": "gen-1", "traceId": "trace-A", "sessionId": "session-A",
     "type": "GENERATION", "name": "answer", "parentObservationId": "root",
     "model": "answerer", "input": "Bearer private-token", "output": "42",
     "usageDetails": {"input": 80, "input_cached": 20, "output": 8},
     "totalCost": 0.012, "metadata": {"retry_attempt": 1, "api_key": "other-secret"},
     "startTime": "2026-09-21T10:00:01Z", "endTime": "2026-09-21T10:00:02Z"},
]


class Handler(BaseHTTPRequestHandler):
    mode = "ok"
    calls = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        query = parse_qs(urlsplit(self.path).query)
        self.calls.append((self.path, self.headers.get("Authorization")))
        if self.mode == "slow":
            time.sleep(3)
        status = 429 if self.mode == "quota" else 200
        rows = OBS
        if self.mode == "empty" or self.mode == "delayed" and len(self.calls) == 1:
            rows = []
        if self.mode == "wrong":
            rows = [OBS[0] | {"traceId": "unrelated"}]
        cursor = None
        if self.mode == "pages":
            rows = OBS[1:] if query.get("cursor") else OBS[:1]
            cursor = None if query.get("cursor") else "next"
        if self.mode == "partial":
            rows = [OBS[1]]
        if self.mode == "malformed":
            rows = [None]
        if self.mode == "no_result":
            rows = [OBS[0], OBS[1] | {"output": "[]", "level": "DEFAULT", "statusMessage": None}]
        if self.mode == "retries":
            rows = OBS + [OBS[2] | {"id": "gen-2", "metadata": {"retry_attempt": 2}}]
        if self.mode == "redirect":
            status = 302
        self.send_response(status)
        if status == 302:
            self.send_header("Location", "/stolen")
        self.end_headers()
        try:
            self.wfile.write(json.dumps({"data": rows, "meta": {"cursor": cursor}}).encode())
        except (BrokenPipeError, ConnectionResetError):
            pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
cfg = {"base_url": f"http://127.0.0.1:{server.server_port}", "wait_seconds": 2}
q = {"id": "Q1", "mode": "sales", "question": "How many?", "split": "dev"}
payload = {"answer": "42", "tools_called": None, "trace_id": "trace-A",
           "session_id": "session-A"}


def emit(data):
    return ("printf %s " + shlex.quote(json.dumps(data))).replace("{", "{{").replace("}", "}}")


def probe(raw, data=None, config=None):
    command = emit(data if data is not None else payload)
    return loop.probe([q], command, langfuse=config or cfg, raw_dir=raw)[0]


try:
    # Removing correlation capture must fail even when Langfuse is disabled.
    row = loop.probe([q], emit(payload))[0]
    assert row.get("trace_id") == "trace-A", "probe discards returned trace_id"
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
        "LANGFUSE_PUBLIC_KEY": "public-test", "LANGFUSE_SECRET_KEY": "secret-test",
    }):
        root = pathlib.Path(directory)
        for mode in ("ok", "pages", "delayed"):
            Handler.mode, Handler.calls = mode, []
            raw = root / mode / "raw"
            row = probe(raw)
            assert row["trace_evidence"]["status"] == "available", row
            assert row["tools_called"][0]["args"]["company"] == "A"
            assert row["tool_results"][0]["result"] == {"count": -2}
            assert row["model_generations"] == 1
            assert (row["input_tokens"], row["cached_input_tokens"], row["output_tokens"]) == (100, 20, 8)
            assert row["latency_ms"] == 2000 and row["cost_usd"] == 0.012
            assert row["trace_url"].endswith("/project/project-A/traces/trace-A")
            evidence = list(raw.glob("langfuse-*.json"))
            assert len(evidence) == 1
            stored = evidence[0].read_text()
            assert all(secret not in stored + json.dumps(row) for secret in (
                "private-token", "other-secret", "secret-test", "public-test"))
            assert "retry_attempt" in stored and "bad count" in stored
            assert evidence[0].stat().st_mode & 0o077 == 0
            for url, auth in Handler.calls:
                query = parse_qs(urlsplit(url).query)
                assert query["traceId"] == ["trace-A"]
                assert "fromStartTime" in query and "toStartTime" in query
                assert auth == "Basic " + base64.b64encode(b"public-test:secret-test").decode()

        # No latest-trace lookup, no ambiguous session lookup, no redirect of credentials.
        for mode, data, reason in (
            ("ok", {"answer": "42", "tools_called": None}, "missing_correlation"),
            ("ok", {"answer": "42", "session_id": "shared"}, "ambiguous_session"),
            ("wrong", payload, "correlation_mismatch"),
            ("quota", payload, "http_429"),
            ("redirect", payload, "http_302"),
            ("malformed", payload, "invalid_response"),
        ):
            Handler.mode, Handler.calls = mode, []
            row = probe(root / reason / "raw", data)
            assert row["answer"] == "42" and row["tools_called"] is None
            assert row["trace_evidence"]["reason"] == reason, row
            assert len(Handler.calls) <= 1

        Handler.mode, Handler.calls = "ok", []
        row = probe(root / "session/raw", {"answer": "42", "session_id": "session-A"},
                    cfg | {"session_per_request": True})
        assert row["model_generations"] == 1
        assert parse_qs(urlsplit(Handler.calls[0][0]).query)["sessionId"] == ["session-A"]

        for mode in ("empty", "slow", "partial"):
            Handler.mode, Handler.calls = mode, []
            start = time.monotonic()
            row = probe(root / mode / "raw", config=cfg | {"wait_seconds": 1})
            assert time.monotonic() - start < 2.5, "Langfuse exceeds its total wait budget"
            assert row["trace_evidence"]["status"] == ("partial" if mode == "partial" else "missing")
            assert row["answer"] == "42" and row["error"] is None

        Handler.mode = "ok"
        row = probe(root / "existing/raw", payload | {"input_tokens": 999, "latency_ms": 17})
        assert row["input_tokens"] == 999 and row["latency_ms"] == 17
        # Sanitizing evidence must not change a JSON-looking answer into an object.
        row = probe(root / "json-answer/raw", payload | {"answer": '{"count":42}'})
        assert isinstance(row["answer"], str) and json.loads(row["answer"]) == {"count": 42}
        Handler.mode = "no_result"
        row = probe(root / "no-result/raw")
        assert "tool_results" not in row, "tool name alone cannot establish captured result evidence"
        assert "input_tokens" not in row and "cost_usd" not in row

        Handler.mode, Handler.calls = "ok", []
        with patch.dict(os.environ, {"LANGFUSE_SECRET_KEY": ""}):
            row = probe(root / "no-credentials/raw")
        assert row["trace_evidence"]["reason"] == "missing_credentials" and not Handler.calls
        Handler.mode = "retries"
        row = probe(root / "retries/raw")
        assert row["model_generations"] == 2 and row["output_tokens"] == 16
        assert row["cost_usd"] == 0.024

        Handler.mode, Handler.calls = "ok", []
        row = loop.probe([q], "sleep 0.2", timeout=0.01, langfuse=cfg,
                         raw_dir=root / "probe-timeout/raw")[0]
        assert row["error"] == "probe timeout"
        assert row.get("trace_evidence", {}).get("status") == "missing"
        assert not Handler.calls

        for invalid in (None, {"wait_seconds": 0}, {"wait_seconds": True},
                        {"secret_key": "not-in-config"}, {"base_url": "http://example.com"},
                        {"base_url": "https://user:password@example.com"}):
            assert loop.validate_config({"goals": loop.DEFAULT_GOALS, "langfuse": invalid}, 1)

        # Private evidence on either split must not be copied into the fix brief.
        private = {"trace_id": "trace-A", "session_id": "session-A",
                   "trace_url": "https://private-trace", "trace_evidence": OBS}
        verdicts = [{"id": "Q1", "improvement_comment": "Fix inventory arguments", **private},
                    {"id": "HIDDEN", "improvement_comment": "reserved conclusion", **private}]
        graded = [{"id": ident, "score": 3, "failure_source": "model",
                   **dict.fromkeys(loop.CRITICAL_FLAGS, False)} for ident in ("Q1", "HIDDEN")]
        brief = loop.build_fix_brief(verdicts, [q | private,
            q | private | {"id": "HIDDEN", "split": "holdout"}],
            {"per_question": graded, "cross_analysis": [], "holdout": {"percent": 60},
             "gap_pp": 0, "hard_gate": False}, [], {"repo": "/product", "allowed_actions": {}})
        assert brief["dev"][0]["improvement_comment"] == "Fix inventory arguments"
        assert all(s not in json.dumps(brief) for s in (
            "HIDDEN", "reserved conclusion", "trace-A", "session-A", "private-trace", "observations"))

        # Resume the real loop: neither the chatbot nor Langfuse may be queried twice.
        run = root / "run"
        run.mkdir()
        golden = run / "golden.jsonl"
        golden.write_text(json.dumps(q) + "\n")
        config = {"schema_version": 2, "goals": loop.DEFAULT_GOALS,
                  "golden_set": str(golden), "golden_sha256": hashlib.sha256(golden.read_bytes()).hexdigest(),
                  "probe_cmd": emit(payload), "langfuse": cfg}
        (run / "config.json").write_text(json.dumps(config))
        for attempt in range(2):
            with patch.object(sys, "argv", ["loop.py", "--run", str(run)]), contextlib.redirect_stdout(io.StringIO()):
                assert loop.main() == 0
            if attempt == 0:
                saved = (run / "iter-01/raw/pack.jsonl").read_bytes()
                calls = len(Handler.calls)
                config["probe_cmd"] = "exit 99"
                (run / "config.json").write_text(json.dumps(config))
            else:
                assert len(Handler.calls) == calls
                assert (run / "iter-01/raw/pack.jsonl").read_bytes() == saved
finally:
    server.shutdown()
    server.server_close()

print("Langfuse integration checks passed")
