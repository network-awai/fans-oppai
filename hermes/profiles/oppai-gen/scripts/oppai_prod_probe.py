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
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

PROFILE = Path(__file__).resolve().parents[1]
LEDGER = PROFILE / "workspace" / "oppai-ledger.jsonl"
PREV = PROFILE / "workspace" / ".prev-modelmap.json"
NODES = "aiueos-6600hs-1,gad"
FIXED_IDS = {"health", "index", "modelmap", "video-gate"}
IMAGE = "curlimages/curl@sha256:463eaf6072688fe96ac64fa623fe73e1dbe25d8ad6c34404a669ad3ce1f104b6"
CONTAINER = ("docker run --rm --read-only --network bridge --cap-drop ALL "
             "--security-opt no-new-privileges --pids-limit 32 --memory 128m "
             "--cpus 0.25 --tmpfs /tmp:rw,noexec,nosuid,size=16m " + IMAGE)

def decode_batch(results, expected_ids, require_two_nodes=False):
    if not isinstance(results, list) or len(results) != len(expected_ids):
        raise ValueError("incomplete Murakumo task batch")
    by_id = {row.get("id"): row for row in results}
    if set(by_id) != set(expected_ids):
        raise ValueError("missing or duplicate Murakumo task IDs")
    nodes = {row.get("node") for row in results if row.get("node")}
    if require_two_nodes and len(nodes) < 2:
        raise ValueError("public probes did not use two sandbox nodes")
    readings = {}
    for task_id in expected_ids:
        row = by_id[task_id]
        before_time, time_marker, seconds = str(row.get("stdout", "")).rpartition("__TIME__")
        body, status_marker, code = before_time.rpartition("__HTTP__")
        if not status_marker or not time_marker or not code.isdigit():
            raise ValueError(f"{task_id}: missing HTTP status or duration")
        elapsed = float(seconds.strip())
        if not 0 <= elapsed <= 45:
            raise ValueError(f"{task_id}: invalid request duration")
        status = int(code) if row.get("exit") == 0 and code != "000" else None
        readings[task_id] = (status, int(elapsed * 1000), body)
    return readings, sorted(nodes)


def run_tasks(batch, expected_ids, require_two_nodes=False):
    root = Path(os.environ.get("MURAKUMO_TASK_ROOT") or
                Path.home() / ".itonami-fleet/worktrees/hermes-murakumo-sandbox")
    if not (root / "scripts/run-task.cljk").is_file():
        raise RuntimeError("MURAKUMO_TASK_ROOT has no task runner")
    if not batch.is_file():
        raise RuntimeError(f"task batch missing: {batch.name}")
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([
        "kbb", "--backend", "sci", "scripts/run-task.cljk", "task", "run",
        "--tasks", str(batch), "--nodes", NODES, "--slots", "1",
        "--max-load-per-core", "0.8", "--timeout-ms", "45000",
        "--attempts", "1", "--ledger", str(PROFILE / "workspace/oppai-task-ledger.edn"),
        "--format", "json",
    ], cwd=root, text=True, capture_output=True, timeout=180, check=False)
    lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
    if not lines:
        detail = (proc.stderr or proc.stdout).strip()[-500:]
        raise RuntimeError(f"Murakumo task run failed (exit {proc.returncode}): {detail}")
    return decode_batch(json.loads(lines[-1]).get("results"), expected_ids, require_two_nodes)


def bundle_probe(path):
    if not re.fullmatch(r"/assets/main\.[A-Za-z0-9]+\.js", path):
        raise ValueError("invalid bundle path from shell")
    command = (f"{CONTAINER} -sS -o /dev/null -w '__HTTP__%{{http_code}}__TIME__%{{time_total}}' "
               f"--connect-timeout 5 --max-time 30 -A 'oppai-gen-bot/1.0' https://oppai.fans{path}")
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".edn", prefix="oppai-bundle-",
                                     dir=LEDGER.parent, delete=False) as handle:
        handle.write('{:tasks [{:id "bundle" :cmd ' + json.dumps(command) + '}]}\n')
        batch = Path(handle.name)
    try:
        readings, _ = run_tasks(batch, {"bundle"})
        return readings["bundle"]
    finally:
        batch.unlink(missing_ok=True)


def jparse(text):
    try:
        return json.loads(text)
    except Exception:
        return None


def main():
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = {"ts": ts, "findings": []}
    f = row["findings"]
    try:
        readings, nodes = run_tasks(Path(__file__).with_name("oppai-probe-tasks.edn"),
                                    FIXED_IDS, require_two_nodes=True)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        row["unmeasured"] = f"{type(exc).__name__}: {exc}"
        f.append("remote public probes unmeasured")
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        print(json.dumps({"ok": False, "refused": row["unmeasured"]}, ensure_ascii=False))
        return 2
    row["sandbox_nodes"] = nodes
    scanned = len(readings)

    # ── 1. production smoke: oppai.fans ──────────────────────────────
    status, ms, body = readings["health"]
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
    status, ms, body = readings["index"]
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
    m = body if status == 200 else ""
    bm = re.search(r'(?:src=")?(/assets/main\.[A-Za-z0-9]+\.js)', m)
    if bm:
        try:
            s, _, _ = bundle_probe(bm.group(1))
            scanned += 1
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            s = None
            f.append(f"app bundle unmeasured: {type(exc).__name__}: {exc}")
        row["bundle_status"] = s
        if s != 200:
            f.append(f"app bundle unreachable: {s}")
    else:
        f.append("bundle tag not found in shell HTML")
        row["bundle_status"] = None

    # ── 2. fleet model-map drift ────────────────────────────────────
    status, ms, body = readings["modelmap"]
    row["modelmap_status"] = status
    if status == 200:
        mm = jparse(body)
        if not isinstance(mm, dict) or not isinstance(mm.get("media"), list):
            f.append("model-map response malformed")
        else:
            image_nodes = {}
            for m in mm["media"]:
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
    status, ms, _ = readings["video-gate"]
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
    with LEDGER.open() as fh:
        seq = sum(1 for _ in fh)
    row["ledger_seq"] = seq
    print(f"SCANNED\t{scanned}\tnodes={','.join(nodes)}")
    print(json.dumps({"ok": not f, "ledger_seq": seq,
                      "findings": row["findings"],
                      "snapshot": {k: row[k] for k in
                                   ("health_status", "douga_configured",
                                    "rta_meta", "bundle_status",
                                    "modelmap_status", "video_submit_status",
                                    "video_blocker") if k in row}},
                     ensure_ascii=False))
    return 0 if not f else 1


if __name__ == "__main__":
    raise SystemExit(main())
