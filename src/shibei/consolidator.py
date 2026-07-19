"""知识库整理 — 相似 chunk 聚类合并。

目标：把知识库中内容高度重叠的 chunk 找出来，合并成精炼摘要，
减少重复内容占用的存储和检索噪音。

当前策略：
  1. 加载所有 chunks（BM25 FTS5）
  2. 计算两两之间的 token Overlap 系数
  3. 用阈值聚类（传递闭包）
  4. 输出相似 chunk 候选报告
  5. 可选：用启发式摘要直接 apply 合并（零 LLM 依赖）

未来可扩展：
  - 支持按 source 合并整篇文档
"""

import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .dreamer import load_chunks
from .tokenizer import tokenize_text

logger = logging.getLogger(__name__)


def _overlap_coefficient(a: set[str], b: set[str]) -> float:
    """计算两个 token 集合的 Overlap 系数：|A∩B| / min(|A|,|B|)。

    比 Jaccard 更适合检测"一段是另一段子集"的重复关系。
    """
    if not a or not b:
        return 0.0
    inter = a & b
    denom = min(len(a), len(b))
    return len(inter) / denom if denom > 0 else 0.0


def _build_similarity_pairs(chunks: list[dict], threshold: float = 0.6) -> list[tuple[int, int, float]]:
    """计算 chunk 两两相似度，返回超过阈值的边。"""
    tokens_list = []
    for c in chunks:
        text = c.get("text", "")
        toks = set(tokenize_text(text).split())
        tokens_list.append(toks)

    edges = []
    n = len(chunks)
    for i in range(n):
        for j in range(i + 1, n):
            score = _overlap_coefficient(tokens_list[i], tokens_list[j])
            if score >= threshold:
                edges.append((i, j, score))
    return edges


def _cluster_by_edges(n: int, edges: list[tuple[int, int, float]]) -> list[list[int]]:
    """基于传递闭包聚类。"""
    adj: dict[int, set[int]] = defaultdict(set)
    for i, j, _ in edges:
        adj[i].add(j)
        adj[j].add(i)

    visited = set()
    clusters = []

    for start in range(n):
        if start in visited:
            continue
        cluster = []
        stack = [start]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            cluster.append(node)
            for neighbor in adj[node]:
                if neighbor not in visited:
                    stack.append(neighbor)
        if cluster:
            clusters.append(cluster)

    return clusters


def _heuristic_summary(cluster_chunks: list[dict]) -> str:
    """无 LLM 启发式摘要：去重拼接 cluster 内各 chunk 文本。"""
    texts = []
    seen = set()
    for c in cluster_chunks:
        text = (c.get("text") or "").strip()
        if text and text not in seen:
            seen.add(text)
            texts.append(text)
    return "\n".join(texts)


def consolidate(
    db_path: Path,
    threshold: float = 0.6,
    min_cluster_size: int = 2,
    max_chunks: int = 500,
    apply: bool = False,
) -> dict:
    """整理知识库：发现相似 chunk 聚类（零 LLM 依赖）。

    Args:
        db_path: 数据存储根路径
        threshold: Overlap 相似度阈值（0~1），越高要求越严格
        min_cluster_size: 最小组大小，小于此值的孤立 chunk 不处理
        max_chunks: 最大处理 chunk 数
        apply: 是否直接用启发式摘要合并并写回数据库

    Returns:
        {
            "status": "ok" | "empty",
            "total_chunks": N,
            "cluster_count": N,
            "clusters": [...],
            "applied": {...}  # 仅 apply=True 且成功时存在
        }
    """
    chunks = load_chunks(db_path, max_chunks=max_chunks)
    if not chunks:
        return {"status": "empty", "message": "知识库为空"}

    edges = _build_similarity_pairs(chunks, threshold=threshold)
    clusters = _cluster_by_edges(len(chunks), edges)

    result_clusters = []
    clusters_to_apply = []

    for cluster in clusters:
        if len(cluster) < min_cluster_size:
            continue
        cluster_chunks = [chunks[i] for i in cluster]
        sources = sorted(set(c.get("source", "unknown") for c in cluster_chunks))
        summary = _heuristic_summary(cluster_chunks)

        result_clusters.append({
            "size": len(cluster),
            "sources": sources,
            "summary": summary,
            "chunks": [
                {
                    "_rowid": c.get("_rowid"),
                    "source": c.get("source", ""),
                    "text": c.get("text", "")[:200],
                }
                for c in cluster_chunks
            ],
        })

        if apply:
            clusters_to_apply.append((cluster_chunks, summary, sources))

    result = {
        "status": "ok",
        "total_chunks": len(chunks),
        "cluster_count": len(result_clusters),
        "threshold": threshold,
        "clusters": result_clusters,
    }

    if apply and clusters_to_apply:
        result["applied"] = _apply_clusters(db_path, clusters_to_apply)

    return result


def _apply_clusters(db_path: Path, clusters_to_apply: list) -> dict:
    """将合并摘要写回 BM25 索引，并删除原重叠 chunks。"""
    from .bm25 import BM25Index

    rowids_to_delete = []
    new_items = []
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")

    for idx, (cluster_chunks, summary, sources) in enumerate(clusters_to_apply, 1):
        valid_rowids = [
            c["_rowid"]
            for c in cluster_chunks
            if isinstance(c.get("_rowid"), int)
        ]
        if len(valid_rowids) != len(cluster_chunks):
            logger.warning("跳过无效集群的 apply")
            continue

        rowids_to_delete.extend(valid_rowids)
        source_name = sources[0] if sources else "unknown"
        new_source = f"__consolidated__/{timestamp}/{idx}#{Path(source_name).name}"
        new_items.append((
            new_source,
            new_source,
            "consolidated",
            summary,
            "",
            "",
            "",
        ))

    if not rowids_to_delete or not new_items:
        return {"applied": False, "removed": 0, "inserted": 0}

    try:
        bm25 = BM25Index(str(db_path / "fts5.db"))
        bm25.delete_by_rowids(rowids_to_delete)
        bm25.add_batch(new_items)
        return {
            "applied": True,
            "removed": len(rowids_to_delete),
            "inserted": len(new_items),
        }
    except Exception as e:
        logger.warning("应用合并结果失败: %s", e)
        return {"applied": False, "removed": 0, "inserted": 0, "error": str(e)}


def apply_consolidation(db_path: Path, clusters: list[dict]) -> dict:
    """根据外部 Agent 提供的 cluster 摘要直接应用合并。

    若 summary 为空，则自动使用启发式拼接摘要。

    Args:
        db_path: 数据存储根路径
        clusters: 每个元素为 {"rowids": [int, ...], "summary": str, "sources": [str, ...]}

    Returns:
        {"applied": bool, "removed": int, "inserted": int, "invalid_clusters": int}
    """
    from .bm25 import BM25Index

    rowids_to_delete = []
    new_items = []
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    invalid = 0

    for idx, cluster in enumerate(clusters, 1):
        rowids = cluster.get("rowids", [])
        summary = cluster.get("summary", "").strip()
        sources = cluster.get("sources", [])

        valid_rowids = [r for r in rowids if isinstance(r, int)]
        if not valid_rowids:
            invalid += 1
            continue

        # 未提供 summary 时自动生成
        if not summary:
            chunks_for_summary = [
                {"text": c} for c in cluster.get("chunks", [])
            ] if "chunks" in cluster else []
            summary = _heuristic_summary(chunks_for_summary)

        if not summary:
            invalid += 1
            continue

        rowids_to_delete.extend(valid_rowids)
        source_name = sources[0] if sources else "unknown"
        new_source = f"__consolidated__/{timestamp}/{idx}#{Path(source_name).name}"
        new_items.append((
            new_source,
            new_source,
            "consolidated",
            summary,
            "",
            "",
            "",
        ))

    if not rowids_to_delete or not new_items:
        return {
            "applied": False,
            "removed": 0,
            "inserted": 0,
            "invalid_clusters": invalid,
        }

    try:
        bm25 = BM25Index(str(db_path / "fts5.db"))
        bm25.delete_by_rowids(rowids_to_delete)
        bm25.add_batch(new_items)
        return {
            "applied": True,
            "removed": len(rowids_to_delete),
            "inserted": len(new_items),
            "invalid_clusters": invalid,
        }
    except Exception as e:
        logger.warning("应用外部合并结果失败: %s", e)
        return {
            "applied": False,
            "removed": 0,
            "inserted": 0,
            "invalid_clusters": invalid,
            "error": str(e),
        }
