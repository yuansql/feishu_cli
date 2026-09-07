#!/usr/bin/env python3
"""
每天自动注册一个新的 yangtzeailab/NewAPI 账号并获取 sk-xxx key
流程：mail.tm 临时邮箱 -> Router 直接注册 -> 提取 initial_token_key -> 更新配置文件
"""
import json, os, random, string, sys, time, urllib.request, urllib.error

# ── 配置 ────────────────────────────────────────
LOG_DIR = os.path.expanduser("~/Desktop/tmp/捡钱")
KEY_FILE = os.path.join(LOG_DIR, "@捡钱.txt")
VAULT_FILE = os.path.expanduser("~/.feishu-partner/key_vault.json")
AGENT_FILE = os.path.expanduser("~/.feishu-partner/agent.json")
REPORT_FILE = os.path.join(LOG_DIR, f"auto_reg_{time.strftime('%Y%m%d_%H%M%S')}.json")

MAILTM_API = "https://api.mail.tm"
ROUTER_API = "https://delta-router.yangtzeailab.com"

# ── HTTP helpers ────────────────────────────────
def mailtm_req(path, data=None, method=None, token=None):
    url = MAILTM_API + path
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())

def router_req(path, data=None, method="POST", token=None):
    url = ROUTER_API + path
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"_error": True, "status": e.code, "body": json.loads(e.read().decode())}

# ── 1. mail.tm 创建临时邮箱 ──────────────────────
print("[1/6] 创建临时邮箱...")

domains = mailtm_req("/domains")["hydra:member"]
domain = next((d["domain"] for d in domains if d["domain"] == "uberip.com"), domains[0]["domain"])
print(f"  域名: {domain}")

random_user = ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))
email = f"{random_user}@{domain}"
password = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
print(f"  邮箱: {email}")

try:
    mailtm_req("/accounts", {"address": email, "password": password}, method="POST")
    print("  账号创建成功")
except urllib.error.HTTPError:
    pass

token_data = mailtm_req("/token", {"address": email, "password": password}, method="POST")
mail_token = token_data["token"]
mail_account_id = token_data.get("id", "")
print("  token 获取成功")

# ── 2. Router 直接注册 ───────────────────────────
print("\n[2/6] 在 Router 注册...")

reg_resp = router_req("/api/user/register", {
    "username": random_user,
    "password": password,
    "email": email
})

if not reg_resp.get("success"):
    print(f"[ERROR] 注册失败")
    with open(REPORT_FILE, "w") as f:
        json.dump({"success": False, "error": reg_resp}, f, indent=2)
    sys.exit(1)

access_token = reg_resp["access_token"]
user_id = reg_resp["id"]
new_key = reg_resp["initial_token_key"]
print("  注册成功")

# ── 3. 验证 key ─────────────────────────────────
print("\n[3/6] 验证 key...")

def check_key(key):
    req = urllib.request.Request(
        f"{ROUTER_API}/v1/models",
        headers={"Authorization": f"Bearer {key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception:
        return False

key_valid = check_key(new_key)
print(f"  Key 验证: {'通过' if key_valid else '未通过（不影响）'}")

# ── 4. 更新配置文件 ─────────────────────────────
print("\n[4/6] 更新配置文件...")

today = time.strftime("%Y-%m-%d")

# 4a. @捡钱.txt
try:
    with open(KEY_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{email} (自动注册 - {today})\n")
        f.write(f'{{"_type":"newapi_channel_conn","key":"{new_key}","url":"{ROUTER_API}"}}\n')
    print("  @捡钱.txt 已更新")
except Exception as e:
    print(f"  [WARN] @捡钱.txt: {e}")

# 4b. key_vault.json
try:
    with open(VAULT_FILE, "r", encoding="utf-8") as f:
        vault = json.load(f)
    auto_ids = [c.get("id", "") for c in vault["channels"] if c.get("id", "").startswith("newapi-auto-")]
    next_num = len(auto_ids) + 1
    vault["channels"].append({
        "_type": "newapi_channel_conn",
        "id": f"newapi-auto-{next_num:02d}",
        "key": new_key,
        "url": ROUTER_API,
        "label": f"{email} (自动注册)",
        "status": "ok",
        "last_check": time.strftime("%Y-%m-%dT%H:%M")
    })
    with open(VAULT_FILE, "w", encoding="utf-8") as f:
        json.dump(vault, f, indent=2, ensure_ascii=False)
    print(f"  key_vault.json 已更新 (newapi-auto-{next_num:02d})")
except Exception as e:
    print(f"  [WARN] key_vault.json: {e}")

# 4c. agent.json
try:
    with open(AGENT_FILE, "r", encoding="utf-8") as f:
        agent = json.load(f)
    if new_key not in agent.get("openai_api_keys", []):
        agent["openai_api_keys"].append(new_key)
        with open(AGENT_FILE, "w", encoding="utf-8") as f:
            json.dump(agent, f, indent=2, ensure_ascii=False)
        print("  agent.json 已更新")
    else:
        print("  agent.json 已存在，跳过")
except Exception as e:
    print(f"  [WARN] agent.json: {e}")

# ── 5. 清理临时邮箱 ─────────────────────────────
print("\n[5/6] 清理临时邮箱...")
try:
    if mail_account_id:
        mailtm_req(f"/accounts/{mail_account_id}", method="DELETE", token=mail_token)
    else:
        me = mailtm_req("/me", token=mail_token)
        acc_id = me.get("id")
        if acc_id:
            mailtm_req(f"/accounts/{acc_id}", method="DELETE", token=mail_token)
    print("  已删除")
except Exception as e:
    print(f"  [WARN] 清理失败: {e}")

# ── 6. 保存报告 ─────────────────────────────────
print("\n[6/6] 保存报告...")
report = {
    "success": True,
    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    "email": email,
    "username": random_user,
    "password": password,
    "user_id": user_id,
    "key": new_key,
    "key_valid": key_valid,
    "files_updated": ["@捡钱.txt", "key_vault.json", "agent.json"]
}
with open(REPORT_FILE, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)
print(f"  报告已保存: {REPORT_FILE}")

print("\n" + "="*50)
print("✅ 自动注册完成")
print(f"   邮箱: {email}")
print(f"   报告: {REPORT_FILE}")
print("="*50)
