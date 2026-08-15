"""文档导入器 — 扫描目录 → 分块 → 去重 → 存储到 BM25

支持增量导入：通过 manifest 追踪文件 hash，跳过未变更文件。
支持精确去重：通过 chunk_hashes 持久化，跳过内容完全相同的 chunk。
支持 parent-child：小 chunk 索引 + parent 上下文（config.chunking.parent_window > 0）。
支持格式：md, txt, docx, xlsx, csv（配置化扩展名）。
"""

import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import Config
from .tagger import Tagger
from .manifest import Manifest
from .parsers import parse_file
from .dedup import ChunkHashStore, content_hash, dedup_chunks
from .chunker import chunk_text, ChildChunk
from .summarizer import ChunkSummarizer

logger = logging.getLogger(__name__)


def _path_under_ignored_dir(path: Path, ignore_dirs: set[str]) -> bool:
    return any(part in ignore_dirs for part in path.parts)


class Importer:
    """扫描文档文件，分块，存入 BM25 FTS5。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tagger = Tagger(cfg.tagging)
        self.summarizer = ChunkSummarizer(cfg.llm)

    def run(self, dirs: 'list[str] | None' = None, full: bool = False,
            file_pattern: 'str | None' = None,
            progress: 'Callable[[int, int], None] | None' = None) -> dict:
        """导入文档。

        Args:
            dirs: 要扫描的目录。None 则用 config 中的 sources。
            full: True 全量重建，False 增量导入（默认）。
            file_pattern: 文件名 glob 过滤（如 'README*'），None 不过滤。
            progress: 进度回调，参数为 (current, total)，按文件触发。

        Returns:
            {"files": N, "chunks": N, "skipped": N, "removed": N,
             "deduped": N, "status": "ok"}
        """
        import fnmatch
        scan_dirs = dirs or self.cfg.sources
        extensions = set(self.cfg.extensions)
        ignore_dirs = {str(item).strip() for item in self.cfg.ignore_dirs if str(item).strip()}
        files: list[Path] = []
        warnings: list[str] = []
        for d in scan_dirs:
            p = Path(d).expanduser()
            if p.exists():
                for ext in extensions:
                    candidates: list[Path] = list(p.rglob(f"*{ext}"))
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
            to_update: list[Path] = []
            to_remove = list(manifest.all_paths() - set(current_files.keys()))
        else:
            to_add, to_update, to_remove = manifest.classify(current_files)

        changed = to_add + to_update
        skipped = len(current_files) - len(changed)

        # 并行解析文件（IO 密集型），再串行分块 + 标签
        # chunks_data 结构：(source, file_path, tags, content, parent_text, parent_id, summary)
        # parent_text/parent_id 在 parent_window=0 时为空字符串
        # summary 默认空字符串，LLM 启用且调用成功时填充
        chunks_data: list[tuple[str, str, str, str, str, str, str]] = []
        ck = self.cfg.chunking

        def _parse_one(path: Path) -> tuple[Path, str] | tuple[Path, Exception]:
            try:
                return path, parse_file(path)
            except (ValueError, OSError, UnicodeDecodeError) as e:
                return path, e
            except Exception as e:  # 自定义 parser 异常不中断整批导入
                logger.exception("解析文件时发生未预期异常: %s", path)
                return path, e

        parsed_results: list[tuple[Path, str] | tuple[Path, Exception]] = []
        if changed:
            # IO 密集型：默认线程数参考 concurrent.futures 推荐值
            max_workers = min(32, (os.cpu_count() or 1) + 4)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                parsed_results = list(executor.map(_parse_one, changed))

        if progress is not None:
            progress(0, len(changed))

        for idx, (f, result) in enumerate(parsed_results, start=1):
            if isinstance(result, Exception):
                warnings.append(f"文件解析失败: {f} ({result})")
                logger.warning("文件解析失败: %s (%s)", f, result)
                if progress is not None:
                    progress(idx, len(changed))
                continue

            content = result
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
                child_chunks: list[ChildChunk] = chunks  # type: ignore[assignment]
                for cc in child_chunks:
                    chunks_data.append(
                        (slug, str(f), ",".join(tags),
                         cc.text, cc.parent_text, cc.parent_id, "")
                    )
            else:
                # 普通模式
                plain_chunks: list[str] = chunks  # type: ignore[assignment]
                for chunk_text_str in plain_chunks:
                    chunks_data.append(
                        (slug, str(f), ",".join(tags), chunk_text_str, "", "", "")
                    )

            if progress is not None:
                progress(idx, len(changed))

        # 为每个 chunk 生成一句话中文摘要（LLM 失败时降级为空字符串）
        # chunks_data 元组已是 7 字段：(source, file_path, tags, content, parent_text, parent_id, summary_placeholder)
        # 此处把占位的第 7 位替换为真正的 summarize 结果
        chunks_data = [
            (source, file_path, tags, content, parent_text, parent_id,
             self.summarizer.summarize(content))
            for source, file_path, tags, content, parent_text, parent_id, _placeholder in chunks_data
        ]

        # 精确去重：跳过内容完全相同的 chunk
        hash_store = ChunkHashStore(self.cfg.db_path_expanded)
        if full:
            # full 模式：清空全部 hash，从零重建（否则旧 hash 会让存量 chunk 被跳过，
            # chunking 配置变更后新旧 chunk 会在索引中重复）
            hash_store.clear()
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
        for source, file_path, tags, content, _parent_text, _parent_id, _summary in unique_chunks:
            h = content_hash(content)
            if not hash_store.has(h):
                hash_store.add(h, source)

        chunks_data = unique_chunks

        # 需要清理旧 chunk 的 source：已删除文件 + 内容变更文件
        sources_to_clear = list(to_remove) + [str(f) for f in to_update]
        self._import_bm25(chunks_data, sources_to_clear, full=full)

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
            "removed": len(sources_to_clear),
            "deduped": deduped,
            "status": "ok",
        }
        if warnings:
            result["warnings"] = warnings
        return result

    def _import_bm25(self, chunks_data: list, sources_to_clear: list[str],
                     full: bool = False):
        """把分块写入 BM25 索引，并删除已移除/变更文件的旧 chunk。

        full=True 时直接清空整个索引再重建（真正的全量重建），
        而不是只删 to_remove 文件——否则存量 chunk 会残留、与新导入重复。
        """
        from .bm25 import BM25Index

        db_path = self.cfg.db_path_expanded / "fts5.db"
        bm25 = BM25Index(str(db_path))

        if full:
            bm25.clear()
        elif sources_to_clear:
            # 批量删除旧 chunk（整传入 list，避免按字符拆分）
            bm25.remove_sources(sources_to_clear)

        if chunks_data:
            bm25.add_batch(chunks_data)


def clean_orphans(
    cfg: Config,
    dirs: 'list[str] | None' = None,
    dry_run: bool = False,
) -> dict:
    """清理源文件已删除但数据库中仍残留的 orphan chunks。

    Args:
        cfg: 配置
        dirs: 要扫描的目录。None 则用 config 中的 sources。
        dry_run: True 只报告不删除

    Returns:
        {"orphans": [file_path, ...], "removed": int, "dry_run": bool}
    """
    scan_dirs = dirs or cfg.sources
    extensions = set(cfg.extensions)
    ignore_dirs = {str(item).strip() for item in cfg.ignore_dirs if str(item).strip()}

    files: list[Path] = []
    for d in scan_dirs:
        p = Path(d).expanduser()
        if p.exists():
            for ext in extensions:
                candidates: list[Path] = list(p.rglob(f"*{ext}"))
                files.extend(
                    f for f in candidates if not _path_under_ignored_dir(f, ignore_dirs)
                )

    current_files = {str(f) for f in files}
    manifest = Manifest(cfg.db_path_expanded)
    known_paths = manifest.all_paths()
    orphans = sorted(known_paths - current_files)

    if not orphans:
        return {"orphans": [], "removed": 0, "dry_run": dry_run}

    if not dry_run:
        from .bm25 import BM25Index
        db_path = cfg.db_path_expanded / "fts5.db"
        bm25 = BM25Index(str(db_path))
        bm25.remove_sources(orphans)
        for fp in orphans:
            manifest.remove(fp)
        manifest.save()

    return {"orphans": orphans, "removed": len(orphans), "dry_run": dry_run}


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
