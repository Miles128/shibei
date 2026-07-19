"""shibei clean 命令测试"""

import tempfile
from pathlib import Path

from shibei.config import Config, StorageConfig
from shibei.importer import clean_orphans
from shibei.bm25 import BM25Index
from shibei.manifest import Manifest


def _cfg(db_path: Path, sources: list[str]) -> Config:
    return Config(
        storage=StorageConfig(path=str(db_path)),
        sources=sources,
    )


def test_clean_orphans_dry_run():
    """dry-run 只报告不删除"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        source_dir = db_path / "notes"
        source_dir.mkdir()
        old_path = str(source_dir / "old.md")

        cfg = _cfg(db_path, [str(source_dir)])
        bm25 = BM25Index(str(db_path / "fts5.db"))
        bm25.add_batch([
            (old_path, old_path, "ai", "old content", "", ""),
        ])
        manifest = Manifest(db_path)
        manifest.set_hash(old_path, "hash1")
        manifest.save()

        # 文件不存在，dry-run
        result = clean_orphans(cfg, dry_run=True)
        assert result["removed"] == 1
        assert old_path in result["orphans"]
        assert result["dry_run"] is True

        # 数据库还在
        assert bm25.count() == 1


def test_clean_orphans_removes():
    """真正删除 orphan chunks"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        source_dir = db_path / "notes"
        source_dir.mkdir()
        old_path = str(source_dir / "old.md")
        keep_path = str(source_dir / "keep.md")

        cfg = _cfg(db_path, [str(source_dir)])
        bm25 = BM25Index(str(db_path / "fts5.db"))
        bm25.add_batch([
            (old_path, old_path, "ai", "old content", "", ""),
            (keep_path, keep_path, "ai", "keep content", "", ""),
        ])
        manifest = Manifest(db_path)
        manifest.set_hash(old_path, "hash1")
        manifest.set_hash(keep_path, "hash2")
        manifest.save()

        (source_dir / "keep.md").write_text("keep", encoding="utf-8")

        result = clean_orphans(cfg, dry_run=False)
        assert result["removed"] == 1
        assert result["orphans"] == [old_path]
        assert bm25.count() == 1

        manifest = Manifest(db_path)
        assert old_path not in manifest.all_paths()
        assert keep_path in manifest.all_paths()


def test_clean_orphans_no_orphans():
    """没有 orphan 时返回空"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        source_dir = db_path / "notes"
        source_dir.mkdir()
        a_path = str(source_dir / "a.md")
        (source_dir / "a.md").write_text("a", encoding="utf-8")

        cfg = _cfg(db_path, [str(source_dir)])
        bm25 = BM25Index(str(db_path / "fts5.db"))
        bm25.add_batch([
            (a_path, a_path, "ai", "a", "", ""),
        ])
        manifest = Manifest(db_path)
        manifest.set_hash(a_path, "hash")
        manifest.save()

        result = clean_orphans(cfg)
        assert result["removed"] == 0
        assert result["orphans"] == []
