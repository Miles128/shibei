"""Chunker 模块测试 — overlap + 句子边界 + parent-child"""

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
    # 三个短句应合并到一两个 chunk
    assert len(chunks) >= 1
    assert "短句一" in chunks[0]


def test_merge_sentences_with_overlap():
    """overlap 模式：相邻 chunk 末尾重叠"""
    # 用短句 + 足够 max_chars，确保走合并分支而非硬切
    sents = ["短句一内容。", "短句二内容。", "短句三内容。"]
    chunks = _merge_sentences(sents, max_chars=10, overlap=3)
    # 至少 2 个 chunk（三句总长 21 > 10）
    assert len(chunks) >= 2
    # overlap 区域：第一个 chunk 末尾 3 字符应出现在第二个 chunk 开头附近
    if len(chunks) >= 2:
        overlap_part = chunks[0][-3:]
        # 第二个 chunk 前 20 字符内应包含 overlap 的某个字符
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
    # overlap 模式下 chunk 数可能更多（因为每个 chunk 实际容量减少）
    assert len(chunks_with_overlap) >= len(chunks_no_overlap)


def test_chunk_text_parent_child():
    """parent-child 模式返回 ChildChunk"""
    text = "## A\n\n内容A。\n\n## B\n\n内容B。\n\n## C\n\n内容C。"
    result = chunk_text(
        text, max_chars=800, parent_window=2, source="/test.md"
    )
    assert isinstance(result, list)
    assert all(isinstance(c, ChildChunk) for c in result)
    # 每个 child 都有 parent_text 和 parent_id
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
        # 第一个 child 的 parent 应包含 child[0] 和 child[1] 的内容
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
    # 第一个 parent 包含 chunk0 + chunk1
    assert "chunk0" in result[0].parent_text
    assert "chunk1" in result[0].parent_text
    # 第二个 parent 包含 chunk1 + chunk2
    assert "chunk1" in result[1].parent_text
    assert "chunk2" in result[1].parent_text
