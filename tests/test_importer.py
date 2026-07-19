"""Importer 增量导入与清理测试"""

import tempfile
from pathlib import Path

from shibei.config import Config, StorageConfig
from shibei.importer import Importer
from shibei.bm25 import BM25Index


def _cfg(source_dir: Path, db_path: Path) -> Config:
    db_path.mkdir(parents=True, exist_ok=True)
    return Config(
        storage=StorageConfig(path=str(db_path)),
        sources=[str(source_dir)],
        extensions=[".md"],
    )


def test_import_removes_deleted_files():
    """源文件删除后增量导入应清理旧 chunk"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source_dir = root / "notes"
        source_dir.mkdir()
        db_path = root / "db"

        (source_dir / "keep.md").write_text("keep content", encoding="utf-8")
        (source_dir / "old.md").write_text("old content", encoding="utf-8")

        cfg = _cfg(source_dir, db_path)
        importer = Importer(cfg)

        # 首次导入
        result1 = importer.run()
        assert result1["status"] == "ok"
        assert result1["files"] == 2

        bm25 = BM25Index(str(db_path / "fts5.db"))
        assert bm25.count() == 2

        # 删除 old.md 后再次导入
        (source_dir / "old.md").unlink()
        result2 = importer.run()
        assert result2["status"] == "ok"
        assert result2["removed"] == 1

        assert bm25.count() == 1
        sources = bm25.list_sources()
        assert str(source_dir / "keep.md") in sources
        assert str(source_dir / "old.md") not in sources


def test_import_updates_changed_files():
    """文件内容变更后增量导入应替换旧 chunk"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source_dir = root / "notes"
        source_dir.mkdir()
        db_path = root / "db"

        file_path = source_dir / "note.md"
        file_path.write_text("original content", encoding="utf-8")

        cfg = _cfg(source_dir, db_path)
        importer = Importer(cfg)

        result1 = importer.run()
        assert result1["files"] == 1

        bm25 = BM25Index(str(db_path / "fts5.db"))
        assert bm25.count() == 1

        # 修改内容
        file_path.write_text("updated content", encoding="utf-8")
        result2 = importer.run()
        assert result2["files"] == 1
        assert result2["removed"] == 1  # 清理旧 chunk

        # 应该还是只有 1 条（不是 2 条）
        assert bm25.count() == 1

        # 搜索结果应只有新内容
        results = bm25.search("updated")
        assert len(results) == 1
        assert "updated" in results[0]["text"].lower()


def test_import_full_rebuild():
    """--full 应重建数据库，不产生重复"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source_dir = root / "notes"
        source_dir.mkdir()
        db_path = root / "db"

        (source_dir / "a.md").write_text("content alpha", encoding="utf-8")

        cfg = _cfg(source_dir, db_path)
        importer = Importer(cfg)

        importer.run()
        bm25 = BM25Index(str(db_path / "fts5.db"))
        assert bm25.count() == 1

        # 多次 full 导入不应累加
        importer.run(full=True)
        importer.run(full=True)
        assert bm25.count() == 1


def test_import_handles_parser_error_and_reports_progress(monkeypatch, tmp_path):
    """单个文件解析异常不应中断整批导入，且进度回调按文件触发。"""
    import shibei.importer

    source_dir = tmp_path / "notes"
    source_dir.mkdir()
    db_path = tmp_path / "db"
    db_path.mkdir()

    (source_dir / "ok.md").write_text("ok content", encoding="utf-8")
    (source_dir / "bad.md").write_text("bad content", encoding="utf-8")

    original = shibei.importer.parse_file

    def fake_parse_file(path: Path) -> str:
        if path.name == "bad.md":
            raise ValueError("mock parser error")
        return original(path)

    monkeypatch.setattr(shibei.importer, "parse_file", fake_parse_file)

    cfg = _cfg(source_dir, db_path)
    progress_calls = []

    def progress(current: int, total: int) -> None:
        progress_calls.append((current, total))

    result = Importer(cfg).run(progress=progress)

    assert result["status"] == "ok"
    assert result["files"] == 2
    assert result["chunks"] == 1
    assert any("bad.md" in w and "mock parser error" in w for w in result.get("warnings", []))
    assert progress_calls == [(0, 2), (1, 2), (2, 2)]
