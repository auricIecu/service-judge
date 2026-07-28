#!/usr/bin/env python3
"""Detect which scorer/orchestrator capabilities are available (LOOP-DESIGN §3).

Prints a JSON capability map for loop.py / humans:
  {"claude_cli": true, "codex_cli": false, "anthropic_api_key": true}
"""
import json
import os
import shutil


def detect() -> dict:
    # ponytail: presence-only checks; real auth probes (a 1-token call) go in
    # loop.py when a run actually starts — detection must stay free and offline
    return {
        "claude_cli": shutil.which("claude") is not None,
        "codex_cli": shutil.which("codex") is not None,
        "anthropic_api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


if __name__ == "__main__":
    caps = detect()
    print(json.dumps(caps, indent=2))
    raise SystemExit(0 if any(caps.values()) else 1)
