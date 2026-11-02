"""群成员目录：把模型写的 @昵称 解析成真实 QQ 号。"""

from __future__ import annotations

import logging
import time

from .client import OneBotConnection

log = logging.getLogger(__name__)


class MemberDirectory:
    """按群缓存 名片/昵称 → QQ 号 的映射，默认 10 分钟过期。"""

    def __init__(self, ttl: float = 600.0):
        self.ttl = ttl
        self._cache: dict[int, tuple[float, dict[str, int]]] = {}

    async def resolve(self, conn: OneBotConnection, group_id: int, name: str) -> int | None:
        mapping = await self._mapping(conn, group_id)
        return mapping.get(name.strip())

    async def _mapping(self, conn: OneBotConnection, group_id: int) -> dict[str, int]:
        now = time.time()
        cached = self._cache.get(group_id)
        if cached is not None and now - cached[0] < self.ttl:
            return cached[1]

        try:
            data = await conn.call_action("get_group_member_list", {"group_id": group_id}, 10.0)
        except Exception as exc:  # noqa: BLE001 - 拿不到名单就退化成纯文本
            log.debug("获取群成员失败 group=%s: %s", group_id, exc)
            return cached[1] if cached else {}

        mapping: dict[str, int] = {}
        for member in data or []:
            if not isinstance(member, dict):
                continue
            try:
                user_id = int(member.get("user_id"))
            except (TypeError, ValueError):
                continue
            card = str(member.get("card") or "").strip()
            nickname = str(member.get("nickname") or "").strip()
            # 名片优先，昵称不覆盖名片，避免重名时乱指
            if card:
                mapping[card] = user_id
            if nickname:
                mapping.setdefault(nickname, user_id)

        self._cache[group_id] = (now, mapping)
        log.debug("群成员缓存 group=%s 人数=%d", group_id, len(mapping))
        return mapping
