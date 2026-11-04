"""上下文窗口的回归测试：锚点窗口必须包含最新消息。"""

from __future__ import annotations

from app.config import ContextConfig
from app.context.store import MessageStore, StoredMessage
from app.context.window import build_history, keep_addressed, pick_anchor_window


def _store(tmp_path) -> MessageStore:
    return MessageStore(tmp_path / "messages.db")


def _msg(mid: int, role: str, content: str) -> StoredMessage:
    return StoredMessage(
        id=mid,
        session="group:1",
        role=role,
        speaker="alice" if role == "user" else "bot",
        user_id=1,
        content=content,
        has_image=False,
        created_at=0.0,
    )


def test_latest_after_returns_the_tail(tmp_path):
    store = _store(tmp_path)
    try:
        for index in range(300):
            store.add("group:1", "user", f"m{index}")
        tail = store.latest_after("group:1", 0, 126)
        assert len(tail) == 126
        assert tail[0].content == "m174"
        assert tail[-1].content == "m299"

        # after() 是最早优先：这正是旧实现的 bug 来源，留个对照
        head = store.after("group:1", 0, 126)
        assert head[0].content == "m0"
        assert head[-1].content == "m125"
    finally:
        store.close()


def test_anchor_window_keeps_the_latest_turn(tmp_path):
    store = _store(tmp_path)
    try:
        for index in range(100):
            store.add("group:1", "user", f"u{index}")
            store.add("group:1", "assistant", f"a{index}")

        messages = store.latest_after("group:1", 0, 512)
        addressed = keep_addressed(messages)
        window, anchor = pick_anchor_window(addressed, 40)
        assert window[-1].content == "a99"
        assert anchor is not None
        assert window[0].id > 0

        # 锚点推进后，下一轮仍然能看到最新消息，而不是停在旧窗口
        store.add("group:1", "user", "latest")
        messages = store.latest_after("group:1", anchor, 512)
        addressed = keep_addressed(messages)
        window, _ = pick_anchor_window(addressed, 40)
        assert window[-1].content == "latest"
    finally:
        store.close()


def test_keep_addressed_drops_chatter_and_keeps_backlog():
    messages = [
        _msg(1, "user", "闲话一"),
        _msg(2, "user", "问它"),
        _msg(3, "assistant", "回答"),
        _msg(4, "user", "闲话二"),
        _msg(5, "user", "闲话三"),
    ]
    kept = keep_addressed(messages)
    assert [item.content for item in kept] == ["问它", "回答", "闲话二", "闲话三"]


def test_build_history_respects_bot_reply_switch():
    messages = [
        _msg(1, "user", "问它"),
        _msg(2, "assistant", "回答"),
    ]
    with_bot = build_history(messages, ContextConfig(), is_group=False)
    assert [item["role"] for item in with_bot] == ["user", "assistant"]

    without_bot = build_history(
        messages, ContextConfig(include_bot_replies=False), is_group=False
    )
    assert [item["role"] for item in without_bot] == ["user"]
