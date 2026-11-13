"""Manage the bump experiment list (data/experiment_bumps.json).

  python analyst/experiment.py candidates            # Dallas's quiet high-participation posts not yet in the list
  python analyst/experiment.py add <slug> [why]       # add a post
  python analyst/experiment.py remove <slug>
  python analyst/experiment.py swap <old-slug> <new-slug> [why]
  python analyst/experiment.py bump <slug> [YYYY-MM-DD]   # log that you commented on it (default today)
  python analyst/experiment.py window <slug> <text>   # note a time window, e.g. "late November"
Slugs can be a unique prefix.
"""
import json, sys, re, datetime as dt
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
DATA = Path(__file__).resolve().parents[1] / "data"
F = DATA / "experiment_bumps.json"
exp = json.loads(F.read_text(encoding="utf-8"))
posts = json.loads((DATA / "posts.json").read_text(encoding="utf-8"))
by_slug = {p["slug"]: p for p in posts.values() if p.get("slug")}


def find(prefix):
    hits = [s for s in by_slug if s == prefix] or [s for s in by_slug if s.startswith(prefix)]
    if len(hits) != 1:
        sys.exit(f"slug '{prefix}' matches {len(hits)} posts: {hits[:5]}")
    return hits[0]


def save():
    F.write_text(json.dumps(exp, ensure_ascii=False, indent=1), encoding="utf-8")


def entry(slug, why=""):
    p = by_slug[slug]
    return {"slug": slug, "title": p.get("title", ""), "date": (p.get("createdAt") or "")[:10], "comments": p.get("comments", 0), "likes": p.get("upvotes", 0), "why": why}


def candidates(n=20):
    have = {e["slug"] for e in exp["posts"]}
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).isoformat()
    rows = []
    for p in posts.values():
        if p.get("author") != "Dallas James Wicker" or p.get("slug") in have or p.get("comments", 0) < 25:
            continue
        if (p.get("lastCommentAt") or "") >= cutoff:
            continue
        ask = bool(re.search(r"\?|comment|tell me|below|drop|share|reply", (p.get("title", "") + " " + (p.get("content") or "")[:400]).lower()))
        rows.append((p["comments"], p["upvotes"], ask, p["slug"], p["title"], (p.get("createdAt") or "")[:10]))
    rows.sort(reverse=True)
    return rows[:n]


cmd = sys.argv[1] if len(sys.argv) > 1 else "candidates"
if cmd == "candidates":
    for c, l, ask, slug, title, d in candidates():
        print(f"{d}  {c:>5} c {l:>4} l  {'ASK' if ask else '   '}  {title[:55]:<55}  {slug}")
elif cmd == "add":
    slug = find(sys.argv[2]); exp["posts"].append(entry(slug, " ".join(sys.argv[3:]))); save(); print("added", slug)
elif cmd == "remove":
    slug = find(sys.argv[2]); exp["posts"] = [e for e in exp["posts"] if e["slug"] != slug]; save(); print("removed", slug)
elif cmd == "swap":
    old, new = find(sys.argv[2]), find(sys.argv[3]); why = " ".join(sys.argv[4:])
    exp["posts"] = [entry(new, why) if e["slug"] == old else e for e in exp["posts"]]; save(); print("swapped", old, "->", new)
elif cmd == "bump":
    slug = find(sys.argv[2]); date = sys.argv[3] if len(sys.argv) > 3 else dt.date.today().isoformat()
    exp.setdefault("log", []).append({"slug": slug, "date": date}); save(); print("logged bump", slug, date)
elif cmd == "window":
    slug = find(sys.argv[2])
    for e in exp["posts"]:
        if e["slug"] == slug:
            e["window"] = " ".join(sys.argv[3:])
    save(); print("window set")
else:
    print(__doc__)
