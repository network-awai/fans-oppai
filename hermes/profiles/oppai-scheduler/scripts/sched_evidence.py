#!/usr/bin/env python3
"""oppai-scheduler evidence probe — read-only 実測 script.

測るもの（全て read-only、ssh 経由の fleet 観測のみ）:
  1. receipts.edn の直近 48h wall-time per (lane,node) — median/p90
  2. 各 ComfyUI ノードの queue 深度 + pending 保持数
  3. receipts 24h 計数（pass/fail/unmeasured、hourly bucket 欠落検出）
  4. story 提案: 次に割り当てるべきレーン構成を測定値から決定論的に算出

出力: MEASURE<TAB>key<TAB>value 行 + ledger append (workspace/ledger.jsonl)
script が判断を持ち、agent は読んで報告するだけ。
"""
import re, json, statistics, subprocess, os, datetime, sys

HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".itonami/oppai-studio")
RECEIPTS = os.path.join(STATE, "receipts.edn")
LEDGER = os.path.join(HOME, ".hermes/profiles/oppai-scheduler/workspace/ledger.jsonl")
RECEIPT_RE = re.compile(r':([\w-]+)\s+("(?:[^"\\]|\\.)*"|:[\w!?<>+=.-]+|nil|true|false|-?\d+(?:\.\d+)?)')

NODES = ["benjamin","simeon","dan","joseph","zebulun","naphtali","issachar","asher","gad"]  # comfy+gad only; judah/levi are TTS-only (no ComfyUI), excluded from queue probe
LANE_NODE = {
    "real-mix": ["benjamin","simeon"],
    "real-cn": ["dan","joseph"],
    "anime": ["zebulun"],
    "video-ltx": ["naphtali","issachar","asher"],
    "eros-h3": ["gad"],
    "voice": ["judah","levi"],
}

def parse_at(s):
    try:
        return datetime.datetime.fromisoformat(str(s).replace('Z','+00:00'))
    except Exception:
        return None

def read_receipts():
    rows = []
    if not os.path.exists(RECEIPTS):
        return rows
    for line in open(RECEIPTS):
        line = line.strip()
        if not line:
            continue
        rec = {}
        for m in RECEIPT_RE.finditer(line):
            k, v = m.group(1), m.group(2)
            if v.startswith('"'):
                v = v[1:-1]
            elif v.startswith(':'):
                v = v[1:]
            elif v == 'nil':
                v = None
            else:
                try:
                    v = int(v)
                except ValueError:
                    try:
                        v = float(v)
                    except ValueError:
                        pass
            rec[k] = v
        rows.append(rec)
    return rows

def ssh(h, script):
    try:
        r = subprocess.run(["ssh","-o","BatchMode=yes","-o","ConnectTimeout=8",h,"/bin/sh -s"],
                           input=script, capture_output=True, text=True, timeout=40)
        return r.stdout
    except Exception:
        return ""

def node_queue(h):
    out = ssh(h, "curl -s --max-time 6 http://localhost:8188/queue 2>/dev/null ")
    if not out:
        return None
    try:
        q = json.loads(out)
        return len(q.get("queue_running", [])) + len(q.get("queue_pending", []))
    except Exception:
        return None

def main():
    lines = []
    def emit(key, val):
        lines.append(f"MEASURE\t{key}\t{val}")

    rows = read_receipts()
    now = datetime.datetime.now(datetime.timezone.utc)
    recent = [r for r in rows if (t := parse_at(r.get('at'))) and (now - t).total_seconds() < 48*3600]
    h24 = [r for r in recent if (t := parse_at(r.get('at'))) and (now - t).total_seconds() < 24*3600]
    emit("receipts_total", len(rows))
    emit("receipts_24h", len(h24))
    q = {}
    for r in h24:
        k = str(r.get('quality') or 'unknown')
        q[k] = q.get(k, 0) + 1
    emit("quality_24h", json.dumps(q))
    emit("rate_per_hour", round(len(h24)/24, 1))

    # hourly bucket gap detection (24h, UTC hour of receipt)
    buckets = {}
    for r in recent:
        t = parse_at(r.get('at'))
        if t:
            buckets[t.strftime('%m-%dT%H')] = buckets.get(t.strftime('%m-%dT%H'), 0) + 1
    # gap = zero-receipt hours in the last 24h
    gaps = []
    for i in range(24):
        t = now - datetime.timedelta(hours=i)
        k = t.strftime('%m-%dT%H')
        if buckets.get(k, 0) == 0:
            gaps.append(k)
    emit("zero_receipt_hours_24h", len(gaps))
    if gaps:
        emit("gap_hours_sample", ",".join(gaps[:6]))

    # wall-time per lane/node
    by = {}
    for r in recent:
        if r.get('kind') in ('image','video') and isinstance(r.get('wall-s'), (int, float)) and r.get('quality') == 'pass':
            by.setdefault((r.get('lane'), r.get('node')), []).append(r['wall-s'])
    walls = {}
    for (lane, node), ws in sorted(by.items(), key=lambda x: (str(x[0][0]), str(x[0][1]))):
        ws = sorted(ws)
        med = statistics.median(ws)
        p90 = ws[max(0, int(len(ws)*0.9)-1)]
        walls[f"{lane}:{node}"] = {"n": len(ws), "median_s": round(med), "p90_s": round(p90)}
        emit(f"wall_{lane}_{node}", f"n={len(ws)} median={med:.0f}s p90={p90:.0f}s")

    # queue depth per node
    queues = {}
    for h in NODES:
        d = node_queue(h)
        queues[h] = d
        emit(f"queue_{h}", "unmeasured" if d is None else d)

    # saturation: nodes with queue >= 1 (running) are saturated at cap=1
    sat = sum(1 for h, d in queues.items() if isinstance(d, int) and d >= 1)
    unmeasured = [h for h, d in queues.items() if not isinstance(d, int)]
    emit("saturated_nodes", f"{sat}/{len(NODES)}")
    emit("unmeasured_nodes", ",".join(unmeasured) if unmeasured else "none")

    # capacity model: measured throughput vs 1h story cycle
    # 24h receipts / 24 = current hourly throughput. Story = 1 job per lane per hour.
    emit("story_jobs_per_hour", len(LANE_NODE))
    # whether adding story jobs fits: cap room per node
    room = {h: max(0, 2 - (d if isinstance(d, int) else 99)) for h, d in queues.items()}
    emit("cap_room_json", json.dumps(room))

    # deterministic decision from measurements
    gaps_n = len(gaps)
    if gaps_n >= 4:
        verdict = f"tick_gap_detected:{gaps_n}h"
    elif sat == len(NODES):
        verdict = "saturated:cap2_upgrade_or_prioritize"
    else:
        verdict = "healthy"
    emit("verdict", verdict)

    # ledger append (append-only)
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    entry = {
        "at": now.isoformat(timespec="seconds"),
        "receipts_24h": len(h24),
        "rate_per_hour": round(len(h24)/24, 1),
        "quality": q,
        "walls": walls,
        "queues": queues,
        "saturated": sat,
        "zero_hours": gaps_n,
        "verdict": verdict,
    }
    with open(LEDGER, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    for ln in lines:
        print(ln)
    print(f"LEDGER\t{LEDGER}\tseq={sum(1 for _ in open(LEDGER))}")

if __name__ == "__main__":
    main()
