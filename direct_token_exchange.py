
import sys, os, sqlite3, json, base64, requests
from datetime import datetime, timezone

root = "/Volumes/181TB/Perdana-LLC/nate.tps.pro"
env = {}
with open(f"{root}/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v

APP_KEY = env["SCHWAB_CLIENT_ID"]
APP_SECRET = env["SCHWAB_CLIENT_SECRET"]
CALLBACK_URL = env["SCHWAB_CALLBACK_URL"]
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"

# Read callback URL from file or arg
cb_file = f"{root}/fallback_callback.txt"
if len(sys.argv) > 1:
    callback_url = sys.argv[1]
else:
    callback_url = open(cb_file).read().strip()

print(f"[INFO] Callback URL: {callback_url[:80]}...")

# Extract code
from urllib.parse import urlparse, parse_qs
parsed = urlparse(callback_url)
code = parse_qs(parsed.query).get("code", [None])[0]
if not code:
    print("[FATAL] No code in callback URL")
    sys.exit(1)

# URL-decode the code (%40 -> @)
from urllib.parse import unquote
code = unquote(code)
print(f"[INFO] Code extracted: {code[:30]}...")

# Token exchange
auth_b64 = base64.b64encode(f"{APP_KEY}:{APP_SECRET}".encode()).decode()
headers = {
    "Authorization": f"Basic {auth_b64}",
    "Content-Type": "application/x-www-form-urlencoded"
}
data = {
    "grant_type": "authorization_code",
    "code": code,
    "redirect_uri": CALLBACK_URL
}

print(f"[INFO] POST {TOKEN_URL}")
print(f"[INFO] redirect_uri={CALLBACK_URL}")
resp = requests.post(TOKEN_URL, headers=headers, data=data, timeout=15)

print(f"[RESP] HTTP {resp.status_code}")
print(f"[RESP] Body: {resp.text[:500]}")

if not resp.ok:
    print("[FATAL] Token exchange failed")
    sys.exit(1)

token_data = resp.json()
access_token = token_data.get("access_token", "")
refresh_token = token_data.get("refresh_token", "")
id_token = token_data.get("id_token", "")
expires_in = token_data.get("expires_in", 1800)
token_type = token_data.get("token_type", "Bearer")
scope = token_data.get("scope", "")

print(f"[OK] Got access_token ({len(access_token)} chars)")
print(f"[OK] Got refresh_token ({len(refresh_token)} chars)")
print(f"[OK] expires_in={expires_in}")

# Write to schwabdev DB
db_path = f"{root}/tokens/schwab_tokens.db"
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Ensure table exists
cur.execute("""
CREATE TABLE IF NOT EXISTS schwabdev (
    access_token_issued TEXT NOT NULL,
    refresh_token_issued TEXT NOT NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    id_token TEXT NOT NULL,
    expires_in INTEGER,
    token_type TEXT,
    scope TEXT
)
""")

now = datetime.now(timezone.utc).isoformat()

# Delete old row if any
cur.execute("DELETE FROM schwabdev")
cur.execute(
    "INSERT INTO schwabdev VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    (now, now, access_token, refresh_token, id_token, expires_in, token_type, scope)
)
conn.commit()
conn.close()

print(f"[OK] Tokens written to {db_path}")

# Verify
conn2 = sqlite3.connect(db_path)
row = conn2.execute("SELECT COUNT(*) FROM schwabdev").fetchone()
conn2.close()
print(f"[VERIFY] DB rows: {row[0]}")
print("[DONE] Token exchange complete!")
