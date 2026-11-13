"""Analyst: sort new posts into forums and draft replies worth Wiley's time.

Runs Claude Code headless (claude -p) under the user's subscription.
  python analyst/analyze.py              # new/changed posts since last analysis
  python analyst/analyze.py --days 7     # re-score everything from the last 7 days
Outputs: data/labels.json (id -> f,t), data/drafts.json (id -> score, reason, kind, draft)
"""
import argparse, json, subprocess, sys, re, datetime as dt
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ME = "Wiley Wilkins"


def load(name, default):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def save(name, obj):
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=0), encoding="utf-8")


def iso(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def claude_json(prompt):
    """Call claude -p and return the parsed JSON array from its answer."""
    r = subprocess.run(["claude", "-p", "--output-format", "json", "--max-turns", "1"], input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=600, shell=True)
    if r.returncode != 0:
        print("claude error:", r.stderr[:500]); return None
    try:
        out = json.loads(r.stdout)
        text = out.get("result", "") if isinstance(out, dict) else str(out)
    except Exception:
        text = r.stdout
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        print("no JSON in answer:", text[:300]); return None
    try:
        return json.loads(m.group(0))
    except Exception as e:
        print("bad JSON:", e, text[:300]); return None


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

    for i in range(0, len(todo), args.batch):
        batch = todo[i:i + args.batch]
        items = []
        for pid in batch:
            p = posts[pid]
            cl = sorted(cby.get(pid, []), key=lambda c: c.get("createdAt") or "")[-12:]
            items.append({
                "id": pid, "title": p.get("title", ""), "author": p.get("author", ""), "date": (p.get("createdAt") or "")[:10],
                "likes": p.get("upvotes", 0), "comments": p.get("comments", 0), "in_my_thread": pid in my_threads,
                "text": (p.get("content") or "")[:1800],
                "recent_comments": [{"by": c.get("author"), "text": (c.get("content") or "")[:400]} for c in cl],
            })
        prompt = f"""{voice}

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
            drafts[pid] = {"score": int(r.get("score", 0) or 0), "kind": r.get("kind", ""), "reason": r.get("reason", ""), "draft": (r.get("draft") or "").strip(), "at": now.isoformat(timespec="minutes")}
        print(f"batch {i // args.batch + 1}: {len(res)} analyzed")
        save("labels.json", labels); save("drafts.json", drafts)

    astate["last"] = now.isoformat(timespec="seconds")
    save("analyst_state.json", astate)
    print("done. drafts with text:", sum(1 for d in drafts.values() if d.get("draft")))


if __name__ == "__main__":
    main()
