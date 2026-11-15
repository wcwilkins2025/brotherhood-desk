"""Forum intelligence: "Brothers near you" and "Ask a brother".

Reads data/posts.json and data/comments.json. Asks Claude (Sonnet, to spare the allowance) to
  1. turn each man's profile location and his words in the location thread into town, state, area
  2. pull skills a man says HE has, in his own words, from posts and comments that describe him
Results are cached in data/intel.json so each man and each post is only read once.
Men listed in data/optout.json (handles or user ids) are left out of everything.

  python analyst/intel.py              # incremental, capped per run
  python analyst/intel.py --limit 2000 # bigger first pass
"""
import argparse, datetime as dt, hashlib, json, re, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import claude_env  # noqa: E402  (same token handling as the drafts)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
sys.stdout.reconfigure(encoding="utf-8")

CATS = ["Trades and building", "Mechanics and equipment", "Land, livestock and farming", "Medical and health care",
        "Military and law enforcement", "Business, sales and money", "Tech and computers", "Outdoors, hunting and firearms",
        "Food and cooking", "Faith and counseling", "Teaching and coaching", "Driving and transport", "Other"]

SELF = re.compile(
    r"\b(i'?m an? |i am an? |i was an? |i'?ve been an? |i have been an? |years (as|in|of) |my trade|i work (as|in|for|at)|"
    r"i worked (as|in|for)|retired (from|as)|by trade|journeyman|apprentice|licensed|certified|i run (a|my|our)|i own (a|my)|"
    r"my (business|shop|company|job|career)|career (in|as)|served (in|as|with)|i build|i raise|i grow|i teach|i train|"
    r"background in|experience (in|with)|i know how to|i can help with|i do (a lot of )?\w+ing for a living|for a living)", re.I)


REC = re.compile(r"\b(i recommend|recommend(ed)?|i use|i'?ve used|i bought|i got mine|i swear by|check out|go with|best (one|brand|place|deal)|great (product|company|brand)|good (brand|company|book)|worth (it|every)|look (at|into)|get (a|an|the) \w+ from)\b", re.I)


def load(name, default):
    f = DATA / name
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else default


def save(name, obj):
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def ask(prompt):
    r = subprocess.run(["claude", "-p", "--model", "sonnet", "--output-format", "json", "--max-turns", "1"],
                       input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=900, shell=True, env=claude_env())
    try:
        out = json.loads(r.stdout); text = out.get("result", "") if isinstance(out, dict) else str(out)
    except Exception:
        out = None; text = r.stdout
    if r.returncode != 0 or (isinstance(out, dict) and out.get("is_error")):
        print("claude error:", (text or r.stderr or "(no message)").strip()[:500]); return None
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        print("no JSON in answer:", text[:300]); return None
    try:
        return json.loads(m.group(0))
    except Exception as e:
        print("bad JSON:", e, text[:300]); return None


def clean(t):
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t or "")
    return re.sub(r"\s+", " ", t).strip()


def h(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


LOC_PROMPT = """You are reading a men's community forum. For each man below you get the location on his profile and
anything he wrote in the "Drop your location below" thread or in "anyone near me" posts. Work out where he lives now.

Rules:
- Town level at most. Never a street, road, county road or anything finer than a town.
- "state": two-letter US state code, or the country name if outside the US. "" if you cannot tell.
- "town": the town or city if he named one, else "".
- "area": the region a neighbor would use, like "Dallas-Fort Worth", "East Texas", "Upstate New York", "Central Florida".
  Use the nearest well-known metro or region. "" if only the state is known.
- If he says he lives one place and travels or works elsewhere, use where he lives.
- If nothing says where he lives, return state "".

Return ONLY a JSON array: [{"u": "<id>", "town": "...", "state": "..", "area": "..."}] with one entry per man.

Men:
"""

SKILL_PROMPT = """You are reading posts and comments from a men's community forum. Find skills, trades and know-how that
the WRITER says HE himself has or has done: his work, his trade, his training, his service, things he has built or raised.

Rules:
- Only what the writer says about himself. Not what he wants to learn, not what someone else does, not advice he read.
- Leave out health conditions, injuries, addiction, money trouble, debts, marriage or family trouble, legal trouble,
  and any guns, armor or gear he owns. Those are private, not skills.
- "skill": short and plain, 1 to 4 words, like "welder", "cattle rancher", "ER nurse", "diesel mechanic", "Army infantry".
- "cat": exactly one of: """ + "; ".join(CATS) + """.
- "q": his own words that show it, copied exactly, at most 20 words.
- Most texts have no skill. Return an empty "s" for those.

Also find RECOMMENDATIONS the writer makes from his own use: a product, brand, store, book, app, course or service
he says is good or that he uses. Not things he is asking about, not things he warns against, not politicians or news.
- "item": the product, brand or store name, 1 to 6 words.
- "kind": one of: gear; tools; food and supplies; land and farm; books and learning; apps and tech; services; other.
- "q": his own words that show it, copied exactly, at most 20 words.
Return an empty "r" when there are none.

Return ONLY a JSON array: [{"i": "<id>", "s": [{"skill": "...", "cat": "...", "q": "..."}], "r": [{"item": "...", "kind": "...", "q": "..."}]}]
with one entry per text.

Texts:
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=400, help="max texts to read for skills this run")
    args = ap.parse_args()

    # one extraction at a time: a long first pass and the hourly run must not both write intel.json
    lock = DATA / "intel.lock"
    if lock.exists() and (dt.datetime.now().timestamp() - lock.stat().st_mtime) < 4 * 3600:
        print("intel: another pass is running, skipping"); return
    lock.write_text(str(dt.datetime.now()), encoding="utf-8")
    try:
        run(args)
    finally:
        lock.unlink(missing_ok=True)


def run(args):
    posts = load("posts.json", {})
    comments = load("comments.json", {})
    intel = load("intel.json", {"loc": {}, "skills": {}})
    opt = set(load("optout.json", []))

    # ---------- locations ----------
    loc_thread = {pid for pid, p in posts.items() if (p.get("slug") or "").startswith("drop-your-location")}
    NEAR = re.compile(r"\b(any(one|body)? (here )?(from|in|near|around)|checking in from|brothers? in|locals?|near me|in my area)\b", re.I)
    men = {}
    for x in list(posts.values()) + list(comments.values()):
        uid = x.get("userId")
        if not uid:
            continue
        m = men.setdefault(uid, {"name": x.get("author", ""), "handle": x.get("handle", ""), "profile": "", "said": []})
        if x.get("location"):
            m["profile"] = x["location"]
        text = clean(((x.get("title") or "") + " " + (x.get("content") or "")) if "title" in x else x.get("content"))
        in_thread = x.get("postId") in loc_thread or x.get("id") in loc_thread
        if text and (in_thread or (NEAR.search(text) and len(text) < 400)):
            m["said"].append(text[:300])
    todo = []
    for uid, m in men.items():
        if not m["profile"] and not m["said"]:
            continue
        key = h(m["profile"] + "|" + "|".join(m["said"][-4:]))
        if intel["loc"].get(uid, {}).get("key") != key:
            todo.append((uid, m, key))
    print(f"locations: {len(todo)} men to read")
    for i in range(0, len(todo), 60):
        chunk = todo[i:i + 60]
        body = "\n".join(json.dumps({"id": uid, "profile": m["profile"], "wrote": m["said"][-4:]}, ensure_ascii=False) for uid, m, _ in chunk)
        res = ask(LOC_PROMPT + body)
        if res is None:
            continue
        got = {r.get("u"): r for r in res if isinstance(r, dict)}
        for uid, m, key in chunk:
            r = got.get(uid, {})
            intel["loc"][uid] = {"key": key, "town": r.get("town", ""), "state": r.get("state", ""), "area": r.get("area", ""),
                                 "name": m["name"], "handle": m["handle"], "profile": m["profile"]}
        save("intel.json", intel)
        print(f"  located {min(i + 60, len(todo))}/{len(todo)}")

    # ---------- skills ----------
    texts = []
    for pid, p in posts.items():
        t = clean((p.get("title") or "") + ". " + (p.get("content") or ""))
        if SELF.search(t) or REC.search(t):
            texts.append((pid, p.get("userId"), p.get("author", ""), p.get("handle", ""), pid, t))
    for cid, c in comments.items():
        t = clean(c.get("content"))
        if SELF.search(t) or REC.search(t):
            texts.append((cid, c.get("userId"), c.get("author", ""), c.get("handle", ""), c.get("postId"), t))
    unread = [x for x in texts if intel["skills"].get(x[0], {}).get("v") != 2]
    todo = unread[: args.limit]
    print(f"skills and picks: {len(unread)} texts unread, reading {len(todo)}")
    for i in range(0, len(todo), 30):
        chunk = todo[i:i + 30]
        body = "\n".join(json.dumps({"id": tid, "text": t[:900]}, ensure_ascii=False) for tid, _, _, _, _, t in chunk)
        res = ask(SKILL_PROMPT + body)
        if res is None:
            continue
        got = {r.get("i"): r for r in res if isinstance(r, dict)}
        for tid, uid, name, handle, pid, t in chunk:
            found = []
            for s in got.get(tid, {}).get("s") or []:
                q = (s.get("q") or "").strip()
                if not s.get("skill") or (q and q.lower()[:40] not in t.lower()):
                    continue  # drop anything whose quote is not really in his text
                found.append({"skill": s["skill"], "cat": s.get("cat") if s.get("cat") in CATS else "Other", "q": q})
            recs = []
            for r in got.get(tid, {}).get("r") or []:
                q = (r.get("q") or "").strip()
                if r.get("item") and (not q or q.lower()[:40] in t.lower()):
                    recs.append({"item": r["item"], "kind": r.get("kind") or "other", "q": q})
            intel["skills"][tid] = {"v": 2, "u": uid, "name": name, "handle": handle, "post": pid, "s": found, "r": recs}
        save("intel.json", intel)
        print(f"  read {min(i + 30, len(todo))}/{len(todo)}")

    intel["optout"] = sorted(opt)
    save("intel.json", intel)
    n_loc = sum(1 for v in intel["loc"].values() if v.get("state"))
    n_sk = sum(len(v["s"]) for v in intel["skills"].values())
    n_r = sum(len(v.get("r") or []) for v in intel["skills"].values())
    print(f"done. men with a place: {n_loc}. skills found: {n_sk}. picks found: {n_r}")


if __name__ == "__main__":
    main()
