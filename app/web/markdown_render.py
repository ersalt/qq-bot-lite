"""README 的 Markdown → 安全 HTML。

插件 README 是第三方内容，渲染后必须过白名单：不执行脚本、不加载外部图片。
"""

from __future__ import annotations

import markdown as _markdown
import nh3

_ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "del",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}
_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "code": {"class"},
    "th": {"align"},
    "td": {"align"},
}
_URL_SCHEMES = {"http", "https", "mailto"}


def render_markdown(text: str) -> str:
    """渲染常用 Markdown 扩展，并用 nh3 清洗。"""
    html = _markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "sane_lists"],
        output_format="html",
    )
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        url_schemes=_URL_SCHEMES,
        link_rel="noopener noreferrer",
    )
