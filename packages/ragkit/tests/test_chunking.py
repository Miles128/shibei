"""结构化 Markdown 分块。"""

from ragkit.chunking import chunk_markdown, split_frontmatter


def test_strips_frontmatter():
    meta, body = split_frontmatter("---\ntopic: ai\n---\n正文")
    assert "topic: ai" in meta
    assert body.strip() == "正文"


def test_short_document_is_single_chunk():
    chunks = chunk_markdown("---\ntopic: x\n---\n只有一段。")
    assert chunks == [{"content": "只有一段。", "section_title": ""}]


def test_h2_and_h3_become_section_titles():
    text = (
        "## 架构\n"
        "总览内容。\n"
        "### 存储\n"
        + "存储细节。" * 200
        + "\n### 检索\n"
        + "检索细节。" * 200
    )
    chunks = chunk_markdown(text, max_chars=300)
    titles = {chunk["section_title"] for chunk in chunks}
    assert "架构" in titles
    assert any("架构 > 存储" == title for title in titles)
    assert any("架构 > 检索" == title for title in titles)


def test_code_block_stays_atomic():
    code = "```python\n" + "print('x')\n" * 40 + "```"
    text = "## 示例\n" + "前言。" * 100 + "\n" + code + "\n" + "后记。" * 100
    chunks = chunk_markdown(text, max_chars=200, overlap_ratio=0.1)
    contents = [chunk["content"] for chunk in chunks]
    assert any(item.startswith("```python") and item.endswith("```") for item in contents)
    code_chunk = next(item for item in contents if item.startswith("```python"))
    assert code_chunk.count("```") == 2


def test_table_stays_atomic():
    table = "| a | b |\n|---|---|\n" + "\n".join(f"| {i} | {i} |" for i in range(30))
    text = "## 数据\n" + "说明。" * 100 + "\n\n" + table
    chunks = chunk_markdown(text, max_chars=200)
    assert any(chunk["content"].startswith("| a | b |") for chunk in chunks)


def test_long_plain_text_respects_max_chars_and_overlap():
    text = "## 长文\n" + "\n\n".join(f"第{i}段。" * 20 for i in range(30))
    chunks = chunk_markdown(text, max_chars=300, overlap_ratio=0.2)
    assert len(chunks) > 3
    assert all(len(chunk["content"]) <= 300 + 60 for chunk in chunks)


def test_empty_input():
    assert chunk_markdown("") == []
    assert chunk_markdown("   \n  ") == []
