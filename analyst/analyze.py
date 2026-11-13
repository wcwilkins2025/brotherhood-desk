"""Analyst: sort new posts into forums and draft replies worth Wiley's time.

Runs Claude Code headless (claude -p) under the user's subscription.
  python analyst/analyze.py              # new/changed posts since last analysis
  python analyst/analyze.py --days 7     # re-score everything from the last 7 days
Outputs: data/labels.json (id -> f,t), data/drafts.json (id -> score, reason, kind, draft)
"""
import argparse, json, os, subprocess, sys, re, datetime as dt
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ME = "Wiley Wilkins"


def claude_env():
    """Environment for the headless claude call: token from .env, and no nested-session guard."""
    env = dict(os.environ)
    envf = ROOT / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                if v.strip():
                    env[k.strip()] = v.strip()
    for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(k, None)
    if not env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        print("note: CLAUDE_CODE_OAUTH_TOKEN is empty in .env; relying on the interactive login")
    return env


def load(name, default):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def save(name, obj):
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=0), encoding="utf-8")


def iso(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def clean(s):
    s = s or ""
    s = re.sub(r"\[/?(ul|ol)\]", "\n", s); s = re.sub(r"\[li\]", "\n- ", s); s = re.sub(r"\[/li\]", "", s)
    s = re.sub(r"\[([^\]\n]{1,120})\]\((https?://[^)\s]+)\)", r"\1 (\2)", s)
    s = re.sub(r"\\([()\[\].\-*_#+!`>~])", r"\1", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def claude_json(prompt):
    """Call claude -p and return the parsed JSON array from its answer."""
    r = subprocess.run(["claude", "-p", "--output-format", "json", "--max-turns", "1"], input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=600, shell=True, env=claude_env())
    try:
        out = json.loads(r.stdout)
        text = out.get("result", "") if isinstance(out, dict) else str(out)
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


LAND = re.compile(r"\b(land|acre|acres|ranch|ranching|cattle|horses?|well|wells|septic|fenc\w*|rural|property|homestead|farm\w*|livestock|water)\b", re.I)
MONEY = re.compile(r"\b(loan|lend|i'?ll (send|pay|cover|wire)|venmo|paypal|cash ?app|send you (some )?money|help (you )?out financially|\$\d)", re.I)
AI = re.compile(r"\b(ai|a\.i\.|claude|chatgpt|gpt|drafted|language model|as an assistant)\b", re.I)
RANCH = re.compile(r"\b(300[- ]acre|ranch|cattle|telephone,? texas)\b", re.I)


def lint(draft, post_text):
    """Mechanical cleanup, then flags Wiley should see before he pastes."""
    d = draft.replace("—", ", ").replace("–", ", ").replace(" , ", ", ").replace(";", ".")
    d = re.sub(r"\s*\([^)]{0,80}\)", "", d)                    # no parentheses
    d = re.sub(r"[ ]{2,}", " ", d)
    flags = []
    if MONEY.search(d):
        flags.append("offers money")
    if AI.search(d):
        flags.append("mentions AI or drafting")
    if RANCH.search(d) and not LAND.search(post_text or ""):
        flags.append("ranch resume in a thread that is not about land")
    sents = [x for x in re.split(r"(?<=[.!?])\s+", d) if x.strip()]
    long = [x for x in sents if len(x.split()) > 22]
    if len(long) >= 2:
        flags.append(f"{len(long)} long sentences")
    if re.search(r"\b(when one of you|some of you|you men need to|you all need)\b", d, re.I):
        flags.append("callout tone")
    if len(d.split()) > 200:
        flags.append("over 200 words")
    return d.strip(), flags


HARD_RULES = """
## Hard rules for every draft (these override anything else)
- Sentences under 15 words. One idea each. No semicolons, no dashes, no parentheses.
- No ranch, cattle or 300 acres unless the post itself is about land, water, animals or rural living.
- Never offer money, a loan, or to pay for anything. Experience only.
- Confession, not callout. Say what you got wrong before what he should do.
- Never mention Claude, AI, drafting, or that anything was written for him.
- No tagging except at the very start of a reply in a busy thread.
- End with one real question when it fits. No sign-off needed.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--limit", type=int, default=60, help="max posts to analyze per run")
    args = ap.parse_args()

    posts = load("posts.json", {})
    comments = load("comments.json", {})
    labels = load("labels.json", {})
    drafts = load("drafts.json", {})
    forum = load("forum_seed.json", {"forums": [], "posts": []})
    seed_labels = {p["id"]: (p["f"], p["t"]) for p in forum["posts"]}
    state = load("state.json", {})
    astate = load("analyst_state.json", {"last": None})
    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=args.days) if args.days else (iso(astate["last"]) if astate.get("last") else now - dt.timedelta(days=3))

    cby = {}
    for c in comments.values():
        cby.setdefault(c["postId"], []).append(c)
    my_threads = {pid for pid, p in posts.items() if p.get("author") == ME} | {c["postId"] for c in comments.values() if c.get("author") == ME}

    todo = []
    for pid, p in posts.items():
        created = iso(p.get("createdAt")); lastc = iso(p.get("lastCommentAt")) if p.get("lastCommentAt") else created
        unlabeled = pid not in labels and pid not in seed_labels
        recent = (created and created >= since) or (lastc and lastc >= since)
        if p.get("author") == ME:
            continue
        if unlabeled or (recent and pid not in drafts) or (pid in my_threads and recent):
            todo.append(pid)
    todo.sort(key=lambda i: posts[i].get("lastCommentAt") or posts[i].get("createdAt") or "", reverse=True)
    todo = todo[: args.limit]
    print(f"{len(todo)} posts to analyze (since {since.isoformat(timespec='minutes')})")
    if not todo:
        return

    taxonomy = "\n".join(f"{f['code']} {f['name']}: " + "; ".join(f"{t['code']} {t['name']}" for t in f.get("threads", [])) for f in forum["forums"])
    voice = (ROOT / "analyst" / "voice.md").read_text(encoding="utf-8")

    analyzed = 0
    for i in range(0, len(todo), args.batch):
        batch = todo[i:i + args.batch]
        items = []
        for pid in batch:
            p = posts[pid]
            cl = sorted(cby.get(pid, []), key=lambda c: c.get("createdAt") or "")[-12:]
            items.append({
                "id": pid, "title": p.get("title", ""), "author": p.get("author", ""), "date": (p.get("createdAt") or "")[:10],
                "likes": p.get("upvotes", 0), "comments": p.get("comments", 0), "in_my_thread": pid in my_threads,
                "text": clean(p.get("content"))[:1800],
                "recent_comments": [{"by": c.get("author"), "text": clean(c.get("content"))[:400]} for c in cl],
            })
        prompt = f"""{voice}
{HARD_RULES}
## Forum taxonomy (code name: thread codes)
{taxonomy}

## Task
For each post below return one JSON object. Output ONLY a JSON array, no prose, no markdown fence.
Fields:
- "id": copy
- "f": forum code (F01..F12) that fits best
- "t": thread code within that forum, or null
- "score": 0-10 per the rules above (how worth Wiley's reply)
- "kind": "answer" | "welcome" | "encourage" | "mythread" | "skip"
- "reason": one short sentence on why this score (shown to Wiley)
- "draft": the reply text in Wiley's voice if score >= 6, else "". A reply, not a post. 40 to 180 words. If "in_my_thread" is true and the latest comments ask him something, answer them; address the man by first name. Never mention being drafted.

## Posts
{json.dumps(items, ensure_ascii=False)}
"""
        res = claude_json(prompt)
        if not res:
            continue
        for r in res:
            pid = r.get("id")
            if pid not in posts:
                continue
            if r.get("f"):
                labels[pid] = {"f": r["f"], "t": r.get("t")}
            d, flags = lint((r.get("draft") or "").strip(), clean(posts[pid].get("content")) + " " + posts[pid].get("title", ""))
            drafts[pid] = {"score": int(r.get("score", 0) or 0), "kind": r.get("kind", ""), "reason": r.get("reason", ""), "draft": d, "flags": flags, "at": now.isoformat(timespec="minutes")}
            analyzed += 1
        print(f"batch {i // args.batch + 1}: {len(res)} analyzed")
        save("labels.json", labels); save("drafts.json", drafts)

    if analyzed:
        astate["last"] = now.isoformat(timespec="seconds")
        save("analyst_state.json", astate)
    else:
        print("nothing analyzed; will retry next run")
    print("done. drafts with text:", sum(1 for d in drafts.values() if d.get("draft")))


if __name__ == "__main__":
    main()
