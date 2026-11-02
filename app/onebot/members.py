"""群成员目录：双向映射 QQ 号 ↔ 群名片/昵称。

两个用途：
1. 把模型写的 `@昵称` 解析成真实 QQ 号（发送前）。
2. 把收到消息里的 `@123456` 还原成 `@群名片(123456)`（喂给模型前），
   否则模型只看到一句「叫这个 儿子」，根本不知道在叫谁。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from .client import OneBotConnection

log = logging.getLogger(__name__)


@dataclass
class GroupMembers:
    by_name: dict[str, int] = field(default_factory=dict)
    by_id: dict[int, str] = field(default_factory=dict)


class MemberDirectory:
    """按群缓存成员映射，默认 10 分钟过期。"""

    def __init__(self, ttl: float = 600.0):
        self.ttl = ttl
        self._cache: dict[int, tuple[float, GroupMembers]] = {}

    async def resolve(self, conn: OneBotConnection, group_id: int, name: str) -> int | None:
        members = await self._load(conn, group_id)
        return members.by_name.get(name.strip())

    async def name_of(self, conn: OneBotConnection, group_id: int, user_id: int) -> str | None:
        members = await self._load(conn, group_id)
        return members.by_id.get(user_id)

    async def expand(self, conn: OneBotConnection, group_id: int, text: str) -> str:
        """把正文里的 at 123456 换成 at 群名片(123456)。"""
        if " at " not in f" {text} ":
            return text
        members = await self._load(conn, group_id)

        def repl(match: "re.Match[str]") -> str:
            uid = int(match.group(1))
            name = members.by_id.get(uid)
            return f"at {name}({uid})" if name else match.group(0)

        return re.sub(r"(?<![A-Za-z0-9_])at\s+(\d{3,12})(?!\d)", repl, text)

    async def _load(self, conn: OneBotConnection, group_id: int) -> GroupMembers:
        now = time.time()
        cached = self._cache.get(group_id)
        if cached is not None and now - cached[0] < self.ttl:
            return cached[1]

        try:
            data = await conn.call_action("get_group_member_list", {"group_id": group_id}, 10.0)
        except Exception as exc:  # noqa: BLE001 - 拿不到名单就退化成纯文本
            log.debug("获取群成员失败 group=%s: %s", group_id, exc)
            return cached[1] if cached else GroupMembers()

        members = GroupMembers()
        for member in data or []:
            if not isinstance(member, dict):
                continue
            try:
                user_id = int(member.get("user_id"))
            except (TypeError, ValueError):
                continue
            card = str(member.get("card") or "").strip()
            nickname = str(member.get("nickname") or "").strip()
            display = card or nickname
            if display:
                members.by_id[user_id] = display
            # 名片优先，昵称不覆盖名片，避免重名时乱指
            if card:
                members.by_name[card] = user_id
            if nickname:
                members.by_name.setdefault(nickname, user_id)

        self._cache[group_id] = (now, members)
        log.debug("群成员缓存 group=%s 人数=%d", group_id, len(members.by_id))
        return members
