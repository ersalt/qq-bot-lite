"""测一次生成速度：固定 prompt，思考开着，报吞吐。"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

URL = "http://127.0.0.1:1234/v1/chat/completions"

FILLER = "群里有人随口聊了一句闲话，没什么信息量。" * 40

body = {
    "model": "qwen3.8-27b",
    "temperature": 0.8,
    "max_tokens": 300,
    "stream": True,
    "stream_options": {"include_usage": True},
    "messages": [
        {"role": "system", "content": "你在QQ群里聊天，说话简短口语化。"},
        {"role": "user", "content": FILLER},
        {"role": "assistant", "content": "嗯。"},
        {"role": "user", "content": "为什么我打游戏总是输？"},
    ],
}

req = urllib.request.Request(
    URL,
    data=json.dumps(body, ensure_ascii=False).encode(),
    headers={"Content-Type": "application/json"},
)
t0 = time.perf_counter()
first_think = first_content = None
usage = None
with urllib.request.urlopen(req, timeout=300) as resp:
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        data = json.loads(payload)
        if data.get("usage"):
            usage = data["usage"]
        choice = (data.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        if delta.get("reasoning_content") and first_think is None:
            first_think = time.perf_counter()
        if delta.get("content") and first_content is None:
            first_content = time.perf_counter()

total = (time.perf_counter() - t0) * 1000
u = usage or {}
det = u.get("completion_tokens_details") or {}
think = det.get("reasoning_tokens") or 0
out = u.get("completion_tokens") or 0
prompt = u.get("prompt_tokens")

prefill = ((first_think or t0) - t0) * 1000
think_s = ((first_content or t0) - (first_think or t0))
gen_speed = think / think_s if think and think_s > 0 else 0

print(
    f"prompt={prompt} think={think} out={out} "
    f"预填充={prefill:.0f}ms 思考={think_s*1000:.0f}ms 生成={gen_speed:.1f}t/s 总计={total:.0f}ms"
)
sys.exit(0)
