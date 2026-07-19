"""导入流程与摘要生成功能集成测试"""

import tempfile
from pathlib import Path

from shibei.config import Config, StorageConfig, LLMConfig
from shibei.importer import Importer
from shibei.bm25 import BM25Index
import sqlite3


def _cfg(source_dir: Path, db_path: Path, llm_cfg: LLMConfig | None = None) -> Config:
    db_path.mkdir(parents=True, exist_ok=True)
    return Config(
        storage=StorageConfig(path=str(db_path)),
        sources=[str(source_dir)],
        extensions=[".md"],
        llm=llm_cfg or LLMConfig(enabled=False),
    )


def test_import_disabled_summary_stored_as_empty():
    """LLM 摘要禁用时，summary 列应为空字符串"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source_dir = root / "notes"
        source_dir.mkdir()
        db_path = root / "db"
        (source_dir / "note.md").write_text(
            "这是一段足够长的测试内容，用于验证摘要字段。", encoding="utf-8"
        )

        cfg = _cfg(source_dir, db_path)
        result = Importer(cfg).run()
        assert result["status"] == "ok"
        assert result["chunks"] == 1

        with sqlite3.connect(str(db_path / "fts5.db")) as conn:
            row = conn.execute("SELECT summary FROM chunks").fetchone()
        assert row is not None
        assert row[0] == ""


def test_import_enabled_summary_stored(monkeypatch):
    """LLM 摘要启用时，生成的摘要应写入 summary 列"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source_dir = root / "notes"
        source_dir.mkdir()
        db_path = root / "db"
        (source_dir / "note.md").write_text("这是需要生成摘要的原文。", encoding="utf-8")

        cfg = _cfg(source_dir, db_path, llm_cfg=LLMConfig(enabled=True))
        importer = Importer(cfg)

        def fake_summarize(content: str) -> str:
            return "一句话中文摘要。"

        monkeypatch.setattr(importer.summarizer, "summarize", fake_summarize)
        result = importer.run()
        assert result["status"] == "ok"
        assert result["chunks"] == 1

        with sqlite3.connect(str(db_path / "fts5.db")) as conn:
            row = conn.execute("SELECT summary FROM chunks").fetchone()
        assert row is not None
        assert row[0] == "一句话中文摘要。"


def test_import_summary_failure_degrades(monkeypatch):
    """摘要生成失败时降级为空字符串，不影响导入成功"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source_dir = root / "notes"
        source_dir.mkdir()
        db_path = root / "db"
        (source_dir / "note.md").write_text(
            "这是一段足够长的内容，用于验证摘要失败降级。", encoding="utf-8"
        )

        cfg = _cfg(source_dir, db_path, llm_cfg=LLMConfig(enabled=True))
        importer = Importer(cfg)

        def fake_chat(messages):
            raise RuntimeError("模拟 LLM 失败")

        # 在 ChunkSummarizer 内部降级；需有 client 才能走到 chat
        assert importer.summarizer._client is not None
        monkeypatch.setattr(importer.summarizer._client, "chat", fake_chat)
        monkeypatch.setenv("SHIBEI_LLM_API_KEY", "sk-test")
        result = importer.run()
        assert result["status"] == "ok"

        bm25 = BM25Index(str(db_path / "fts5.db"))
        assert bm25.count() == 1

        with sqlite3.connect(str(db_path / "fts5.db")) as conn:
            row = conn.execute("SELECT summary FROM chunks").fetchone()
        assert row is not None
        assert row[0] == ""
