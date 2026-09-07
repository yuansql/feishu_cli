#!/usr/bin/env python3
import json, urllib.request, os, time

KEY = "sk-Ued5F0wqPjETL270MImOj6KXSzY6eEyfXISNQ3EgfH7fOsxJ"
BASE = "https://delta-router.yangtzeailab.com"
OUT = os.path.expanduser("~/Desktop/tmp/捡钱/spend_probe.json")
records = []

def call_once(tag):
    data = {"model": "DeepSeek-V4-Flash", "messages": [{"role": "user", "content": "说一个字"}], "max_tokens": 2}
    req = urllib.request.Request(
        BASE + "/v1/chat/completions",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        hdrs = {k: v for k, v in resp.getheaders()}
        spend = hdrs.get("X-Litellm-Key-Spend", "N/A")
        cost = hdrs.get("X-Litellm-Response-Cost", "N/A")
        records.append({"tag": tag, "key_spend": spend, "response_cost": cost})
        print(f"{tag}: X-Litellm-Key-Spend={spend}, Response-Cost={cost}")

for i in range(3):
    call_once(f"call-{i+1}")
    time.sleep(1)

with open(OUT, "w") as f:
    json.dump(records, f, indent=2, ensure_ascii=False)
print(f"\nreport: {OUT}")
print("说明：X-Litellm-Key-Spend 是累计到当前 key 的总消费额（元）。")
