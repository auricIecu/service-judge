"""Optional, read-only Langfuse v2 evidence. Stdlib only; never a truth source."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


SECRET_FIELD = re.compile(
    r"authorization|cookie|password|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|credential|(?:^|[_-])token$|^auth$",
    re.I,
)


def redact(value):
    if isinstance(value, dict):
        return {k: "[redacted]" if SECRET_FIELD.search(k) else redact(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        # v2 delivers input/output as strings, including serialized JSON.
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            return json.dumps(redact(parsed), ensure_ascii=False)
        for key, secret in os.environ.items():
            if secret and (key.startswith("LANGFUSE_") and key.endswith("_KEY")
                           or len(secret) >= 8 and SECRET_FIELD.search(key)):
                value = value.replace(secret, "[redacted]")
        return re.sub(r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9+/=._~-]+",
                      "[redacted authorization]", value)
    return value


def config_errors(cfg):
    if not isinstance(cfg, dict):
        return ["langfuse must be an object (omit it to disable)"]
    errors = []
    if set(cfg) - {"base_url", "wait_seconds", "session_per_request", "tool_names"}:
        errors.append("unknown langfuse setting; credentials belong in environment variables")
    wait = cfg.get("wait_seconds", 10)
    if isinstance(wait, bool) or not isinstance(wait, int) or not 1 <= wait <= 60:
        errors.append("langfuse.wait_seconds must be an integer 1-60")
    if not isinstance(cfg.get("session_per_request", False), bool):
        errors.append("langfuse.session_per_request must be boolean")
    names = cfg.get("tool_names", [])
    if not isinstance(names, list) or any(not isinstance(n, str) or not n for n in names):
        errors.append("langfuse.tool_names must be a list of non-empty names")
    try:
        url = urlsplit(cfg.get("base_url", os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")))
        if (not url.hostname or url.username or url.password or url.query or url.fragment
                or (url.scheme != "https" and not (
                    url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1")))):
            raise ValueError
        url.port
    except (ValueError, TypeError, AttributeError):
        errors.append("langfuse.base_url requires HTTPS (HTTP only on loopback), without credentials/query/fragment")
    return errors


def save(path, snapshot):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
        temporary = pathlib.Path(f.name)
        try:
            json.dump(redact(snapshot), f, ensure_ascii=False)
            f.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def collect(request, path):
    cfg, row = request["config"], request["row"]
    snapshot = {"question_id": row["id"], "captured_at": datetime.now(timezone.utc).isoformat(),
                "status": "missing", "reason": "timeout", "observations": []}
    for key in ("trace_id", "session_id"):
        if row.get(key) is not None:
            snapshot[key] = row[key]
    trace_id, session_id = row.get("trace_id"), row.get("session_id")
    key, value = ("traceId", trace_id) if trace_id is not None else ("sessionId", session_id)
    if not isinstance(value, str) or not value.strip():
        snapshot["reason"] = "missing_correlation"
    elif key == "sessionId" and not cfg.get("session_per_request"):
        snapshot["reason"] = "ambiguous_session"
    elif not all(os.getenv(k) for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")):
        snapshot["reason"] = "missing_credentials"
    else:
        snapshot["reason"] = "waiting"
    save(path, snapshot)
    if snapshot["reason"] != "waiting":
        return

    auth = base64.b64encode((os.environ["LANGFUSE_PUBLIC_KEY"] + ":" +
                            os.environ["LANGFUSE_SECRET_KEY"]).encode()).decode()
    base = cfg.get("base_url", os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")).rstrip("/")
    end = time.monotonic() + cfg.get("wait_seconds", 10)
    params = {key: value, "limit": 100,
              "fields": "core,basic,time,io,metadata,model,usage,metrics",
              "fromStartTime": request["from_start_time"],
              "toStartTime": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()}
    opener = build_opener(NoRedirect())
    previous = None
    while time.monotonic() < end:
        rows, cursors, size = {}, set(), 0
        params.pop("cursor", None)
        try:
            while True:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                req = Request(base + "/api/public/v2/observations?" + urlencode(params),
                              headers={"Authorization": "Basic " + auth})
                with opener.open(req, timeout=min(remaining, 5)) as response:
                    body = response.read(8 * 1024 * 1024 + 1)
                size += len(body)
                if size > 8 * 1024 * 1024:
                    raise ValueError
                page = json.loads(body)
                if not isinstance(page, dict) or not isinstance(page.get("data"), list):
                    raise ValueError
                for observation in page["data"]:
                    if (not isinstance(observation, dict)
                            or not isinstance(observation.get("id"), str)
                            or not isinstance(observation.get("traceId"), str)):
                        raise ValueError
                    if observation.get(key) != value:
                        snapshot = snapshot | {"status": "missing", "reason": "correlation_mismatch",
                                               "observations": []}
                        save(path, snapshot)
                        return
                    for io_key in ("input", "output"):
                        if isinstance(observation.get(io_key), str):
                            try:
                                observation[io_key] = json.loads(observation[io_key])
                            except (ValueError, RecursionError):
                                pass
                    rows[(observation["traceId"], observation["id"])] = observation
                if rows:
                    snapshot.update(status="partial", reason="collecting", observations=list(rows.values()))
                    save(path, snapshot)
                meta = page.get("meta", {})
                if not isinstance(meta, dict):
                    raise ValueError
                cursor = meta.get("cursor")
                if not cursor:
                    break
                if not isinstance(cursor, str) or cursor in cursors or len(cursors) >= 100:
                    raise ValueError
                cursors.add(cursor)
                params["cursor"] = cursor
        except HTTPError as exc:
            snapshot["reason"] = f"http_{exc.code}"
            if exc.code not in (404, 408, 500, 502, 503, 504):
                save(path, snapshot)
                return
        except (URLError, TimeoutError, OSError):
            snapshot["reason"] = "transport_error"
        except (ValueError, TypeError, RecursionError):
            snapshot["reason"] = "invalid_response"
            save(path, snapshot)
            return
        else:
            ordered = sorted(rows.values(), key=lambda r: (str(r.get("startTime", "")), r["id"]))
            traces = {r["traceId"] for r in ordered}
            closed_roots = {r["traceId"] for r in ordered
                            if not r.get("parentObservationId") and r.get("endTime")}
            closed = all(r.get("endTime") or r.get("type") == "EVENT" for r in ordered)
            # ponytail: two stable reads are a snapshot, not proof all SDK batches
            # arrived; use an application completion manifest if that guarantee is needed.
            if ordered and ordered == previous and traces == closed_roots and closed:
                snapshot.update(status="available", reason="stable_snapshot", observations=ordered)
                save(path, snapshot)
                return
            previous = ordered
            snapshot["reason"] = "timeout"
        save(path, snapshot)
        time.sleep(min(0.5, max(0, end - time.monotonic())))


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def normalized(snapshot, cfg, has_result):
    rows = snapshot.get("observations", [])
    fields = {}
    tools = [r for r in rows if r.get("type") == "TOOL" or
             r.get("type") == "SPAN" and r.get("name") in cfg.get("tool_names", [])]
    generations = [r for r in rows if r.get("type") == "GENERATION"]
    if tools:
        fields["tools_called"] = [{"name": r.get("name"), "args": r.get("input"),
                                   "observation_id": r["id"]} for r in tools]
        results = [{"name": r.get("name"), "result": r.get("output"),
                    "observation_id": r["id"], "error": (r.get("statusMessage") or "ERROR") if r.get("level") == "ERROR" else None}
                   for r in tools if has_result(r.get("output")) or r.get("level") == "ERROR"]
        if results:
            fields["tool_results"] = results
    if generations:
        fields["model_generations"] = len(generations)
        models = {r["model"] for r in generations if isinstance(r.get("model"), str)}
        if len(models) == 1:
            fields["model"] = models.pop()
        for target, prefix, explicit in (("input_tokens", "input", "inputUsage"),
                                         ("output_tokens", "output", "outputUsage"),
                                         ("cached_input_tokens", "input_cached", "cachedInputUsage")):
            values = []
            for r in generations:
                usage = r.get("usageDetails") or {}
                value = r.get(explicit)
                if not number(value) and isinstance(usage, dict):
                    parts = [v for k, v in usage.items() if k == prefix or k.startswith(prefix + "_")]
                    value = sum(parts) if parts and all(number(v) for v in parts) else None
                values.append(value)
            if all(number(v) for v in values):
                fields[target] = sum(values)
        if all(number(r.get("totalCost")) for r in generations):
            fields["cost_usd"] = sum(r["totalCost"] for r in generations)
    roots = [r for r in rows if not r.get("parentObservationId")]
    try:
        if roots:
            start = min(datetime.fromisoformat(r["startTime"].replace("Z", "+00:00")) for r in roots)
            end = max(datetime.fromisoformat(r["endTime"].replace("Z", "+00:00")) for r in roots)
            if end >= start:
                fields["latency_ms"] = round((end - start).total_seconds() * 1000)
    except (KeyError, ValueError, TypeError, AttributeError):
        pass
    trace_ids = {r["traceId"] for r in rows}
    projects = {r.get("projectId") for r in rows if isinstance(r.get("projectId"), str)}
    if len(trace_ids) == len(projects) == 1:
        base = cfg.get("base_url", os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")).rstrip("/")
        fields["trace_url"] = base + "/project/" + quote(projects.pop(), safe="") + "/traces/" + quote(trace_ids.pop(), safe="")
    return fields


def enrich(row, cfg, raw_dir, started_at, has_result):
    """Persist sanitized evidence and fill absent telemetry. Never probe the service."""
    raw_dir = pathlib.Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw_dir.chmod(0o700)
    path = raw_dir / ("langfuse-" + hashlib.sha256(row["id"].encode()).hexdigest()[:16] + ".json")
    request = {"config": cfg, "row": {k: row[k] for k in ("id", "trace_id", "session_id") if k in row},
               "from_start_time": (started_at - timedelta(minutes=5)).isoformat()}
    save(path, {"status": "missing", "reason": "timeout", "observations": []})
    failure = None
    try:
        # A process timeout also bounds DNS, pagination and slow-drip HTTP bodies.
        subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve()), str(path)],
                       input=json.dumps(request), text=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=cfg.get("wait_seconds", 10), check=True)
    except subprocess.TimeoutExpired:
        failure = "timeout"
    except (subprocess.CalledProcessError, OSError):
        failure = "collector_failed"
    snapshot = json.loads(path.read_text())
    if failure and snapshot["status"] != "available":
        snapshot["reason"] = failure
        save(path, snapshot)
    safe_row = redact(row)
    for key, value in normalized(snapshot, cfg, has_result).items():
        if safe_row.get(key) is None:
            safe_row[key] = value
    safe_row["trace_evidence"] = snapshot
    return safe_row


if __name__ == "__main__":
    collect(json.load(sys.stdin), pathlib.Path(sys.argv[1]))
