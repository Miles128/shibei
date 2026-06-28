"""Chunker 分块逻辑测试"""

from shibei.chunker import chunk_text
from shibei.config import ChunkingConfig


def _chunk(text, max_chars=800):
    """用 chunk_text 做简单分块，返回字符串列表。"""
    cfg = ChunkingConfig(max_chars=max_chars)
    result = chunk_text(
        text,
        max_chars=cfg.max_chars,
        split_pattern=cfg.split_pattern,
    )
    if result and hasattr(result[0], 'text'):
        return [c.text for c in result]
    return result


def test_chunk_by_heading():
    """按 ## 标题分块"""
    text = "## Section A\n\nContent A.\n\n## Section B\n\nContent B."
    chunks = _chunk(text)
    assert len(chunks) == 2
    assert "Section A" in chunks[0]
    assert "Section B" in chunks[1]


def test_chunk_preserves_h1():
    """# 标题不触发分割，归入前一个 chunk"""
    text = "# Title\n\nFirst section.\n\n## Section A\n\nContent A."
    chunks = _chunk(text)
    assert len(chunks) == 2
    assert "# Title" in chunks[0]
    assert "Section A" in chunks[1]


def test_chunk_skip_short():
    """跳过过短片段（<10 字符）"""
    text = "## A\n\nHi.\n\n## Normal\n\nThis is a normal section with enough content to pass the filter."
    chunks = _chunk(text)
    assert len(chunks) == 1
    assert "Normal" in chunks[0]


def test_chunk_split_long_paragraph():
    """超长段落再切分"""
    long_para = "x" * 200
    text = f"## Section\n\n{long_para}"
    chunks = _chunk(text, max_chars=100)
    assert len(chunks) >= 2


def test_chunk_empty_input():
    """空输入返回空列表"""
    assert _chunk("") == []
    assert _chunk("   ") == []


def test_chunk_single_section():
    """单个 section 不分割"""
    text = "## Only Section\n\nSome content here."
    chunks = _chunk(text, max_chars=1000)
    assert len(chunks) == 1


def test_chunk_non_markdown_paragraphs():
    """非 markdown 内容按段落分块"""
    text = "First paragraph has enough content to exceed the limit and trigger splitting.\n\nSecond paragraph also has enough content to be its own chunk when splitting.\n\nThird paragraph."
    chunks = _chunk(text, max_chars=100)
    assert len(chunks) >= 2
    assert "First paragraph" in chunks[0]
