#!/usr/bin/env python3
"""Verify the user's console-registered key + probe headless token minting
for the existing 09-07 account. No config files touched."""
import json, base64, urllib.request, urllib.error

ROUTER = "https://delta-router.yangtzeailab.com"
NEW_KEY = "sk-g9RJ0H04RhmJg1DIAoRahOH59MeXdyIcdXmXsxBzr84j5Eqt"
U = "upz157pszdx9"
P = "lYeu0jubQWANE6Zs"

def req(path, data=None, method="POST", token=None, headers=None, base=ROUTER):
    url = base + path
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if headers:
        h.update(headers)
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return {"ok": True, "status": resp.status,
                    "body": json.loads(resp.read().decode()) if resp.read else None}
    except urllib.error.HTTPError as e:
        t = e.read().decode()
        try:
            b = json.loads(t)
        except Exception:
            b = t
        return {"ok": False, "status": e.code, "body": b}

print("=== A. verify user's new console key ===")
m = req("/v1/models", data=None, method="GET", token=NEW_KEY)
print("  /v1/models status:", m["status"])
hdr_req = urllib.request.Request(ROUTER + "/v1/chat/completions",
    data=json.dumps({"model":"DeepSeek-V4-Flash","messages":[{"role":"user","content":"hi"}],"max_tokens":5}).encode(),
    headers={"Content-Type":"application/json","Authorization":f"Bearer {NEW_KEY}"}, method="POST")
try:
    with urllib.request.urlopen(hdr_req, timeout=30) as resp:
        print("  chat status:", resp.status, "spend header:",
              resp.headers.get("X-Litellm-Key-Spend"), "cost:", resp.headers.get("X-Litellm-Response-Cost"))
except urllib.error.HTTPError as e:
    print("  chat status:", e.code, e.read().decode()[:200])

print("\n=== B. login existing 09-07 account ===")
lg = req("/api/user/login", {"username": U, "password": P})
tok = (lg.get("body") or {}).get("data", {}).get("access_token")
print("  login status:", lg["status"], "token?", bool(tok))

if tok:
    print("\n=== C. probe token endpoints (headless mint) ===")
    for fmt_name, val in [("raw", U), ("b64user", base64.b64encode(U.encode()).decode()),
                          ("b64user:pass", base64.b64encode(f"{U}:{P}".encode()).decode())]:
        h = {"New-Api-User": val}
        # list
        lst = req("/api/user/token", data=None, method="GET", token=tok, headers=h)
        print(f"  GET /api/user/token [{fmt_name}] -> {lst['status']} {str(lst['body'])[:160]}")
        # create
        cr = req("/api/user/token", {"name": "auto"}, token=tok, headers=h)
        print(f"  POST /api/user/token [{fmt_name}] -> {cr['status']} {str(cr['body'])[:160]}")
    # also try /api/user/token with subpath '/list' and '/generate'
    for p in ["/api/user/token/list", "/api/user/token/generate", "/api/user/token/new"]:
        x = req(p, {"name":"auto"}, token=tok, headers={"New-Api-User": U})
        print(f"  POST {p} -> {x['status']} {str(x['body'])[:160]}")
    print("\n=== D. /api/user/self quota (try header formats) ===")
    for fmt_name, val in [("raw", U), ("b64user", base64.b64encode(U.encode()).decode())]:
        s = req("/api/user/self", data=None, method="GET", token=tok, headers={"New-Api-User": val})
        print(f"  self [{fmt_name}] -> {s['status']} {str(s['body'])[:200]}")
