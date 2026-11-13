"""Brotherhood collector.

Pulls new posts, comments and the leaderboard from the Skool group using a
persistent logged-in browser profile. Run once with --login to sign in.

  python collector/collect.py --login          # headed, waits for you to log in
  python collector/collect.py                  # incremental pull (headless)
  python collector/collect.py --pages 90       # full backfill of the feed
"""
import argparse, json, os, re, sys, time, datetime as dt
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
PROFILE = ROOT / ".profile"
GROUP = "thebrotherhood"
BASE = f"https://www.skool.com/{GROUP}"
sys.stdout.reconfigure(encoding="utf-8")


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=0), encoding="utf-8")


def next_data(page, url, wait_ms=20000):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("#__NEXT_DATA__", state="attached", timeout=wait_ms)
    txt = page.evaluate("document.getElementById('__NEXT_DATA__').textContent")
    return json.loads(txt)


def dig(obj, *keys, default=None):
    for k in keys:
        if isinstance(obj, dict) and k in obj:
            obj = obj[k]
        else:
            return default
    return obj


def ns_to_iso(ns):
    try:
        return dt.datetime.fromtimestamp(int(ns) / 1e9, dt.timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


def norm_post(tree):
    """Flatten one postTree entry into the shape used by the analysis files."""
    post = tree.get("post", tree)
    md = post.get("metadata", {}) or {}
    user = post.get("user") or tree.get("user") or {}
    umd = user.get("metadata") or {}
    name = (user.get("firstName", "") + " " + user.get("lastName", "")).strip() or user.get("name", "")
    sp = umd.get("spData")
    if isinstance(sp, str):
        try:
            sp = json.loads(sp)
        except Exception:
            sp = {}
    return {
        "location": umd.get("location", ""),
        "handle": user.get("name", ""),
        "id": post.get("id"),
        "slug": post.get("name") or post.get("slug"),
        "title": md.get("title", ""),
        "content": md.get("content", ""),
        "comments": md.get("comments", 0),
        "upvotes": md.get("upvotes", 0),
        "pinned": md.get("pinned", 0),
        "createdAt": post.get("createdAt"),
        "updatedAt": post.get("updatedAt"),
        "lastComment": md.get("lastComment"),
        "lastCommentAt": ns_to_iso(md.get("lastComment")) if md.get("lastComment") else None,
        "userId": post.get("userId") or user.get("id"),
        "author": name,
        "role": (sp or {}).get("role") if isinstance(sp, dict) else None,
        "level": (sp or {}).get("lv") if isinstance(sp, dict) else None,
        "points": (sp or {}).get("pt") if isinstance(sp, dict) else None,
        "hasVideo": bool(md.get("videoLinksData") or md.get("video")),
        "hasAttach": bool(md.get("attachments")),
        "postType": post.get("postType", "generic"),
    }


def walk_comments(node, post_id, parent_id, out):
    """Recursively flatten a comment tree (children under a post)."""
    for child in node.get("children", []) or []:
        c = child.get("post", child)
        md = c.get("metadata", {}) or {}
        user = child.get("user") or c.get("user") or {}
        name = (user.get("firstName", "") + " " + user.get("lastName", "")).strip() or user.get("name", "")
        out.append({
            "id": c.get("id"),
            "postId": post_id,
            "parentId": parent_id,
            "userId": c.get("userId") or user.get("id"),
            "author": name,
            "content": md.get("content", ""),
            "upvotes": md.get("upvotes", 0),
            "createdAt": c.get("createdAt"),
        })
        walk_comments(child, post_id, c.get("id"), out)


GROUP_ID = "f318f42af1414ece8625051937ee2f12"


def walk_api_comments(node, post_id, parent_id, out):
    """Flatten the snake_case comment tree returned by api2.skool.com."""
    for child in node.get("children", []) or []:
        c = child.get("post", child)
        md = c.get("metadata", {}) or {}
        user = c.get("user") or {}
        umd = user.get("metadata") or {}
        name = (user.get("first_name", "") + " " + user.get("last_name", "")).strip() or user.get("name", "")
        out.append({
            "id": c.get("id"), "postId": post_id, "parentId": parent_id,
            "userId": c.get("user_id") or user.get("id"), "author": name, "handle": user.get("name", ""),
            "location": umd.get("location", ""), "content": md.get("content", ""), "upvotes": md.get("upvotes", 0),
            "createdAt": c.get("created_at"),
        })
        walk_api_comments(child, post_id, c.get("id"), out)


def feed_trees(nd):
    pp = dig(nd, "props", "pageProps", default={})
    trees = pp.get("postTrees")
    if trees is None:
        trees = dig(pp, "renderData", "postTrees", default=[])
    return trees or []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="open a visible browser and wait for login")
    ap.add_argument("--pages", type=int, default=0, help="force this many feed pages (backfill)")
    ap.add_argument("--max-detail", type=int, default=80, help="max post pages to open for comments")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--recomment", action="store_true", help="pull comments for posts that have none captured yet (no feed walk)")
    args = ap.parse_args()

    state = load(DATA / "state.json", {})
    posts = load(DATA / "posts.json", {})
    comments = load(DATA / "comments.json", {})
    last_run = state.get("last_run")
    cutoff = dt.datetime.fromisoformat(last_run) - dt.timedelta(days=2) if last_run else None
    run_id = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    rawdir = RAW / run_id
    log = {"run": run_id, "started": now_iso(), "new_posts": 0, "updated_posts": 0, "new_comments": 0, "pages": 0}

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE), headless=not (args.login or args.headed), viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        )
        page = ctx.new_page()

        # --- login check ---
        nd = next_data(page, f"{BASE}?p=1")
        trees = feed_trees(nd)
        if not trees:
            if not args.login:
                print("NOT LOGGED IN. Run:  python collector/collect.py --login")
                ctx.close(); sys.exit(2)
            print("Log in to Skool in the browser window. Waiting up to 30 minutes...")
            page.goto("https://www.skool.com/login", wait_until="domcontentloaded", timeout=60000)
            logged = False
            for _ in range(360):
                time.sleep(5)
                names = {c["name"] for c in ctx.cookies("https://www.skool.com")}
                if "auth_token" in names:
                    logged = True
                    break
            if not logged:
                print("Still not logged in. Giving up."); ctx.close(); sys.exit(2)
            time.sleep(3)
            nd = next_data(page, f"{BASE}?p=1")
            trees = feed_trees(nd)
            if not trees:
                print("Cookie found but feed still empty. Giving up."); ctx.close(); sys.exit(2)
            print("Logged in. Session saved to", PROFILE)
        save(rawdir / "feed_p1_sample.json", trees[:2])

        # --- feed pages ---
        touched = []          # post ids seen this run that need a detail fetch
        max_pages = 0 if args.recomment else (args.pages or 40)
        pg = 1
        while pg <= max_pages:
            if pg > 1:
                nd = next_data(page, f"{BASE}?p={pg}")
                trees = feed_trees(nd)
            if not trees:
                break
            log["pages"] = pg
            all_old = True
            for t in trees:
                n = norm_post(t)
                if not n["id"]:
                    continue
                created = dt.datetime.fromisoformat(n["createdAt"].replace("Z", "+00:00")) if n["createdAt"] else None
                lastc = dt.datetime.fromisoformat(n["lastCommentAt"]) if n["lastCommentAt"] else created
                fresh = cutoff is None or (created and created >= cutoff) or (lastc and lastc >= cutoff)
                if fresh:
                    all_old = False
                old = posts.get(n["id"])
                if old is None:
                    log["new_posts"] += 1
                    n["firstSeen"] = now_iso()
                    posts[n["id"]] = n
                    touched.append(n["id"])
                else:
                    if (n["comments"], n["upvotes"]) != (old.get("comments"), old.get("upvotes")) or n["content"] != old.get("content"):
                        log["updated_posts"] += 1
                        if n["comments"] != old.get("comments"):
                            touched.append(n["id"])
                    n["firstSeen"] = old.get("firstSeen")
                    posts[n["id"]] = n
            if cutoff is not None and all_old and not args.pages:
                break
            pg += 1
            time.sleep(1.0)

        # --- comments via the API the site uses (first 25 + newest 25, with reply threads) ---
        group_id = dig(nd, "props", "pageProps", "currentGroup", "id") or GROUP_ID
        if args.recomment:
            have = {c["postId"] for c in comments.values()}
            touched = [i for i, p in posts.items() if p.get("comments", 0) > 0 and i not in have]
        ordered = sorted(dict.fromkeys(touched), key=lambda i: posts[i].get("lastCommentAt") or posts[i].get("createdAt") or "", reverse=True)
        ordered = [i for i in ordered if posts[i].get("comments", 0) > 0][: args.max_detail]

        def warm(pid):
            """Visit a post page so the site's firewall token is fresh for API calls."""
            slug = posts[pid].get("slug")
            try:
                page.goto(f"{BASE}/{slug}" if slug else BASE, wait_until="domcontentloaded", timeout=60000)
            except PWTimeout:
                pass
            time.sleep(5)

        if ordered:
            warm(ordered[0])
        calls = 0; fails = 0
        for n_done, pid in enumerate(ordered):
            if calls and calls % 40 == 0:
                warm(pid)
            flat = []
            for extra in ("", "&tail=true"):
                url = f"https://api2.skool.com/posts/{pid}/comments?group-id={group_id}&limit=25{extra}"
                tree = None
                for attempt in range(2):
                    try:
                        r = page.request.get(url, timeout=30000); calls += 1
                        if r.ok:
                            tree = r.json().get("post_tree", {}); fails = 0; break
                        log.setdefault("api_errors", 0); log["api_errors"] += 1; fails += 1
                        time.sleep(10); warm(pid)
                    except Exception:
                        break
                if fails >= 6:
                    print("comments API keeps refusing; stopping comment pull for this run"); break
                if tree is None:
                    continue
                if n_done == 0 and extra == "":
                    save(rawdir / "comments_api_sample.json", tree)
                walk_api_comments(tree, pid, None, flat)
                if posts[pid].get("comments", 0) <= 25:
                    break
            for c in flat:
                if c["id"] and c["id"] not in comments:
                    c["firstSeen"] = now_iso()
                    comments[c["id"]] = c
                    log["new_comments"] += 1
                elif c["id"]:
                    comments[c["id"]]["upvotes"] = c["upvotes"]
            if fails >= 6:
                break
            time.sleep(1.2)
        log["detail_posts"] = len(ordered)

        # --- leaderboard ---
        try:
            nd = next_data(page, f"{BASE}/-/leaderboards")
            pp = dig(nd, "props", "pageProps", default={})
            save(DATA / "leaderboard_raw.json", pp)
        except PWTimeout:
            pass

        ctx.close()

    # --- daily snapshot of counts, for trend and experiment measurement ---
    snap = {pid: [p.get("comments", 0), p.get("upvotes", 0)] for pid, p in posts.items()}
    save(DATA / "snapshots" / f"{dt.datetime.now().strftime('%Y-%m-%d')}.json", snap)

    log["finished"] = now_iso()
    state["last_run"] = log["finished"]
    state.setdefault("runs", []).append(log)
    save(DATA / "posts.json", posts)
    save(DATA / "comments.json", comments)
    save(DATA / "state.json", state)
    print(json.dumps(log, indent=1))


if __name__ == "__main__":
    main()
