"""文档导入器 — 扫描目录 → 分块 → 去重 → 存储到 BM25

支持增量导入：通过 manifest 追踪文件 hash，跳过未变更文件。
支持精确去重：通过 chunk_hashes 持久化，跳过内容完全相同的 chunk。
支持 parent-child：小 chunk 索引 + parent 上下文（config.chunking.parent_window > 0）。
支持格式：md, txt, docx, xlsx, csv（配置化扩展名）。
"""

import logging
from pathlib import Path

from .config import Config
from .tagger import Tagger
from .manifest import Manifest
from .parsers import parse_file
from .dedup import ChunkHashStore, content_hash, dedup_chunks
from .chunker import chunk_text, ChildChunk

logger = logging.getLogger(__name__)


def _path_under_ignored_dir(path: Path, ignore_dirs: set[str]) -> bool:
    return any(part in ignore_dirs for part in path.parts)


class Importer:
    """扫描文档文件，分块，存入 BM25 FTS5。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tagger = Tagger(cfg.tagging)

    def run(self, dirs: 'list[str] | None' = None, full: bool = False,
            file_pattern: 'str | None' = None) -> dict:
        """导入文档。

        Args:
            dirs: 要扫描的目录。None 则用 config 中的 sources。
            full: True 全量重建，False 增量导入（默认）。
            file_pattern: 文件名 glob 过滤（如 'README*'），None 不过滤。

        Returns:
            {"files": N, "chunks": N, "skipped": N, "removed": N,
             "deduped": N, "status": "ok"}
        """
        import fnmatch
        scan_dirs = dirs or self.cfg.sources
        extensions = set(self.cfg.extensions)
        ignore_dirs = {str(item).strip() for item in self.cfg.ignore_dirs if str(item).strip()}
        files = []
        warnings: list[str] = []
        for d in scan_dirs:
            p = Path(d).expanduser()
            if p.exists():
                for ext in extensions:
                    candidates = p.rglob(f"*{ext}")
                    if file_pattern:
                        candidates = [f for f in candidates
                                      if fnmatch.fnmatch(f.name, file_pattern)]
                    files.extend(
                        f for f in candidates if not _path_under_ignored_dir(f, ignore_dirs)
                    )
            else:
                warnings.append(f"目录不存在: {p}")

        current_files = {str(f): f for f in sorted(files)}
        manifest = Manifest(self.cfg.db_path_expanded)

        if full:
            to_add = list(current_files.values())
            to_update = []
            to_remove = list(manifest.all_paths() - set(current_files.keys()))
        else:
            to_add, to_update, to_remove = manifest.classify(current_files)

        changed = to_add + to_update
        skipped = len(current_files) - len(changed)

        # 分块 + 标签（仅处理变更文件）
        # chunks_data 结构：(source, file_path, tags, content, parent_text, parent_id)
        # parent_text/parent_id 在 parent_window=0 时为空字符串
        chunks_data = []
        ck = self.cfg.chunking
        for f in changed:
            try:
                content = parse_file(f)
            except (ValueError, OSError, UnicodeDecodeError) as e:
                warnings.append(f"文件解析失败: {f} ({e})")
                logger.warning("文件解析失败: %s (%s)", f, e)
                continue
            tags = self.tagger.infer(f, content)
            slug = str(f)
            chunks = chunk_text(
                content,
                max_chars=ck.max_chars,
                split_pattern=ck.split_pattern,
                overlap=ck.overlap,
                sentence_boundaries=ck.sentence_boundaries,
                parent_window=ck.parent_window,
                source=slug,
            )
            if ck.parent_window > 0 and chunks and isinstance(chunks[0], ChildChunk):
                # parent-child 模式
                for cc in chunks:
                    chunks_data.append(
                        (slug, str(f), ",".join(tags),
                         cc.text, cc.parent_text, cc.parent_id)
                    )
            else:
                # 普通模式
                for chunk_text_str in chunks:
                    chunks_data.append(
                        (slug, str(f), ",".join(tags), chunk_text_str, "", "")
                    )

        # 精确去重：跳过内容完全相同的 chunk
        hash_store = ChunkHashStore(self.cfg.db_path_expanded)
        # full 模式下清空 hash store，重新建立
        if full:
            for fp in to_remove:
                hash_store.remove_by_source(fp)
        else:
            # 删除已移除文件的旧 hash
            for fp in to_remove:
                hash_store.remove_by_source(fp)
            # 变更文件的旧 hash 也要清（重新导入）
            for f in to_update:
                hash_store.remove_by_source(str(f))

        unique_chunks, deduped, new_hashes = dedup_chunks(
            chunks_data, hash_store.all_hashes()
        )
        # 记录新增 hash 到 store（首次出现的 source）
        for source, file_path, tags, content, _parent_text, _parent_id in unique_chunks:
            h = content_hash(content)
            if not hash_store.has(h):
                hash_store.add(h, source)

        chunks_data = unique_chunks

        self._import_bm25(chunks_data, to_remove)

        # manifest 统一更新一次
        for fp in to_remove:
            manifest.remove(fp)
        for fp in current_files:
            manifest.set_hash(fp, Manifest.file_hash(current_files[fp]))
        manifest.save()

        # 持久化 chunk hash store
        hash_store.save()

        result = {
            "files": len(changed),
            "chunks": len(chunks_data),
            "skipped": skipped,
            "removed": len(to_remove),
            "deduped": deduped,
            "status": "ok",
        }
        if warnings:
            result["warnings"] = warnings
        return result

    def _import_bm25(self, chunks_data: list, to_remove: list[str]):
        """把分块写入 BM25 索引，并删除已移除文件的旧 chunk。"""
        from .bm25 import BM25Index

        db_path = self.cfg.db_path_expanded / "fts5.db"
        bm25 = BM25Index(str(db_path))

        # 删除已移除/变更文件的旧 chunk
        for fp in to_remove:
            bm25.remove_by_source(fp)
        # 变更文件会在 remove_by_source 中删除旧 chunk，然后下面 add_batch 写入新 chunk

        if chunks_data:
            bm25.add_batch(chunks_data)


def list_sources(config: Config, limit: int = 0, offset: int = 0) -> dict:
    """列出已入库的文件来源。

    Returns:
        {
            "count": 总数,
            "sources": [...],
            "summary": {
                "total_chunks": chunk 总数,
                "total_files": 文件总数,
                "by_tag": {tag: count}
            },
            "has_more": bool
        }
    """
    from .bm25 import BM25Index

    db_path = config.db_path_expanded / "fts5.db"
    bm25 = BM25Index(str(db_path))

    all_meta = bm25.list_sources_with_meta()
    total = len(all_meta)

    chunks_total = bm25.count()

    by_tag: dict[str, int] = {}
    for info in all_meta:
        for tag in (info.get("tags") or "").split(","):
            tag = tag.strip()
            if tag:
                by_tag[tag] = by_tag.get(tag, 0) + 1

    if offset:
        all_meta = all_meta[offset:]
    if limit:
        all_meta = all_meta[:limit]

    sources = [info["source"] for info in all_meta]
    has_more = (offset + limit) < total if limit else False

    return {
        "count": total,
        "sources": sources,
        "summary": {
            "total_chunks": chunks_total,
            "total_files": total,
            "by_tag": by_tag,
        },
        "has_more": has_more,
    }
