"""假 SnowLuma：不接 QQ，直接往本地服务发一条群消息，看看会不会回。

用法：
    .venv\\Scripts\\python.exe scripts\\fake_snowluma.py
    .venv\\Scripts\\python.exe scripts\\fake_snowluma.py --text "在吗" --at
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid

from websockets.asyncio.client import connect


def build_event(self_id: int, user_id: int, group_id: int, text: str, mention: bool) -> dict:
    message: list[dict] = []
    if mention:
        message.append({"type": "at", "data": {"qq": str(self_id)}})
    message.append({"type": "text", "data": {"text": text}})
    return {
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "self_id": self_id,
        "user_id": user_id,
        "group_id": group_id,
        "message_id": int(time.time() * 1000) % 2_000_000_000,
        "message": message,
        "raw_message": text,
        "sender": {"user_id": user_id, "nickname": "阿伟", "card": "阿伟"},
        "time": int(time.time()),
    }


async def run(args: argparse.Namespace) -> int:
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    print(f"连接 {args.url} ...")
    async with connect(args.url, additional_headers=headers) as ws:
        print("已连接，发送测试消息")
        await ws.send(json.dumps(build_event(args.self_id, args.user_id, args.group_id, args.text, args.at)))
        deadline = time.monotonic() + args.timeout
        count = 0
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
            except (asyncio.TimeoutError, TimeoutError):
                break
            payload = json.loads(raw)
            if "action" not in payload:
                continue
            count += 1
            params = payload.get("params", {})
            print(f"\n[{count}] {payload['action']} -> {params.get('group_id') or params.get('user_id')}")
            for seg in params.get("message", []):
                print("   ", json.dumps(seg, ensure_ascii=False))
            await ws.send(
                json.dumps(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "data": {"message_id": int(uuid.uuid4().int % 1_000_000)},
                        "echo": payload.get("echo"),
                    }
                )
            )
        print(f"\n共收到 {count} 条发送动作")
        return 0 if count else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8790/onebot/v11/ws")
    parser.add_argument("--token", default="maid-lite-2026")
    parser.add_argument("--text", default="在吗")
    parser.add_argument("--self-id", type=int, default=10000)
    parser.add_argument("--user-id", type=int, default=123456)
    parser.add_argument("--group-id", type=int, default=777888)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--at", action="store_true", default=True)
    parser.add_argument("--no-at", dest="at", action="store_false")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
