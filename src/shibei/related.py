"""轻量关联发现 — 标签共现、Markdown 链接、关键词共现。

不依赖图数据库，基于已有 BM25 索引元数据计算关联。
"""

import re
from collections import Counter
from pathlib import Path
from typing import Optional

from .config import Config
from .tokenizer import tokenize_text


# Markdown 链接正则
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _extract_markdown_links(text: str) -> list[str]:
    """从文本中提取 wiki 链接和普通 markdown 链接的目标。"""
    links: list[str] = []
    for m in _WIKILINK_RE.finditer(text):
        links.append(m.group(1).strip())
    for m in _MD_LINK_RE.finditer(text):
        target = m.group(2).strip()
        # 过滤掉 URL，只保留相对路径或文件名
        if not target.startswith(("http://", "https://", "#")):
            links.append(target)
    return links


def _normalize_link(target: str, source_dir: Path) -> str:
    """把链接目标归一化为绝对路径字符串。"""
    target = target.split("#")[0].split("?")[0]
    if target.startswith("/"):
        return str(Path(target).resolve())
    return str((source_dir / target).resolve())


class RelatedFinder:
    """基于已有索引元数据发现相关文档。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def find_related(
        self,
        source: str,
        limit: int = 5,
    ) -> list[dict]:
        """查找与给定 source 相关的其他 source。

        返回列表按关联强度排序，元素格式：
        {"source": str, "score": float, "reason": str}
        """
        return self._find_related_bm25(source, limit)

    def _find_related_bm25(self, source: str, limit: int) -> list[dict]:
        import sqlite3
        from .bm25 import CONTENT_TABLE

        db_path = str(self.cfg.db_path_expanded / "fts5.db")
        with sqlite3.connect(db_path, timeout=30.0) as conn:
            # 1) 拉取目标 source 自身信息
            src_row = conn.execute(
                f"SELECT source, file_path, tags, content FROM {CONTENT_TABLE} WHERE source = ? LIMIT 1",
                (source,),
            ).fetchone()
            if not src_row:
                return []

            src_tags = src_row[2] or ""
            # 2) 通过 tags LIKE 找到可能相关的 source（避免大库全表扫描）
            tag_list = [t.strip() for t in src_tags.split(",") if t.strip()]
            related_sources: set[str] = set()
            for tag in tag_list:
                for row in conn.execute(
                    f"SELECT DISTINCT source FROM {CONTENT_TABLE} WHERE source != ? AND tags LIKE ?",
                    (source, f"%{tag}%"),
                ).fetchall():
                    related_sources.add(row[0])

            # 3) 只拉取相关 source 的 chunks；若 tag 关联为空，则回退到同目录少量样本
            if related_sources:
                placeholders = ",".join("?" * len(related_sources))
                rows = conn.execute(
                    f"SELECT source, file_path, tags, content FROM {CONTENT_TABLE} "
                    f"WHERE source IN ({placeholders}) LIMIT 5000",
                    tuple(related_sources),
                ).fetchall()
            else:
                source_dir = Path(src_row[1] or source).parent
                rows = conn.execute(
                    f"SELECT source, file_path, tags, content FROM {CONTENT_TABLE} "
                    f"WHERE file_path LIKE ? AND source != ? LIMIT 1000",
                    (f"{source_dir}%", source),
                ).fetchall()

        return self._build_relations(source, [src_row] + list(rows), limit)

    def _build_relations(
        self,
        source: str,
        rows: list[tuple],
        limit: int,
    ) -> list[dict]:
        """基于标签共现、Markdown 链接、关键词共现构建关联。"""
        # 按 source 聚合
        source_tags: dict[str, set[str]] = {}
        source_tokens: dict[str, Counter] = {}
        source_content: dict[str, str] = {}
        source_file: dict[str, str] = {}

        for src, file_path, tags, content in rows:
            if not src:
                continue
            source_file[src] = file_path or src
            if src not in source_tags:
                source_tags[src] = set()
                source_tokens[src] = Counter()
            if tags:
                source_tags[src].update(t.strip() for t in tags.split(",") if t.strip())
            if content:
                source_content[src] = (source_content.get(src, "") + "\n" + content).strip()
                source_tokens[src].update(tokenize_text(content).split())

        if source not in source_tags:
            return []

        related: dict[str, dict] = {}

        # 1. 标签共现
        src_tags = source_tags[source]
        for other, tags in source_tags.items():
            if other == source:
                continue
            common = src_tags & tags
            if common:
                score = len(common) / max(len(src_tags), len(tags), 1)
                related[other] = {
                    "source": other,
                    "score": score,
                    "reason": f"共享标签: {', '.join(sorted(common)[:3])}",
                }

        # 2. Markdown 链接
        src_dir = Path(source_file.get(source, source)).parent
        src_content = source_content.get(source, "")
        links = [_normalize_link(t, src_dir) for t in _extract_markdown_links(src_content)]
        link_targets = set(links)
        for other, file_path in source_file.items():
            if other == source:
                continue
            resolved = str(Path(file_path).resolve()) if file_path else other
            if resolved in link_targets:
                item = related.get(other, {"source": other, "score": 0.0, "reason": ""})
                item["score"] = min(item["score"] + 0.5, 1.0)
                item["reason"] = (item["reason"] + "; " if item["reason"] else "") + "文档内链接"
                related[other] = item

        # 3. 关键词共现（基于 BM25 tokens）
        src_tokens = source_tokens[source]
        for other, tokens in source_tokens.items():
            if other == source:
                continue
            if not tokens or not src_tokens:
                continue
            common_tokens = src_tokens & tokens
            if not common_tokens:
                continue
            # Jaccard
            score = sum(common_tokens.values()) / max(sum(src_tokens.values()), sum(tokens.values()), 1)
            if score <= 0:
                continue
            if other in related:
                related[other]["score"] = min(related[other]["score"] + score, 1.0)
                related[other]["reason"] += "; 关键词共现"
            else:
                top_words = [w for w, _ in common_tokens.most_common(3)]
                related[other] = {
                    "source": other,
                    "score": score,
                    "reason": f"关键词共现: {', '.join(top_words)}",
                }

        results = sorted(related.values(), key=lambda x: x["score"], reverse=True)[:limit]
        for r in results:
            r["score"] = round(r["score"], 3)
        return results


def find_related(cfg: Config, source: str, limit: int = 5) -> list[dict]:
    """便捷函数：查找与 source 相关的文档。"""
    return RelatedFinder(cfg).find_related(source, limit)
