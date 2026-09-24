#!/usr/bin/env python3
"""oppai-gen pre-run measurement script (no_agent).

Measures the oppai.fans production surface and the murakumo generation
backend, appends one JSON line to workspace/oppai-ledger.jsonl, and prints
a short summary for the agent (or cron history) to relay.

All network calls are read-only GET/POST probes. Auth: none needed for the
public routes. The video submit probe uses no token — it EXPECTS 401 and
records the blocker state instead of guessing secrets.

Judgement lives here, not in the agent. Failures are recorded as failures.
"""
import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

PROFILE = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes/profiles/oppai-gen"))
LEDGER = PROFILE / "workspace" / "oppai-ledger.jsonl"
PREV = PROFILE / "workspace" / ".prev-modelmap.json"

UA = {"User-Agent": "oppai-gen-bot/1.0 (itonami fleet; +https://oppai.fans)"}


def probe(url, method="GET", body=None, headers=None, timeout=30):
    """Return (status, ms, body_head). Never raises."""
    start = time.monotonic()
    try:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={**UA, **(headers or {}),
                                              "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read(200_000).decode("utf-8", "replace")
            return r.status, int((time.monotonic() - start) * 1000), text
    except urllib.error.HTTPError as e:
        return e.code, int((time.monotonic() - start) * 1000), ""
    except Exception as e:
        return None, int((time.monotonic() - start) * 1000), str(e)[:120]


def jparse(text):
    try:
        return json.loads(text)
    except Exception:
        return None


def main():
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = {"ts": ts, "findings": []}
    f = row["findings"]

    # ── 1. production smoke: oppai.fans ──────────────────────────────
    status, ms, body = probe("https://oppai.fans/api/health")
    health = jparse(body) if status == 200 else None
    row["health_status"] = status
    row["health_ms"] = ms
    if status != 200 or not (health or {}).get("ok"):
        f.append(f"health probe failed: status={status}")
    else:
        douga = (health or {}).get("douga", {})
        row["douga_configured"] = douga.get("configured")
        if not douga.get("configured"):
            f.append("douga configured:false — MURAKUMO_GENERATION_TOKEN secret missing")

    # gate + shell
    status, ms, body = probe("https://oppai.fans/")
    row["index_status"] = status
    if status != 200:
        f.append(f"index 200 missing: status={status}")
    else:
        has_rta = "RTA-5042" in body
        has_gate_title = "R18" in body
        row["rta_meta"] = has_rta
        if not (has_rta and has_gate_title):
            f.append("R18 declaration missing from shell (RTA meta or title)")

    # static assets reachability (bundle name from the shell HTML)
    import re
    m = body if status == 200 else ""
    bm = re.search(r'(?:src=")?(/assets/main\.[A-Za-z0-9]+\.js)', m)
    if bm:
        s, _, _ = probe("https://oppai.fans" + bm.group(1))
        bundle_ok = s
        row["bundle_status"] = s
        if s != 200:
            f.append(f"app bundle unreachable: {s}")
    else:
        f.append("bundle tag not found in shell HTML")
        row["bundle_status"] = None

    # ── 2. fleet model-map drift ────────────────────────────────────
    status, ms, body = probe("https://api.murakumo.cloud/infer/model-map")
    row["modelmap_status"] = status
    if status == 200:
        mm = jparse(body) or {}
        media = mm.get("media", [])
        image_nodes = {}
        for m in media:
            if m.get("model-kind") == "image":
                image_nodes.setdefault(m.get("model-id"), set()).add(m.get("node"))
        image_nodes = {k: sorted(v) for k, v in image_nodes.items()}
        row["image_models"] = {k: v for k, v in sorted(image_nodes.items())}

        prev = None
        if PREV.exists():
            try:
                prev = json.loads(PREV.read_text())
            except Exception:
                prev = None
        if prev is not None and prev != image_nodes:
            gone = sorted(set(prev) - set(image_nodes))
            new = sorted(set(image_nodes) - set(prev))
            moved = sorted(k for k in set(prev) & set(image_nodes)
                           if prev[k] != image_nodes[k])
            f.append(f"fleet image-model drift: gone={gone} new={new} moved={moved}")
        PREV.write_text(json.dumps(image_nodes, sort_keys=True))
    else:
        f.append(f"model-map unreachable: status={status}")

    # ── 3. video gate state (read-only: expect 401 with no token) ────
    # 401 = gate ON and rejecting anonymous callers (healthy). The auth+
    # billing chain was live-verified 2026-09-05 (402 insufficient-credits
    # and queued jobs observed through the oppai Worker); this probe cannot
    # spend real credits, so it only asserts the gate is up.
    status, ms, _ = probe(
        "https://generation.murakumo.cloud/api/v1/generation",
        method="POST",
        body={"type": "video", "model": "ltx-2.3",
              "input": {"prompt": "bot probe"},
              "params": {"width": 768, "height": 448, "frames": 9}},
        timeout=30)
    row["video_submit_status"] = status
    if status == 401:
        row["video_blocker"] = None
    elif status in (400, 402):
        # gate passed but request rejected — anonymous callers should never
        # reach billing, so this is suspicious, not healthy
        row["video_blocker"] = f"gate accepted anonymous caller ({status})"
        f.append(f"video gate NOT rejecting anonymous callers: {status}")
    else:
        row["video_blocker"] = f"unexpected status {status}"
        f.append(f"video probe unexpected status: {status}")

    # ── ledger append (append-only) ──────────────────────────────────
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    # seq = line count
    seq = sum(1 for _ in LEDGER.open())
    row["ledger_seq"] = seq
    print(json.dumps({"ok": True, "ledger_seq": seq,
                      "findings": row["findings"],
                      "snapshot": {k: row[k] for k in
                                   ("health_status", "douga_configured",
                                    "rta_meta", "bundle_status",
                                    "modelmap_status", "video_submit_status",
                                    "video_blocker") if k in row}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
