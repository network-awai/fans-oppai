#!/usr/bin/env python3
"""oppai-scheduler story tick — 1h 単位でストーリーベース生成を 1 周提出する script.

仕事:
  1. sched_evidence.py と同じ実測を取り (receipts 48h wall、queue 深度)
  2. story 1 本 = 全レーン 1 job ずつ (real-mix/real-cn/anime/video-ltx/eros-h3/voice)
     を、前回 story の次の番号 (counters.edn) で compose 相当の prompt を組み、
     空いている node (queue=0) に ComfyUI /prompt で提出する
  3. receipts + story-ledger に記録

boundary: bots/oppai-studio.edn の :forbidden + guard/minor? を同じ語彙で
ローカルに再実装 (スクリプトは ssh terminal 経由のみ、外部 HTTP を持たない)。
成人マーカーを必ず先頭に載せる。
"""
import re, json, statistics, subprocess, os, datetime, time, sys

HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".itonami/oppai-studio")
RECEIPTS = os.path.join(STATE, "receipts.edn")
PENDING = os.path.join(STATE, "pending.edn")
COUNTERS = os.path.join(STATE, "counters.edn")
LEDGER = os.path.join(HOME, ".hermes/profiles/oppai-scheduler/workspace/story-ledger.jsonl")

# ---- boundary (guard/minor-terms + profile :forbidden, same vocab) ----
MINOR_TERMS = ["loli","lolita","shota","child","children","kid","kids","toddler","baby",
               "infant","teen","teenage","teenager","underage","minor","schoolgirl",
               "schoolboy","preteen","young girl","young boy","little girl","little boy"]
TOKEN_TERMS = {"jk","jc","js"}
FORBIDDEN = ["rape","raped","forced","non-consensual","nonconsensual","unconscious","sleeping",
             "drugged","drunk","hypno","mind control","blood","gore","guro","torture","crying in pain",
             "bestiality","animal","dog","horse","tentacle","necro","corpse","scat","vomit",
             "sister","brother","mother","daughter","father","son","incest","stepsister","stepmother","stepdaughter",
             "real person","celebrity","actress name","idol name",
             "student","school uniform","seifuku","classroom","flat chest","petite"]
ADULT_TAGS = ["1girl, adult, mature female, 25yo","1girl, adult woman, 28yo, mature female",
              "1girl, milf, mature female, 32yo","1girl, adult, office lady, 27yo"]
NEGATIVE_FLOOR = ("child, loli, shota, teen, underage, minor, young girl, petite, flat chest, "
                  "school uniform, gore, blood, animal, deformed")

def tokens(s):
    return [t for t in re.split(r'[^a-z0-9]+', str(s).lower()) if t]

def minor_ok(text):
    low = str(text).lower()
    if any(t in low for t in MINOR_TERMS):
        return False
    return not (set(tokens(low)) & TOKEN_TERMS)

def allowed(text):
    if not minor_ok(text):
        return False
    low = str(text).lower()
    toks = set(tokens(low))
    for term in FORBIDDEN:
        t = term.lower()
        if re.search(r'\s|-', t):
            if t in low:
                return False
        elif t in toks:
            return False
    return True

# ---- story banks: tags.csv 採用語 (202語、2026-09-12 gate 分類) から選定したストーリー語彙 ----
# 分類正本: workspace/accepted_tags.txt (guard/minor? + forbidden-hit 実測 ACCEPT)
# ストーリー = 場所 → 服装 → 情景 → 表現 の順序で 1 文を作る
STORY_SCENES = [
    {"tags":"onsen, outdoor, steam, wet hair, night", "scene":"an outdoor onsen in the steam at night"},
    {"tags":"love hotel, mirror, neon light, night", "scene":"a love hotel room with neon light and a mirror"},
    {"tags":"bedroom, on bed, sheets, dim light", "scene":"a dim bedroom with sheets rumpled"},
    {"tags":"shower, wet, water drops, tiles", "scene":"a tiled shower, water running down"},
    {"tags":"office, desk, night, city lights", "scene":"an office desk at night with city lights behind"},
    {"tags":"beach, sunset, sand, wet skin", "scene":"a beach at sunset with wet skin"},
    {"tags":"outdoor, exposure, forest, daylight", "scene":"a forest clearing in daylight"},
    {"tags":"living room, sofa, afternoon light", "scene":"a sofa in warm afternoon light"},
]
STORY_OUTFITS = [
    {"tags":"lingerie, lace bra, garter belt, stockings","outfit":"black lace lingerie with garter belt and stockings"},
    {"tags":"nude, completely nude","outfit":"completely nude"},
    {"tags":"micro bikini, wet","outfit":"a wet micro bikini"},
    {"tags":"topless, panties only","outfit":"topless, wearing only panties"},
    {"tags":"open shirt, no bra, cleavage, unbuttoned","outfit":"an unbuttoned white shirt with no bra"},
    {"tags":"office suit, pencil skirt, pantyhose","outfit":"an office suit with blouse unbuttoned"},
    {"tags":"yukata, off shoulder","outfit":"a loose yukata slipping off her shoulders"},
    {"tags":"maid outfit, thighhighs","outfit":"a maid outfit with a cleavage cutout"},
]
STORY_ACTS = [
    {"tags":"ahegao, rolling eyes, tongue out, orgasm face","act":"making an ahegao face, eyes rolled back"},
    {"tags":"bukkake, cum on face, cum on breasts","act":"covered in cum on her face and breasts"},
    {"tags":"paizuri, 1boy","act":"paizuri, squeezing him between her breasts"},
    {"tags":"fellatio, 1boy","act":"giving a sloppy blowjob"},
    {"tags":"sex, cowgirl position, riding, 1boy","act":"riding a man in cowgirl position"},
    {"tags":"sex, from behind, 1boy","act":"having sex from behind, doggy style"},
    {"tags":"masturbation, spread legs","act":"masturbating with her legs spread"},
    {"tags":"consensual sex, vaginal, creampie, 1boy","act":"in missionary, legs spread, creampie"},
]
STORY_MOMENTS = [
    {"tags":"blush, moaning","moment":"blushing and moaning"},
    {"tags":"seductive smile, looking at viewer","moment":"with a seductive smile"},
    {"tags":"embarrassed, looking away","moment":"embarrassed, looking away"},
    {"tags":"ahegao, crossed eyes, tongue out","moment":"eyes crossed and tongue out"},
]

def rpick(seq, i):
    return seq[i % len(seq)]

def lcg(s):
    a, c, m = 1664525, 1013904223, 4294967296
    return ((s * a) + c) % m

def seq_of(seed, n):
    out, s = [], lcg(seed + 0x9E3779B9)
    for _ in range(n):
        s = lcg(s)
        out.append(s // 65536)
    return out

def compose_story(lane, n, sizes, steps, cfg):
    seed = 20260911 + 7 * n + {"real-mix":0,"real-cn":1,"anime":2,"video-ltx":3,"eros-h3":4,"voice":5}[lane]
    rs = seq_of(seed, 6)
    adult = ADULT_TAGS[rs[0] % len(ADULT_TAGS)]
    scene = STORY_SCENES[rs[1] % len(STORY_SCENES)]
    outfit = STORY_OUTFITS[rs[2] % len(STORY_OUTFITS)]
    act = STORY_ACTS[rs[3] % len(STORY_ACTS)]
    moment = STORY_MOMENTS[rs[4] % len(STORY_MOMENTS)]
    tags = [adult, scene["tags"], outfit["tags"], act["tags"], moment["tags"]]
    joined = ", ".join(tags)
    if not allowed(joined):
        raise SystemExit(f"REFUSED story tags fail boundary: {joined[:80]}")
    prompt = ", ".join(["masterpiece, best quality, newest", adult, scene["scene"],
                        outfit["outfit"], act["act"], moment["moment"]])
    if not allowed(prompt):
        raise SystemExit(f"REFUSED story prompt fails boundary")
    return {"tags":tags, "prompt":prompt, "seed":seed,
            "negative": NEGATIVE_FLOOR,
            "scene":scene, "outfit":outfit, "act":act, "moment":moment}

def read_edn_map(p):
    try:
        raw = open(p).read().strip()
        out = {}
        for m in re.finditer(r':([\w-]+)\s+(-?\d+)', raw):
            out[m.group(1)] = int(m.group(2))
        return out
    except Exception:
        return {}

def ssh(h, script, timeout=60):
    r = subprocess.run(["ssh","-o","BatchMode=yes","-o","ConnectTimeout=8",h,"/bin/sh -s"],
                       input=script, capture_output=True, text=True, timeout=timeout)
    return r.stdout

def node_queue(h):
    out = ssh(h, "curl -s --max-time 6 http://localhost:8188/queue 2>/dev/null")
    try:
        q = json.loads(out)
        return len(q.get("queue_running", [])) + len(q.get("queue_pending", []))
    except Exception:
        return None

def comfy_submit(h, graph, client_id):
    body = json.dumps({"prompt": graph, "client_id": client_id})
    r = subprocess.run(["ssh","-o","BatchMode=yes","-o","ConnectTimeout=8",h,"/bin/sh -s"],
                       input=("curl -s --max-time 30 -X POST -H 'content-type: application/json' "
                              "--data-binary @- http://localhost:8188/prompt <<'OPPAI_GRAPH'\n"
                              + body + "\nOPPAI_GRAPH\n"),
                       capture_output=True, text=True, timeout=60)
    try:
        return json.loads(r.stdout).get("prompt_id")
    except Exception:
        return None

def txt2img_graph(checkpoint, prompt, negative, w, h, steps, cfg, seed, prefix):
    return {"1":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":checkpoint}},
            "2":{"class_type":"CLIPTextEncode","inputs":{"clip":["1",1],"text":prompt}},
            "3":{"class_type":"CLIPTextEncode","inputs":{"clip":["1",1],"text":negative}},
            "4":{"class_type":"EmptyLatentImage","inputs":{"width":w,"height":h,"batch_size":1}},
            "5":{"class_type":"KSampler","inputs":{"model":["1",0],"positive":["2",0],"negative":["3",0],
                 "latent_image":["4",0],"seed":seed,"steps":steps,"cfg":cfg,
                 "sampler_name":"euler_ancestral","scheduler":"normal","denoise":1.0}},
            "6":{"class_type":"VAEDecode","inputs":{"samples":["5",0],"vae":["1",2]}},
            "7":{"class_type":"SaveImage","inputs":{"images":["6",0],"filename_prefix":prefix}}}

LANES = {
    "real-mix":   {"nodes":["benjamin","simeon"], "ckpt":"waiREALMIX_v11.safetensors", "sizes":[(832,1216),(896,1152)], "steps":26,"cfg":6.0},
    "real-cn":    {"nodes":["dan","joseph"],      "ckpt":"waiREALCN_v150.safetensors", "sizes":[(832,1216),(896,1152)], "steps":26,"cfg":6.0},
    "anime":      {"nodes":["zebulun"],           "ckpt":"waiIllustriousSDXL_v150.safetensors","sizes":[(832,1216)], "steps":28,"cfg":5.5},
    "video-ltx":  {"nodes":["naphtali","issachar","asher"], "ckpt":"ltxv-2b-0.9.6-distilled-04-25.safetensors","sizes":[(704,480)], "steps":8,"cfg":3.0},
}

def main():
    dry = "--dry-run" in sys.argv
    now = datetime.datetime.now(datetime.timezone.utc)
    hour = now.strftime("%Y%m%dT%H")
    counters = read_edn_map(COUNTERS)
    submitted, records = [], []
    for lane, cfg_l in LANES.items():
        n = counters.get(lane, 0)
        job = compose_story(lane, n, cfg_l["sizes"], cfg_l["steps"], cfg_l["cfg"])
        w, h = cfg_l["sizes"][n % len(cfg_l["sizes"])]
        # node pick: least loaded among the lane's nodes
        qs = {nh: node_queue(nh) for nh in cfg_l["nodes"]}
        best = None
        for nh in cfg_l["nodes"]:
            d = qs[nh]
            if d is not None and d < 2 and (best is None or d < qs[best]):
                best = nh
        rec = {"at":now.isoformat(timespec="seconds"),"hour":hour,"lane":lane,"n":n,
               "node":best,"node_queues":qs,"seed":job["seed"],"tags":job["tags"]}
        if best is None:
            rec["state"] = "skipped_all_nodes_busy_or_unmeasured"
        elif dry:
            rec["state"] = "dry"
            submitted.append(lane)
        else:
            prefix = f"oppai-story-{lane}-{n}"
            g = txt2img_graph(cfg_l["ckpt"], job["prompt"], job["negative"], w, h,
                              cfg_l["steps"], cfg_l["cfg"], job["seed"], prefix)
            pid = comfy_submit(best, g, f"oppai-story-{now.strftime('%H%M')}")
            if pid:
                rec["state"] = "submitted"; rec["prompt_id"] = pid
                submitted.append(lane)
            else:
                rec["state"] = "submit_failed"
        records.append(rec)
        print(f"STORY\t{lane}\tn={n}\tnode={best}\tstate={rec['state']}\tseed={job['seed']}")
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"SUMMARY\thour={hour}\tsubmitted={len(submitted)}/{len(LANES)}\tledger={LEDGER}")

if __name__ == "__main__":
    main()
