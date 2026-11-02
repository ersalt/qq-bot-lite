"""检查 LM Studio 是否可用、模型名是否匹配、单 system 消息能否正常返回。

用法：
    .venv\\Scripts\\python.exe scripts\\check_llm.py
    .venv\\Scripts\\python.exe scripts\\check_llm.py --model qwen3.8-27b
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_config  # noqa: E402
from app.llm.client import LLMClient, LLMError  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="覆盖配置里的模型 id")
    parser.add_argument("--base-url", help="覆盖配置里的 base_url")
    args = parser.parse_args()

    cfg = load_config()
    if args.model:
        cfg.llm.model = args.model
    if args.base_url:
        cfg.llm.base_url = args.base_url

    print(f"base_url = {cfg.llm.base_url}")
    print(f"model    = {cfg.llm.model}")

    llm = LLMClient(cfg.llm)
    try:
        models = await llm.list_models()
        print(f"\n已加载模型 ({len(models)}):")
        for name in models:
            mark = "  <-- 配置使用的" if name == cfg.llm.model else ""
            print(f"  - {name}{mark}")
        if cfg.llm.model not in models:
            print("\n[!] 配置里的 model 不在已加载列表中，请求大概率 404/400。")

        messages = [
            {"role": "system", "content": "你是测试对象，只回两个字。"},
            {"role": "user", "content": "测试：请只回“正常”二字。"},
        ]
        started = time.perf_counter()
        reply = await llm.complete(messages)
        elapsed = (time.perf_counter() - started) * 1000
        print(f"\n单次请求 OK，{elapsed:.0f}ms，返回: {reply.strip()[:80]!r}")
        if llm.last_usage:
            print(f"usage: {llm.last_usage}")
        return 0
    except LLMError as exc:
        print(f"\n[失败] {exc}")
        print("检查：LM Studio 是否已启动、是否开了 Local Server、端口是否 1234。")
        return 1
    finally:
        await llm.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
