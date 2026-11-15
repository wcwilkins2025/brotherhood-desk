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
# Extra passphrases (comma-separated), e.g. one to share with Dallas. Remove one from .env and republish to cut that access.
GUESTS = [p.strip() for p in env.get("DESK_GUEST_PASSPHRASE", "").split(",") if p.strip()]


def encrypt(plain: bytes) -> dict:
    """Envelope encryption: one random data key encrypts the desk; each passphrase unlocks a wrapped copy of that key."""
    b = lambda x: base64.b64encode(x).decode()
    data_key, iv = os.urandom(32), os.urandom(12)
    ct = AESGCM(data_key).encrypt(iv, plain, None)
    keys = []
    for pw in [PASS] + GUESTS:
        salt, kiv = os.urandom(16), os.urandom(12)
        kek = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, ITER, dklen=32)
        keys.append({"salt": b(salt), "iv": b(kiv), "wk": b(AESGCM(kek).encrypt(kiv, data_key, None))})
    return {"enc": True, "v": 2, "iter": ITER, "iv": b(iv), "ct": b(ct), "keys": keys}


ap = argparse.ArgumentParser(); ap.add_argument("--local", action="store_true"); args = ap.parse_args()
plain = (ROOT / "data" / "desk.json").read_bytes()
blob = json.dumps(encrypt(plain))
(ROOT / "app" / "data").mkdir(exist_ok=True)
(ROOT / "app" / "data" / "desk.json").write_text(blob, encoding="utf-8")
print("encrypted", len(plain) // 1024, "KB ->", len(blob) // 1024, "KB")
if args.local:
    sys.exit(0)
docs = ROOT / "docs"
(docs / "data").mkdir(parents=True, exist_ok=True)
shutil.copy(ROOT / "app" / "index.html", docs / "index.html")
(docs / "data" / "desk.json").write_text(blob, encoding="utf-8")
(docs / ".nojekyll").write_text("")
run = lambda *a: subprocess.run(a, cwd=ROOT, capture_output=True, text=True)
run("git", "add", "docs", "app/index.html", "collector", "analyst", "publish.py", "run_daily.bat", ".gitignore", "README.md")
r = run("git", "commit", "-m", "Daily desk update")
print(r.stdout.strip()[:200] or r.stderr.strip()[:200])
r = run("git", "push")
print((r.stdout + r.stderr).strip()[-300:])
