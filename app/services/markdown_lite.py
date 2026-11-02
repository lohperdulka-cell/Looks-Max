"""Минимальный безопасный Markdown-рендерер для контента блога, FAQ и планов.

Поддерживает: заголовки, абзацы, списки, цитаты, блоки и инлайн-код, ссылки,
жирный и курсив, горизонтальные линии. HTML из текста экранируется, поэтому
разметка из админки не может внедрить скрипт (XSS). Внешние зависимости не
нужны — намеренно вместо полноценного парсера.
"""

from __future__ import annotations

import html
import re

_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?!\w)")
_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_ITEM = re.compile(r"^\s*[-*]\s+(.*)$")
_ORDERED_ITEM = re.compile(r"^\s*\d+\.\s+(.*)$")


def _inline(text: str) -> str:
    """Разметка внутри строки. Порядок важен: код до ссылок, ссылки до эмфазы."""
    out = html.escape(text, quote=False)
    out = _CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    # URL подставляется в атрибут href, поэтому его мало экранировать как текст
    # (quote=False не трогает кавычки) — экранируем с quote=True, иначе кавычка
    # в ссылке закрывает атрибут и добавляет свой (onmouseover=...).
    out = _LINK.sub(
        lambda m: (
            f'<a href="{html.escape(m.group(2), quote=True)}" target="_blank" '
            f'rel="noopener noreferrer nofollow">{m.group(1)}</a>'
        ),
        out,
    )
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITALIC.sub(r"<em>\1</em>", out)
    return out


def render(text: str | None) -> str:
    """Преобразует Markdown в HTML. Возвращает пустую строку для None."""
    if not text:
        return ""

    lines = text.replace("\r\n", "\n").split("\n")
    blocks: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    list_tag = "ul"

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if list_items:
            items = "".join(f"<li>{_inline(item)}</li>" for item in list_items)
            blocks.append(f"<{list_tag}>{items}</{list_tag}>")
            list_items.clear()

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            flush_list()
            continue

        if stripped in {"---", "***", "___"}:
            flush_paragraph()
            flush_list()
            blocks.append("<hr>")
            continue

        heading = _HEADING.match(stripped)
        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            flush_list()
            blocks.append(f"<blockquote>{_inline(stripped[1:].strip())}</blockquote>")
            continue

        ordered = _ORDERED_ITEM.match(line)
        if ordered:
            flush_paragraph()
            if list_items and list_tag != "ol":
                flush_list()
            list_tag = "ol"
            list_items.append(ordered.group(1))
            continue

        bullet = _LIST_ITEM.match(line)
        if bullet:
            flush_paragraph()
            if list_items and list_tag != "ul":
                flush_list()
            list_tag = "ul"
            list_items.append(bullet.group(1))
            continue

        flush_list()
        paragraph.append(stripped)

    flush_paragraph()
    flush_list()
    return "\n".join(blocks)


def plain_text(text: str | None, limit: int = 200) -> str:
    """Убирает разметку и обрезает текст — для meta-описаний и превью."""
    if not text:
        return ""
    cleaned = re.sub(r"[#*`>\-]", " ", text)
    cleaned = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit]
