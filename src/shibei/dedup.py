"""精确去重 — 基于内容 MD5 的 chunk 级去重。

存储位置: <db_path>/chunk_hashes.json
格式: {"content_md5": "source::file_path", ...}

设计：
  - 入库前去重：跳过内容完全相同的 chunk
  - 跨文件去重：同一内容只保留首次入库的 source
  - 增量维护：导入时追加，删除文件时清理对应 hash
"""

import hashlib
import json
from pathlib import Path
from typing import Iterable


# chunk tuple 结构（兼容 4/6/7-tuple）:
# 4-tuple: (source, file_path, tags, content)
# 6-tuple: (source, file_path, tags, content, parent_text, parent_id)
# 7-tuple: (..., summary)
# 去重只看 content（index 3），三种结构都支持
ChunkTuple = tuple


def content_hash(content: str) -> str:
    """计算 chunk 内容的 MD5（归一化：去除首尾空白）。"""
    normalized = content.strip()
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


class ChunkHashStore:
    """已入库 chunk hash 的持久化存储。

    与 manifest.json 不同：manifest 跟踪文件级变更，
    chunk_hashes 跟踪 chunk 级去重。
    """

    def __init__(self, db_path: Path):
        self.path = db_path / "chunk_hashes.json"
        self._data: dict[str, str] = {}  # hash -> source
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path) as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._data, f, ensure_ascii=False)

    def has(self, h: str) -> bool:
        return h in self._data

    def add(self, h: str, source: str):
        self._data[h] = source

    def remove_by_source(self, source: str) -> int:
        """删除指定 source 关联的所有 hash，返回删除数。"""
        to_drop = [h for h, s in self._data.items() if s == source]
        for h in to_drop:
            del self._data[h]
        return len(to_drop)

    def all_hashes(self) -> set[str]:
        return set(self._data.keys())


def dedup_chunks(
    chunks: list[ChunkTuple],
    existing_hashes: Iterable[str],
) -> tuple[list[ChunkTuple], int, set[str]]:
    """对 chunks 列表做精确去重。

    Args:
        chunks: 待入库的 [(source, file_path, tags, content), ...]
        existing_hashes: 已入库的内容 hash 集合

    Returns:
        (unique_chunks, skipped_count, new_hashes)
        - unique_chunks: 去重后的 chunks（保留首次出现）
        - skipped_count: 被跳过的重复 chunk 数
        - new_hashes: 本次新增的 hash 集合（用于持久化）
    """
    seen = set(existing_hashes)
    new_hashes: set[str] = set()
    result: list[ChunkTuple] = []

    for chunk in chunks:
        h = content_hash(chunk[3])
        if h in seen:
            continue
        seen.add(h)
        new_hashes.add(h)
        result.append(chunk)

    skipped = len(chunks) - len(result)
    return result, skipped, new_hashes
