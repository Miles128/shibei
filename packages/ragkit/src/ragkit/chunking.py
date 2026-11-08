"""结构化 Markdown 分块。

为什么不用「按字符硬切」：笔记里的二级/三级标题是天然语义边界，
代码块和表格被切断后既检索不到也用不了。这里保留标题层级
（``A > B``）作为 ``section_title``，并让代码块/表格原子化。

非 Markdown 文档（docx/xlsx/csv）不要用本函数——那是 shibei 通用
chunker 的场景。
"""

from __future__ import annotations

import re

_H2 = re.compile(r"^(## .+)$", re.MULTILINE)
_H3 = re.compile(r"^(### .+)$", re.MULTILINE)
_CODE = re.compile(r"(```[\s\S]*?```)", re.MULTILINE)
_TABLE = re.compile(r"(\|.*\|(\n\|[-:|]+\|)?(\n\|.*\|)*)", re.MULTILINE)
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def split_frontmatter(text: str) -> tuple[str, str]:
    """返回 ``(frontmatter, body)``；没有 frontmatter 时前半为空。"""
    match = _FRONTMATTER.match(text or "")
    if not match:
        return "", text or ""
    return match.group(1), text[match.end():]


def chunk_markdown(
    text: str,
    *,
    max_chars: int = 1000,
    overlap_ratio: float = 0.2,
    overlap_min: int = 100,
    strip_frontmatter: bool = True,
) -> list[dict]:
    """把 Markdown 切成 ``[{"content", "section_title"}]``。"""
    if strip_frontmatter:
        _, text = split_frontmatter(text)
    body = text or ""
    if len(body.strip()) <= max_chars:
        content = body.strip()
        return [{"content": content, "section_title": ""}] if content else []

    overlap = max(overlap_min, int(max_chars * overlap_ratio))
    chunks: list[dict] = []
    parts = _H2.split(body)

    if parts and parts[0].strip():
        _add_block(parts[0], "", max_chars, overlap, chunks)
    for index in range(1, len(parts), 2):
        title = re.sub(r"^##\s*", "", parts[index].strip())
        content = parts[index + 1] if index + 1 < len(parts) else ""
        if content.strip():
            _add_block(content, title, max_chars, overlap, chunks)
    return chunks


def _add_block(
    content: str,
    section_title: str,
    max_chars: int,
    overlap: int,
    chunks: list[dict],
) -> None:
    if len(content) <= max_chars:
        _append(chunks, content, section_title)
        return

    parts = _H3.split(content)
    if len(parts) > 1:
        if parts[0].strip():
            _add_paragraphs(parts[0], section_title, max_chars, overlap, chunks)
        for index in range(1, len(parts), 2):
            sub_title = re.sub(r"^###\s*", "", parts[index].strip())
            full = f"{section_title} > {sub_title}" if section_title else sub_title
            sub_content = parts[index + 1] if index + 1 < len(parts) else ""
            if sub_content.strip():
                _add_paragraphs(sub_content, full, max_chars, overlap, chunks)
        return

    _add_paragraphs(content, section_title, max_chars, overlap, chunks)


def _add_paragraphs(
    content: str,
    section_title: str,
    max_chars: int,
    overlap: int,
    chunks: list[dict],
) -> None:
    if len(content) <= max_chars:
        _append(chunks, content, section_title)
        return

    current = ""
    for segment in _split_segments(content):
        segment = segment.strip()
        if not segment:
            continue

        atomic = _is_table(segment) or _is_code(segment)
        if atomic:
            if current:
                _append(chunks, current, section_title)
                current = current[-overlap:] if len(current) > overlap else ""
            _append(chunks, segment, section_title)
            continue

        if len(current) + len(segment) + 2 > max_chars and current:
            _append(chunks, current, section_title)
            current = (
                current[-overlap:] + "\n\n" + segment
                if len(current) > overlap
                else segment
            )
        else:
            current = current + "\n\n" + segment if current else segment

    if current.strip():
        _append(chunks, current, section_title)


def _append(chunks: list[dict], content: str, section_title: str) -> None:
    content = content.strip()
    if content:
        chunks.append({"content": content, "section_title": section_title})


def _split_segments(content: str) -> list[str]:
    segments: list[str] = []
    pos = 0
    while pos < len(content):
        code = _CODE.search(content, pos)
        table = _TABLE.search(content, pos)
        if code is not None and (table is None or code.start() < table.start()):
            if code.start() > pos:
                segments.append(content[pos:code.start()])
            segments.append(code.group(1))
            pos = code.end()
        elif table is not None:
            if table.start() > pos:
                segments.append(content[pos:table.start()])
            segments.append(table.group(1))
            pos = table.end()
        else:
            segments.extend(re.split(r"\n{2,}", content[pos:]))
            break
    return segments


def _is_table(text: str) -> bool:
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return False
    first = lines[0].strip()
    if not (first.startswith("|") and first.endswith("|")):
        return False
    second = lines[1].strip()
    return second.startswith("|") and bool(re.match(r"^\|[-:|]+\|$", second))


def _is_code(text: str) -> bool:
    text = text.strip()
    return text.startswith("```") and text.endswith("```")
