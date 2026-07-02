"""Chunker 模块测试 — 分块、overlap、句子边界、parent-child"""

from shibei.chunker import (
    chunk_text, _split_by_sentences, _merge_sentences,
    _build_parent_child, ChildChunk,
)


def test_split_by_sentences_chinese():
    """中文按句号切分，保留标点"""
    text = "人工智能正在改变世界。大模型技术突破。机器学习应用广泛。"
    sents = _split_by_sentences(text, "。！？!?\n")
    assert len(sents) == 3
    assert sents[0].endswith("。")
    assert "人工智能" in sents[0]


def test_split_by_sentences_mixed():
    """中英混合"""
    text = "AI 是未来。Machine learning is powerful！真的吗？"
    sents = _split_by_sentences(text, "。！？!?\n")
    assert len(sents) == 3


def test_merge_sentences_basic():
    """句子合并到 max_chars 内"""
    sents = ["短句一。", "短句二。", "短句三。"]
    chunks = _merge_sentences(sents, max_chars=20, overlap=0)
    assert len(chunks) >= 1
    assert "短句一" in chunks[0]


def test_merge_sentences_with_overlap():
    """overlap 模式：相邻 chunk 末尾重叠"""
    sents = ["短句一内容。", "短句二内容。", "短句三内容。"]
    chunks = _merge_sentences(sents, max_chars=10, overlap=3)
    assert len(chunks) >= 2
    if len(chunks) >= 2:
        overlap_part = chunks[0][-3:]
        assert any(c in chunks[1][:20] for c in overlap_part), \
            f"overlap {overlap_part!r} not found in {chunks[1][:20]!r}"


def test_chunk_text_basic():
    """基本分块：按 ## 标题"""
    text = "## A\n\n内容A。\n\n## B\n\n内容B。"
    chunks = chunk_text(text, max_chars=800)
    assert len(chunks) == 2
    assert "A" in chunks[0]


def test_chunk_text_with_overlap():
    """overlap 配置生效"""
    text = "## Section\n\n" + "这是一段很长的内容。" * 20
    chunks_no_overlap = chunk_text(text, max_chars=50, overlap=0)
    chunks_with_overlap = chunk_text(text, max_chars=50, overlap=10)
    assert len(chunks_with_overlap) >= len(chunks_no_overlap)


def test_chunk_text_parent_child():
    """parent-child 模式返回 ChildChunk"""
    text = "## A\n\n内容A。\n\n## B\n\n内容B。\n\n## C\n\n内容C。"
    result = chunk_text(
        text, max_chars=800, parent_window=2, source="/test.md"
    )
    assert isinstance(result, list)
    assert all(isinstance(c, ChildChunk) for c in result)
    for cc in result:
        assert cc.parent_text
        assert cc.parent_id.startswith("/test.md#parent_")
        assert cc.child_index >= 0


def test_chunk_text_parent_window_contains_children():
    """parent_text 包含 parent_window 个 child 的内容"""
    text = "## A\n\n内容A。\n\n## B\n\n内容B。\n\n## C\n\n内容C。"
    result = chunk_text(
        text, max_chars=800, parent_window=2, source="/test.md"
    )
    if len(result) >= 2:
        first = result[0]
        assert result[0].text in first.parent_text
        assert result[1].text in first.parent_text


def test_chunk_text_empty():
    """空输入"""
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_chunk_text_skip_short():
    """跳过过短片段"""
    text = "## A\n\nHi.\n\n## Normal\n\nThis is normal content with enough text."
    chunks = chunk_text(text, max_chars=800)
    assert len(chunks) == 1
    assert "Normal" in chunks[0]


def test_build_parent_child_empty():
    """空列表"""
    result = _build_parent_child([], parent_window=2, source="/test.md")
    assert result == []


def test_build_parent_child_window():
    """滑动窗口生成 parent"""
    chunks = ["chunk0", "chunk1", "chunk2", "chunk3"]
    result = _build_parent_child(chunks, parent_window=2, source="/test.md")
    assert len(result) == 4
    assert "chunk0" in result[0].parent_text
    assert "chunk1" in result[0].parent_text
    assert "chunk1" in result[1].parent_text
    assert "chunk2" in result[1].parent_text


def test_chunk_preserves_h1():
    """# 标题不触发分割，归入前一个 chunk"""
    text = "# Title\n\nFirst section.\n\n## Section A\n\nContent A."
    chunks = chunk_text(text, max_chars=800)
    assert len(chunks) == 2
    assert "# Title" in chunks[0]
    assert "Section A" in chunks[1]


def test_chunk_split_long_paragraph():
    """超长段落再切分"""
    long_para = "x" * 200
    text = f"## Section\n\n{long_para}"
    chunks = chunk_text(text, max_chars=100)
    assert len(chunks) >= 2


def test_chunk_single_section():
    """单个 section 不分割"""
    text = "## Only Section\n\nSome content here."
    chunks = chunk_text(text, max_chars=1000)
    assert len(chunks) == 1


def test_chunk_non_markdown_paragraphs():
    """非 markdown 内容按段落分块"""
    text = "First paragraph has enough content to exceed the limit and trigger splitting.\n\nSecond paragraph also has enough content to be its own chunk when splitting.\n\nThird paragraph."
    chunks = chunk_text(text, max_chars=100)
    assert len(chunks) >= 2
    assert "First paragraph" in chunks[0]
