"""Open one post page in the saved profile and record every JSON response,
so we can see where Skool loads comments from. Usage:
  python collector/probe_comments.py <post-slug>
"""
import json, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
slug = sys.argv[1]
hits = []
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(str(ROOT / ".profile"), headless=True)
    page = ctx.new_page()
    def on_resp(r):
        ct = r.headers.get("content-type", "")
        if "json" in ct or "/api/" in r.url:
            try:
                body = r.text()
            except Exception:
                return
            hits.append({"url": r.url, "status": r.status, "len": len(body), "has_children": '"children"' in body, "sample": body[:300]})
    page.on("response", on_resp)
    page.goto(f"https://www.skool.com/thebrotherhood/{slug}", wait_until="networkidle", timeout=60000)
    time.sleep(2)
    # scroll to trigger lazy loads
    for _ in range(4):
        page.mouse.wheel(0, 2000); time.sleep(1)
    nd = page.evaluate("document.getElementById('__NEXT_DATA__')?.textContent")
    pp = json.loads(nd)["props"]["pageProps"] if nd else {}
    print("pageProps keys:", list(pp.keys()))
    print("pageProps size per key:", {k: len(json.dumps(v)) for k, v in pp.items()})
    ctx.close()
for h in hits:
    print(h["status"], h["len"], "children" if h["has_children"] else "", h["url"][:140])
    if h["has_children"]:
        print("   ", h["sample"].replace("\n", " ")[:300])
