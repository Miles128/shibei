"""结构化 Markdown 分块：section 元数据与代码块原子性。"""

from shibei.services import Shibei


def _config(tmp_path, notes):
    notes.mkdir()
    db = tmp_path / "db"
    db.mkdir()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"storage:\n  path: {db}\nsources:\n  - {notes}\nextensions:\n  - .md\n",
        encoding="utf-8",
    )
    return cfg


def test_markdown_chunks_carry_section(tmp_path):
    notes = tmp_path / "notes"
    cfg = _config(tmp_path, notes)
    (notes / "a.md").write_text(
        "## 架构\n存储层用 SQLite。\n\n## 检索\nBM25 与向量融合。\n",
        encoding="utf-8",
    )
    brain = Shibei(str(cfg))
    brain.import_all()

    out = brain.recall(query="SQLite", full_text=True)
    assert out["results"]
    assert out["results"][0]["section"] == "架构"


def test_code_block_is_not_split_across_chunks(tmp_path):
    notes = tmp_path / "notes"
    cfg = _config(tmp_path, notes)
    code = "```python\n" + "print('x')\n" * 80 + "```"
    (notes / "code.md").write_text(
        "## 示例\n" + "前言。" * 120 + "\n\n" + code + "\n\n" + "后记。" * 120,
        encoding="utf-8",
    )
    brain = Shibei(str(cfg))
    brain.import_all()

    chunks = brain.list_chunks(limit=100)["items"]
    code_chunks = [c for c in chunks if "```python" in c["text"]]
    assert code_chunks, "代码块应作为独立 chunk"
    assert code_chunks[0]["text"].count("```") == 2
    assert code_chunks[0]["section"] == "示例"


def test_non_markdown_falls_back_to_generic_chunking(tmp_path):
    notes = tmp_path / "notes"
    cfg = _config(tmp_path, notes)
    (notes / "a.md").write_text("没有标题的正文。\n" * 50, encoding="utf-8")
    brain = Shibei(str(cfg))
    brain.import_all()
    items = brain.list_chunks(limit=10)["items"]
    assert items and items[0]["section"] == ""
