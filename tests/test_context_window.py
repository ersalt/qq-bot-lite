"""上下文窗口的回归测试：锚点窗口必须包含最新消息。"""

from __future__ import annotations

from app.config import ContextConfig
from app.context.store import MessageStore, StoredMessage
from app.context.window import build_ambient, build_history, keep_addressed, pick_anchor_window


def _store(tmp_path) -> MessageStore:
    return MessageStore(tmp_path / "messages.db")


def _msg(
    mid: int,
    role: str,
    content: str,
    *,
    speaker: str | None = None,
    reply_to: int | None = None,
    has_image: bool = False,
) -> StoredMessage:
    return StoredMessage(
        id=mid,
        session="group:1",
        role=role,
        speaker=speaker or ("alice" if role == "user" else "bot"),
        user_id=1,
        content=content,
        has_image=has_image,
        created_at=0.0,
        reply_to=reply_to,
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

        # 锚点推进后，下一轮仍然能看到最新的一轮，而不是停在旧窗口
        user_id = store.add("group:1", "user", "latest")
        store.add("group:1", "assistant", "latest answer", reply_to=user_id)
        messages = store.latest_after("group:1", anchor, 512)
        addressed = keep_addressed(messages)
        window, _ = pick_anchor_window(addressed, 40)
        assert window[-1].content == "latest answer"
    finally:
        store.close()


def test_keep_addressed_drops_chatter_and_trailing_backlog():
    messages = [
        _msg(1, "user", "闲话一"),
        _msg(2, "user", "问它"),
        _msg(3, "assistant", "回答"),
        _msg(4, "user", "闲话二"),
        _msg(5, "user", "闲话三"),
    ]
    kept = keep_addressed(messages)
    assert [item.content for item in kept] == ["问它", "回答"]


def test_build_ambient_formats_chatter_after_last_reply():
    messages = [
        _msg(1, "user", "插话", speaker="bob"),
        _msg(2, "user", "踢你屁股", speaker="alice"),
    ]
    block = build_ambient(messages)
    assert block.startswith("<system_reminder>")
    assert "「bob」: 插话" in block
    assert "「alice」: 踢你屁股" in block
    assert block.endswith("</system_reminder>")
    assert "--- END CONTEXT ---" in block


def test_build_ambient_marks_images():
    block = build_ambient([_msg(1, "user", "", speaker="bob", has_image=True)])
    assert "「bob」: [图片]" in block


def test_build_ambient_marks_directed_messages():
    block = build_ambient(
        [_msg(1, "user", "at 小鲸鱼(10000) 在吗", speaker="bob")],
        self_id=10000,
    )
    assert "⚠️[DIRECTED AT YOU]" in block
    assert "at 小鲸鱼(10000) 在吗" in block


def test_build_ambient_skips_empty_and_assistant():
    assert build_ambient([]) == ""
    assert build_ambient([_msg(1, "user", "", speaker="bob")]) == ""
    assert build_ambient([_msg(1, "assistant", "不该在这里")]) == ""


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


def test_keep_addressed_uses_reply_link_not_adjacency():
    question = _msg(1, "user", "问它", speaker="alice")
    chatter = _msg(2, "user", "别人插话", speaker="bob")
    answer = _msg(3, "assistant", "回答", reply_to=1)

    kept = keep_addressed([question, chatter, answer])
    assert [item.id for item in kept] == [1, 3]


def test_keep_addressed_falls_back_for_old_rows():
    first = _msg(1, "user", "第一条", speaker="alice")
    second = _msg(2, "user", "第二条", speaker="bob")
    old_answer = _msg(3, "assistant", "老回复")  # 没有 reply_to

    kept = keep_addressed([first, second, old_answer])
    assert [item.id for item in kept] == [2, 3]


def test_build_history_marks_assistant_addressee():
    messages = [
        _msg(1, "user", "问它", speaker="alice"),
        _msg(2, "assistant", "回答", reply_to=1),
    ]
    history = build_history(messages, ContextConfig(), is_group=True)
    assert "「alice」: 问它" in history[0]["content"]
    assert history[1]["content"] == "（回 「alice」）回答"

    # 触发消息不在窗口里时不硬编一个名字
    outside = build_history(
        [_msg(3, "assistant", "回答", reply_to=99)], ContextConfig(), is_group=True
    )
    assert outside[0]["content"] == "回答"


def test_store_persists_reply_link(tmp_path):
    store = _store(tmp_path)
    try:
        user_id = store.add("group:1", "user", "问")
        store.add("group:1", "assistant", "答", reply_to=user_id)
        messages = store.latest_after("group:1", 0, 10)
        assert messages[-1].reply_to == user_id
    finally:
        store.close()
