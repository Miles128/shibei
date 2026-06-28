"""文件变更追踪 — 基于 MD5 hash 的增量导入支持。"""

import hashlib
import json
from pathlib import Path


class Manifest:
    """追踪已导入文件的 hash，用于增量更新。

    存储位置: <db_path>/manifest.json
    格式: {"file_path": "md5_hash", ...}
    """

    def __init__(self, db_path: Path):
        self.path = db_path / "manifest.json"
        self._data: dict[str, str] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            with open(self.path) as f:
                self._data = json.load(f)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def get_hash(self, file_path: str) -> 'str | None':
        return self._data.get(file_path)

    def set_hash(self, file_path: str, file_hash: str):
        self._data[file_path] = file_hash

    def remove(self, file_path: str):
        self._data.pop(file_path, None)

    def all_paths(self) -> set[str]:
        return set(self._data.keys())

    @staticmethod
    def file_hash(path: Path) -> str:
        """计算文件内容 MD5。"""
        return hashlib.md5(path.read_bytes()).hexdigest()

    def classify(
        self, current_files: dict[str, Path]
    ) -> tuple[list[Path], list[Path], list[str]]:
        """对比当前文件列表与 manifest，分类为 (新增, 变更, 删除)。

        Args:
            current_files: {file_path_str: Path} 当前扫描到的文件

        Returns:
            (to_add, to_update, to_remove)
        """
        current_paths = set(current_files.keys())
        known_paths = self.all_paths()

        to_add = []
        to_update = []
        to_remove = []

        for fp, p in current_files.items():
            old_hash = self.get_hash(fp)
            new_hash = self.file_hash(p)
            if old_hash is None:
                to_add.append(p)
            elif old_hash != new_hash:
                to_update.append(p)
            # else: unchanged, skip

        for fp in known_paths - current_paths:
            to_remove.append(fp)

        return to_add, to_update, to_remove
