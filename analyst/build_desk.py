"""Build data/desk.json, the single file the app reads.

Merges: data/posts.json (collector, live) over data/posts_seed.json (Nov 10 export),
forum labels from data/forum_seed.json + data/labels.json (analyst),
drafts from data/drafts.json (analyst), comments from data/comments.json,
leaderboard from data/leaderboard_raw.json, member profiles from data/profiles_seed.json.
"""
import json, sys, collections, datetime as dt, statistics
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ME = "Wiley Wilkins"


def load(name, default):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def iso(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


now = dt.datetime.now(dt.timezone.utc)
seed = load("posts_seed.json", {"posts": []})["posts"]
live = load("posts.json", {})
forum = load("forum_seed.json", {"forums": [], "posts": []})
labels = load("labels.json", {})            # id -> {f,t}
drafts = load("drafts.json", {})            # id -> {score, reason, draft, kind}
comments = load("comments.json", {})
lb_raw = load("leaderboard_raw.json", {})
profiles = {p["uid"]: p for p in load("profiles_seed.json", [])}
state = load("state.json", {})

forums = [{"code": f["code"], "name": f["name"], "desc": f.get("desc", ""), "threads": f.get("threads", [])} for f in forum["forums"]]
forums.append({"code": "F00", "name": "Unsorted (new)", "desc": "Pulled this morning, not yet sorted.", "threads": []})
seed_labels = {p["id"]: (p["f"], p["t"]) for p in forum["posts"]}

# merge posts: live wins
posts = {p["id"]: dict(p) for p in seed}
for pid, p in live.items():
    base = posts.get(pid, {})
    base.update(p)
    posts[pid] = base

last_run = iso(state.get("last_run")) if state.get("last_run") else None
runs = state.get("runs", [])
prev_run = iso(runs[-2]["finished"]) if len(runs) >= 2 else (now - dt.timedelta(days=1))
new_since = prev_run or (now - dt.timedelta(days=1))

# comments by post, and my activity
cby = collections.defaultdict(list)
for c in comments.values():
    cby[c["postId"]].append(c)
my_post_ids = {pid for pid, p in posts.items() if p.get("author") == ME}
my_commented = {c["postId"] for c in comments.values() if c.get("author") == ME}

out_posts = []
for pid, p in posts.items():
    f, t = labels.get(pid, {}).get("f"), labels.get(pid, {}).get("t")
    if not f:
        f, t = seed_labels.get(pid, ("F00", None))
    created = iso(p.get("createdAt"))
    lastc = iso(p.get("lastCommentAt")) if p.get("lastCommentAt") else created
    cl = sorted(cby.get(pid, []), key=lambda c: c.get("createdAt") or "")
    new_c = [c for c in cl if iso(c.get("firstSeen") or c.get("createdAt")) and iso(c.get("firstSeen") or c.get("createdAt")) >= new_since]
    d = drafts.get(pid, {})
    mine = pid in my_post_ids or pid in my_commented
    out_posts.append({
        "id": pid, "slug": p.get("slug"), "title": p.get("title", ""), "author": p.get("author", ""), "userId": p.get("userId"),
        "role": p.get("role"), "date": p.get("createdAt", "")[:10], "ts": p.get("createdAt"), "comments": p.get("comments", 0),
        "likes": p.get("upvotes", 0), "pinned": p.get("pinned", 0), "f": f, "t": t,
        "text": (p.get("content") or "")[:2500], "lastCommentAt": p.get("lastCommentAt"),
        "isNew": bool(created and created >= new_since), "mine": mine, "newComments": len(new_c),
        "commentList": [{"author": c.get("author"), "text": (c.get("content") or "")[:800], "date": (c.get("createdAt") or "")[:10], "likes": c.get("upvotes", 0), "new": c in new_c} for c in cl[-40:]],
        "score": d.get("score", 0), "reason": d.get("reason", ""), "draft": d.get("draft", ""), "kind": d.get("kind", ""),
    })
out_posts.sort(key=lambda x: x.get("ts") or "", reverse=True)

# members: counts, 8-week trend
by = collections.defaultdict(list)
for p in posts.values():
    if p.get("author"):
        by[p["userId"]].append(p)
members = []
wk = lambda d: int((now - d).days // 7)
for uid, ps in by.items():
    ps.sort(key=lambda p: p.get("createdAt") or "")
    weeks = [0] * 8
    for p in ps:
        c = iso(p.get("createdAt"))
        if c:
            w = wk(c)
            if 0 <= w < 8:
                weeks[7 - w] += 1
    recent4, prior4 = sum(weeks[4:]), sum(weeks[:4])
    last = ps[-1].get("createdAt", "")[:10]
    likes = [p.get("upvotes", 0) for p in ps]
    pr = profiles.get(uid, {})
    members.append({
        "uid": uid, "name": ps[0].get("author"), "role": ps[0].get("role"), "posts": len(ps), "first": ps[0].get("createdAt", "")[:10], "last": last,
        "likes": sum(likes), "likesMed": statistics.median(likes) if likes else 0, "comments": sum(p.get("comments", 0) for p in ps),
        "weeks": weeks, "recent4": recent4, "prior4": prior4, "delta": recent4 - prior4,
        "posts30": sum(1 for p in ps if iso(p.get("createdAt")) and (now - iso(p.get("createdAt"))).days <= 30),
        "likes30": sum(p.get("upvotes", 0) for p in ps if iso(p.get("createdAt")) and (now - iso(p.get("createdAt"))).days <= 30),
        "where": pr.get("where", ""), "tags": pr.get("tags", []),
    })
members.sort(key=lambda m: (-m["recent4"], -m["posts"]))

# activity by month (members only) and lifecycle
mon = collections.defaultdict(lambda: {"posts": 0, "posters": set(), "comments": 0, "new": set()})
first = {}
for p in sorted(posts.values(), key=lambda p: p.get("createdAt") or ""):
    if p.get("role") == 2 or not p.get("createdAt"):
        continue
    m = p["createdAt"][:7]
    mon[m]["posts"] += 1; mon[m]["posters"].add(p["userId"]); mon[m]["comments"] += p.get("comments", 0)
    if p["userId"] not in first:
        first[p["userId"]] = m; mon[m]["new"].add(p["userId"])
activity = [{"month": m, "posts": v["posts"], "posters": len(v["posters"]), "newPosters": len(v["new"]), "returning": len(v["posters"]) - len(v["new"]), "comments": v["comments"]} for m, v in sorted(mon.items())]

# leaderboard from Skool page if present (best effort), else ours
lb = {"skool": None, "ours": {}}
if lb_raw:
    txt = json.dumps(lb_raw)
    lb["skool_keys"] = list(lb_raw.keys())[:30]
    for key in ("leaderboard", "leaderboards", "renderData"):
        if key in lb_raw:
            lb["skool"] = lb_raw[key]
            break
lb["ours"] = {
    "posts30": [{"name": m["name"], "v": m["posts30"]} for m in sorted(members, key=lambda m: -m["posts30"])[:15] if m["posts30"]],
    "likes30": [{"name": m["name"], "v": m["likes30"]} for m in sorted(members, key=lambda m: -m["likes30"])[:15] if m["likes30"]],
    "likesAll": [{"name": m["name"], "v": m["likes"]} for m in sorted(members, key=lambda m: -m["likes"])[:15]],
    "rising": [{"name": m["name"], "v": m["delta"], "w": m["weeks"]} for m in sorted(members, key=lambda m: -m["delta"])[:15] if m["delta"] > 0],
    "fading": [{"name": m["name"], "v": m["delta"], "w": m["weeks"]} for m in sorted(members, key=lambda m: m["delta"])[:15] if m["delta"] < 0 and m["prior4"] >= 2],
}

# experiment: bump Dallas's peak posts, measured from daily snapshots
exp = load("experiment_bumps.json", None)
snapdir = DATA / "snapshots"
snaps = {}
if snapdir.exists():
    for f in sorted(snapdir.glob("*.json")):
        snaps[f.stem] = json.loads(f.read_text(encoding="utf-8"))
by_slug = {p.get("slug"): pid for pid, p in posts.items() if p.get("slug")}
if exp:
    logs = {l.get("slug"): l for l in exp.get("log", [])}
    for e in exp["posts"]:
        pid = by_slug.get(e["slug"])
        e["id"] = pid
        p = posts.get(pid, {}) if pid else {}
        e["now"] = {"comments": p.get("comments", e["comments"]), "likes": p.get("upvotes", e["likes"])}
        e["series"] = [[d, s[pid][0], s[pid][1]] for d, s in snaps.items() if pid in s]
        e["bumped"] = logs.get(e["slug"], {}).get("date")
        if e["bumped"]:
            before = [s for s in e["series"] if s[0] <= e["bumped"]]
            base = before[-1] if before else [None, e["comments"], e["likes"]]
            e["gain"] = {"comments": e["now"]["comments"] - base[1], "likes": e["now"]["likes"] - base[2]}
            e["newCommenters"] = sorted({c.get("author") for c in cby.get(pid, []) if (c.get("createdAt") or "") >= e["bumped"]} - {ME})

desk = {
    "experiment": exp,
    "generated": now.isoformat(timespec="seconds"), "lastRun": state.get("last_run"), "newSince": new_since.isoformat(timespec="seconds"),
    "me": ME, "forums": forums, "posts": out_posts, "members": members, "activity": activity, "leaderboard": lb,
    "counts": {"posts": len(out_posts), "new": sum(1 for p in out_posts if p["isNew"]), "queue": sum(1 for p in out_posts if p["draft"]),
               "myThreads": sum(1 for p in out_posts if p["mine"]), "myNewComments": sum(p["newComments"] for p in out_posts if p["mine"])},
}
(DATA / "desk.json").write_text(json.dumps(desk, ensure_ascii=False), encoding="utf-8")
print("desk.json", round((DATA / "desk.json").stat().st_size / 1024), "KB", desk["counts"], "members", len(members))
