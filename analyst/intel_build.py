"""Builds the twelve forum-intelligence lists for the desk's Intel tab.

Called from build_desk.py. Only uses what men posted in the group themselves.
Men in data/optout.json (handles or user ids) are left out of every people list.
"""
import collections, datetime as dt, json, re
from urllib.parse import urlparse

URL = re.compile(r"https?://[^\s)\]>\"']+")
ASK = re.compile(r"\?|\b(need help|any(one|body) (know|have|got|recommend|here)|looking for|advice|how do (i|you|we)|"
                 r"what do you (guys )?(think|recommend|use)|recommend|suggestions?|where (can|do|should) i|can someone|tips|ideas)\b", re.I)
INTRO = re.compile(r"\b(intro|introduc\w*|new here|new member|just joined|glad to be (here|part)|happy to be (here|part)|"
                   r"hello (brothers|everyone|all)|checking in from|first post|joined (the|this))\b", re.I)
EVENT = re.compile(r"\b(call|live|zoom|workshop|mod(ule)? ?\d|replay|event|webinar|q ?& ?a|meet ?up|stream|rsvp)\b", re.I)
SKIP_LINK = ("skool.com", "obj://")


def _iso(s):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    except Exception:
        return None


def _plain(t):
    t = re.sub(r"\[([^\]]*)\]\((https?://[^)\s]+)\)", r"\1", t or "")
    t = re.sub(r"\[([^\]]*)\]\(obj://[^)]*\)", r"\1", t)
    t = re.sub(r"\[/?(ul|ol|li)\]", " ", t)
    t = re.sub(r"\\([()\[\].\-*_#+!`>~])", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


def build_intel(posts, comments, post_f, now, intel_raw, optout, me):
    opt = set(optout or [])
    ok = lambda uid, handle: uid not in opt and (handle or "") not in opt
    by_post = collections.defaultdict(list)
    for c in comments.values():
        by_post[c.get("postId")].append(c)
    others = lambda pid, uid: [c for c in by_post.get(pid, []) if c.get("userId") != uid]
    days = lambda s: (now - _iso(s)).days if _iso(s) else 9999
    ref = lambda p: {"slug": p.get("slug"), "title": p.get("title") or "(untitled)", "date": (p.get("createdAt") or "")[:10]}

    # 1. Brothers near you
    near = []
    for uid, v in (intel_raw.get("loc") or {}).items():
        if v.get("state") and ok(uid, v.get("handle")):
            near.append({"name": v.get("name"), "handle": v.get("handle"), "town": v.get("town", ""), "state": v.get("state"), "area": v.get("area", "")})
    near.sort(key=lambda x: (x["state"], x["area"], x["town"], x["name"] or ""))

    # 2. Ask a brother, and 11. Gear picks
    skills, picks, seen = [], [], set()
    for tid, v in (intel_raw.get("skills") or {}).items():
        if not ok(v.get("u"), v.get("handle")):
            continue
        p = posts.get(v.get("post"), {})
        for s in v.get("s") or []:
            k = (v.get("u"), s["skill"].lower())
            if k in seen:
                continue
            seen.add(k)
            skills.append({"name": v.get("name"), "handle": v.get("handle"), "skill": s["skill"], "cat": s["cat"], "q": s.get("q", ""), "slug": p.get("slug")})
        for r in v.get("r") or []:
            picks.append({"name": v.get("name"), "handle": v.get("handle"), "item": r["item"], "kind": r.get("kind", "other"), "q": r.get("q", ""), "slug": p.get("slug")})
    skills.sort(key=lambda x: (x["cat"], x["skill"].lower()))
    pick_groups = collections.defaultdict(list)
    for r in picks:
        pick_groups[re.sub(r"[^a-z0-9]+", " ", r["item"].lower()).strip()].append(r)
    gear = [{"item": g[0]["item"], "kind": g[0]["kind"], "count": len({x["handle"] for x in g}), "by": g[:6]} for g in pick_groups.values()]
    gear.sort(key=lambda g: (-g["count"], g["item"].lower()))

    # 3. Unanswered questions (last 60 days) and 7. New men nobody welcomed
    unanswered, unwelcomed = [], []
    for pid, p in posts.items():
        if days(p.get("createdAt")) > 60 or p.get("role") == 2 or p.get("author") == me:
            continue
        if others(pid, p.get("userId")) or (int(p.get("comments") or 0) > 0 and pid not in by_post):
            continue
        text = _plain((p.get("title") or "") + ". " + (p.get("content") or ""))
        no_urls = URL.sub("", text)
        item = dict(ref(p), author=p.get("author"), handle=p.get("handle"), text=text[:300], f=post_f.get(pid))
        if post_f.get(pid) == "F01" or INTRO.search(no_urls[:300]):
            unwelcomed.append(item)
        elif ASK.search(no_urls) and len(no_urls) > 15:
            unanswered.append(item)
    unanswered.sort(key=lambda x: x["date"], reverse=True)
    unwelcomed.sort(key=lambda x: x["date"], reverse=True)

    # 4. Best answers: question posts, the most-liked reply from someone else (struggles left out, those are personal)
    best = []
    for pid, p in posts.items():
        if post_f.get(pid) == "F10":
            continue
        text = URL.sub("", _plain((p.get("title") or "") + ". " + (p.get("content") or "")[:600]))
        if not ASK.search(text):
            continue
        rep = [c for c in others(pid, p.get("userId")) if (c.get("upvotes") or 0) >= 2 and len(_plain(c.get("content"))) > 60]
        if not rep or not ok(rep[0].get("userId"), rep[0].get("handle")):
            continue
        top = max(rep, key=lambda c: c.get("upvotes") or 0)
        if not ok(top.get("userId"), top.get("handle")):
            continue
        best.append(dict(ref(p), f=post_f.get(pid), asker=p.get("author"),
                         answer={"name": top.get("author"), "handle": top.get("handle"), "text": _plain(top.get("content"))[:700], "likes": top.get("upvotes") or 0}))
    best.sort(key=lambda b: -b["answer"]["likes"])

    # 5. Resource library: every outside link shared
    links = {}
    for x in list(posts.values()) + list(comments.values()):
        pid = x.get("postId") or x.get("id")
        p = posts.get(pid, {})
        for u in URL.findall(x.get("content") or ""):
            u = u.rstrip(".,;:!")
            host = urlparse(u).netloc.lower().removeprefix("www.")
            if not host or any(s in u for s in SKIP_LINK):
                continue
            key = u.split("?")[0] if "youtube" not in host else u
            e = links.setdefault(key, {"url": u, "host": host, "count": 0, "by": [], "slug": p.get("slug"), "title": p.get("title"), "f": post_f.get(pid), "date": (x.get("createdAt") or "")[:10]})
            e["count"] += 1
            if x.get("author") and x.get("author") not in e["by"] and ok(x.get("userId"), x.get("handle")):
                e["by"].append(x.get("author"))
    kind = lambda h: ("Video" if any(k in h for k in ("youtube", "youtu.be", "rumble", "bitchute", "vimeo")) else
                      "Social" if any(k in h for k in ("facebook", "fb.", "x.com", "twitter", "instagram", "tiktok", "truthsocial")) else
                      "Tool" if any(k in h for k in ("github.io", ".app", "calculator")) else "Article and site")
    resources = sorted(links.values(), key=lambda e: (-e["count"], e["date"]), reverse=False)
    for e in resources:
        e["kind"] = kind(e["host"]); e["by"] = e["by"][:5]
    resources = resources[:600]

    # 6. Helpers board: replies on other men's posts
    def helpers(window):
        cnt, helped, name, handle = collections.Counter(), collections.defaultdict(set), {}, {}
        for c in comments.values():
            p = posts.get(c.get("postId"), {})
            if not p or c.get("userId") == p.get("userId") or days(c.get("createdAt")) > window:
                continue
            if not ok(c.get("userId"), c.get("handle")):
                continue
            u = c.get("userId"); cnt[u] += 1; helped[u].add(p.get("userId")); name[u] = c.get("author"); handle[u] = c.get("handle")
        return [{"name": name[u], "handle": handle[u], "replies": n, "men": len(helped[u])} for u, n in cnt.most_common(30)]
    helpers_board = {"d30": helpers(30), "all": helpers(99999)}

    # 8. Meetup spots: areas with five or more men
    spots = collections.defaultdict(list)
    for n in near:
        if n["area"]:
            spots[(n["area"], n["state"])].append(n)
    meetups = [{"area": a, "state": s, "count": len(v), "men": v} for (a, s), v in spots.items() if len(v) >= 5]
    meetups.sort(key=lambda m: -m["count"])

    # 9. Wins wall: last 14 days in wins threads and the check-ins forum
    wins = []
    win_posts = {pid for pid, p in posts.items() if re.search(r"\bwins?\b", p.get("title") or "", re.I) or post_f.get(pid) == "F02"}
    for x in list(comments.values()) + [dict(p, postId=pid) for pid, p in posts.items()]:
        pid = x.get("postId")
        if pid not in win_posts or days(x.get("createdAt")) > 14 or not ok(x.get("userId"), x.get("handle")):
            continue
        t = _plain(x.get("content"))
        if len(t) < 25:
            continue
        p = posts.get(pid, {})
        wins.append({"name": x.get("author"), "handle": x.get("handle"), "text": t[:500], "likes": x.get("upvotes") or 0,
                     "date": (x.get("createdAt") or "")[:10], "slug": p.get("slug"), "title": p.get("title")})
    wins.sort(key=lambda w: w["date"], reverse=True)

    # 10. What the group is talking about: forum activity this week against the four weeks before
    wk, prev = collections.Counter(), collections.Counter()
    for x in list(comments.values()) + [dict(p, postId=pid) for pid, p in posts.items()]:
        f = post_f.get(x.get("postId"))
        d = days(x.get("createdAt"))
        if not f:
            continue
        if d <= 7:
            wk[f] += 1
        elif d <= 35:
            prev[f] += 1
    trend = [{"f": f, "week": wk[f], "avg": round(prev[f] / 4, 1)} for f in set(wk) | set(prev)]
    for t in trend:
        t["change"] = round(t["week"] - t["avg"], 1)
    trend.sort(key=lambda t: -t["week"])
    hot = sorted([dict(ref(p), author=p.get("author"), f=post_f.get(pid),
                       recent=sum(1 for c in by_post.get(pid, []) if days(c.get("createdAt")) <= 7))
                  for pid, p in posts.items()], key=lambda h: -h["recent"])[:15]
    hot = [h for h in hot if h["recent"]]

    # 12. Calls and events from admin posts
    events = []
    for pid, p in posts.items():
        if p.get("role") != 2 or days(p.get("createdAt")) > 180:
            continue
        t = _plain((p.get("title") or "") + ". " + (p.get("content") or ""))
        if EVENT.search(t[:400]):
            events.append(dict(ref(p), author=p.get("author"), text=t[:300]))
    events.sort(key=lambda e: e["date"], reverse=True)

    return {"near": near, "skills": skills, "unanswered": unanswered, "best": best[:300], "resources": resources,
            "helpers": helpers_board, "unwelcomed": unwelcomed, "meetups": meetups, "wins": wins[:200],
            "trend": trend, "hot": hot, "gear": gear[:400], "events": events[:60]}
