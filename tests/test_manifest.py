"""Manifest 增量追踪测试"""

import tempfile
from pathlib import Path

from shibei.manifest import Manifest


def _make_manifest(tmpdir):
    db_path = Path(tmpdir)
    db_path.mkdir(parents=True, exist_ok=True)
    return Manifest(db_path)


def test_manifest_classify_new_files():
    """新文件识别为 to_add"""
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest = _make_manifest(Path(tmpdir))

        # 创建测试文件
        f1 = Path(tmpdir) / "note1.md"
        f1.write_text("content1")

        current = {str(f1): f1}
        to_add, to_update, to_remove = manifest.classify(current)

        assert len(to_add) == 1
        assert to_add[0] == f1
        assert len(to_update) == 0
        assert len(to_remove) == 0


def test_manifest_classify_unchanged():
    """未变更文件被跳过"""
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest = _make_manifest(Path(tmpdir))

        f1 = Path(tmpdir) / "note1.md"
        f1.write_text("content1")

        # 首次导入
        manifest.set_hash(str(f1), Manifest.file_hash(f1))
        manifest.save()

        # 再次扫描，内容未变
        current = {str(f1): f1}
        to_add, to_update, to_remove = manifest.classify(current)

        assert len(to_add) == 0
        assert len(to_update) == 0
        assert len(to_remove) == 0


def test_manifest_classify_changed():
    """内容变更识别为 to_update"""
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest = _make_manifest(Path(tmpdir))

        f1 = Path(tmpdir) / "note1.md"
        f1.write_text("content1")

        manifest.set_hash(str(f1), Manifest.file_hash(f1))
        manifest.save()

        # 修改内容
        f1.write_text("content1_modified")
        current = {str(f1): f1}
        to_add, to_update, to_remove = manifest.classify(current)

        assert len(to_add) == 0
        assert len(to_update) == 1
        assert to_update[0] == f1
        assert len(to_remove) == 0


def test_manifest_classify_removed():
    """已删除文件识别为 to_remove"""
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest = _make_manifest(Path(tmpdir))

        f1 = Path(tmpdir) / "note1.md"
        f1.write_text("content1")
        manifest.set_hash(str(f1), Manifest.file_hash(f1))
        manifest.save()

        # 文件被删除，current 为空
        to_add, to_update, to_remove = manifest.classify({})

        assert len(to_add) == 0
        assert len(to_update) == 0
        assert len(to_remove) == 1
        assert to_remove[0] == str(f1)


def test_manifest_persistence():
    """manifest 持久化存储"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        f1 = db_path / "note1.md"
        f1.write_text("content1")

        # 写入
        manifest = Manifest(db_path)
        manifest.set_hash(str(f1), Manifest.file_hash(f1))
        manifest.save()

        # 重新加载
        manifest2 = Manifest(db_path)
        assert manifest2.get_hash(str(f1)) == Manifest.file_hash(f1)
