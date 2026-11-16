"""Encrypt desk.json with the passphrase in .env and publish the app to docs/ for GitHub Pages.
  python publish.py           # encrypt + commit + push
  python publish.py --local   # encrypt into app/data only, no git
"""
import argparse, base64, json, os, subprocess, sys, hashlib, shutil
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
env = {}
if (ROOT / ".env").exists():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1); env[k.strip()] = v.strip()
PASS = env.get("DESK_PASSPHRASE")
if not PASS:
    print("DESK_PASSPHRASE missing in .env"); sys.exit(1)
ITER = 200000
# Guest passphrases (comma-separated), e.g. the one shared with Dallas. They open ONLY data/guest.json, a copy with
# Wiley's drafts, threads, experiment and own ranks stripped. Remove one from .env and republish to cut that access.
GUESTS = [p.strip() for p in env.get("DESK_GUEST_PASSPHRASE", "").split(",") if p.strip()]
DRAFT_KEYS = ("draft", "score", "reason", "kind", "flags", "iReplied", "awaitingMe")


def encrypt(plain: bytes, passwords) -> dict:
    """Envelope encryption: one random data key encrypts the file; each passphrase unlocks a wrapped copy of that key."""
    b = lambda x: base64.b64encode(x).decode()
    data_key, iv = os.urandom(32), os.urandom(12)
    ct = AESGCM(data_key).encrypt(iv, plain, None)
    keys = []
    for pw in passwords:
        salt, kiv = os.urandom(16), os.urandom(12)
        kek = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, ITER, dklen=32)
        keys.append({"salt": b(salt), "iv": b(kiv), "wk": b(AESGCM(kek).encrypt(kiv, data_key, None))})
    return {"enc": True, "v": 2, "iter": ITER, "iv": b(iv), "ct": b(ct), "keys": keys}


ap = argparse.ArgumentParser(); ap.add_argument("--local", action="store_true"); args = ap.parse_args()
def guest_copy(desk: dict) -> dict:
    """The desk without anything written for or about Wiley's own work: no drafts, no reply queue, no My threads,
    no experiment, no 'you' rows on the leaderboard."""
    g = json.loads(json.dumps(desk))
    for p in g.get("posts", []):
        for k in DRAFT_KEYS:
            p.pop(k, None)
        p["mine"] = False
    for k in ("queue", "myThreads", "myNewComments"):
        g.get("counts", {}).pop(k, None)
    g["experiment"] = None
    g["me"] = ""
    sk = (g.get("leaderboard") or {}).get("skool")
    if sk:
        for k in ("me", "myLevel", "myPoints", "nextLevelAt"):
            sk[k] = None
    g["guest"] = True
    return g


plain = (ROOT / "data" / "desk.json").read_bytes()
blob = json.dumps(encrypt(plain, [PASS]))                       # Wiley's full desk: his passphrase only
guest_plain = json.dumps(guest_copy(json.loads(plain)), ensure_ascii=False).encode("utf-8")
gtext, gdata = guest_plain.decode("utf-8"), json.loads(guest_plain)
drafts = json.loads((ROOT / "data" / "drafts.json").read_text(encoding="utf-8")) if (ROOT / "data" / "drafts.json").exists() else {}
leak = [k for p in gdata.get("posts", []) for k in DRAFT_KEYS if k in p]
leak += ["queue count"] if "queue" in gdata.get("counts", {}) else []
leak += ["experiment"] if gdata.get("experiment") else []
leak += [f"draft text for {pid}" for pid, d in drafts.items() if (d.get("draft") or "").strip() and d["draft"].strip()[:60] in gtext]
if leak:
    print("guest copy still holds", leak, "- not publishing"); sys.exit(1)
gblob = json.dumps(encrypt(guest_plain, GUESTS)) if GUESTS else None
(ROOT / "app" / "data").mkdir(exist_ok=True)
(ROOT / "app" / "data" / "desk.json").write_text(blob, encoding="utf-8")
if gblob:
    (ROOT / "app" / "data" / "guest.json").write_text(gblob, encoding="utf-8")
print("encrypted", len(plain) // 1024, "KB ->", len(blob) // 1024, "KB", "; guest", (len(gblob) // 1024 if gblob else 0), "KB")
if args.local:
    sys.exit(0)
docs = ROOT / "docs"
(docs / "data").mkdir(parents=True, exist_ok=True)
shutil.copy(ROOT / "app" / "index.html", docs / "index.html")
(docs / "data" / "desk.json").write_text(blob, encoding="utf-8")
if gblob:
    (docs / "data" / "guest.json").write_text(gblob, encoding="utf-8")
elif (docs / "data" / "guest.json").exists():
    (docs / "data" / "guest.json").unlink()   # no guest passphrase left: take the guest file down
(docs / ".nojekyll").write_text("")
run = lambda *a: subprocess.run(a, cwd=ROOT, capture_output=True, text=True)
run("git", "add", "docs", "app/index.html", "collector", "analyst", "publish.py", "run_daily.bat", ".gitignore", "README.md")
r = run("git", "commit", "-m", "Daily desk update")
print(r.stdout.strip()[:200] or r.stderr.strip()[:200])
r = run("git", "push")
print((r.stdout + r.stderr).strip()[-300:])
