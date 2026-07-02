"""知识库整理 — 相似 chunk 聚类合并。

目标：把知识库中内容高度重叠的 chunk 找出来，合并成精炼摘要，
减少重复内容占用的存储和检索噪音。

当前策略：
  1. 加载所有 chunks（BM25 FTS5）
  2. 计算两两之间的 token Overlap 系数
  3. 用阈值聚类（传递闭包）
  4. 对每个 cluster 用 LLM 生成合并摘要
  5. 输出合并报告（不自动写回原库，避免误删）

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

    比 Jaccard 更适合检测“一段是另一段子集”的重复关系。
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


def _merge_cluster_with_llm(cluster_chunks: list[dict], llm_client) -> str:
    """用 LLM 把一组相似 chunk 合并成精炼摘要。"""
    sources = []
    texts = []
    for c in cluster_chunks:
        src = c.get("source", "unknown")
        text = c.get("text", "").strip()
        if text:
            sources.append(Path(src).name)
            texts.append(text)

    combined = "\n\n---\n\n".join(texts)
    prompt = f"""以下是知识库中若干条高度相似的内容片段，可能来自不同文档。
请把它们合并整理成一段精炼的中文摘要，去除重复表述，保留核心信息。
不要添加原文中没有的信息，不要输出解释。

来源：{', '.join(sorted(set(sources)))}

内容：
{combined}

请输出合并后的摘要："""

    return llm_client.summarize(combined, max_length=300)


def consolidate(
    db_path: Path,
    threshold: float = 0.6,
    min_cluster_size: int = 2,
    max_chunks: int = 500,
    llm_client=None,
    apply: bool = False,
) -> dict:
    """整理知识库：发现相似 chunk 并生成合并摘要；可选 apply 写回数据库。

    Args:
        db_path: 数据存储根路径
        threshold: Overlap 相似度阈值（0~1），越高要求越严格
        min_cluster_size: 最小组大小，小于此值的孤立 chunk 不处理
        max_chunks: 最大处理 chunk 数
        llm_client: 用于生成合并摘要；未提供时只返回聚类结果
        apply: 是否把合并摘要写回知识库并删除原重叠 chunks

    Returns:
        {
            "status": "ok" | "empty",
            "total_chunks": N,
            "applied": False,
            "removed": 0,
            "inserted": 0,
            "clusters": [...]
        }
    """
    chunks = load_chunks(db_path, max_chunks=max_chunks)
    if not chunks:
        return {"status": "empty", "message": "知识库为空"}

    edges = _build_similarity_pairs(chunks, threshold=threshold)
    clusters = _cluster_by_edges(len(chunks), edges)

    result_clusters = []
    applied = False
    removed = 0
    inserted = 0

    # apply 模式下需要操作的集群列表
    clusters_to_apply = []

    for cluster in clusters:
        if len(cluster) < min_cluster_size:
            continue
        cluster_chunks = [chunks[i] for i in cluster]
        sources = sorted(set(c.get("source", "unknown") for c in cluster_chunks))

        summary = ""
        if llm_client and getattr(llm_client, "ready", False):
            try:
                summary = _merge_cluster_with_llm(cluster_chunks, llm_client)
            except Exception as e:
                logger.warning("合并摘要生成失败: %s", e)

        result_clusters.append({
            "size": len(cluster),
            "sources": sources,
            "summary": summary,
            "chunks": [
                {
                    "source": c.get("source", ""),
                    "text": c.get("text", "")[:200],
                }
                for c in cluster_chunks
            ],
        })

        if summary and apply:
            clusters_to_apply.append((cluster_chunks, summary, sources))

    if apply and clusters_to_apply:
        applied_rows = _apply_clusters(db_path, clusters_to_apply)
        applied = applied_rows["applied"]
        removed = applied_rows["removed"]
        inserted = applied_rows["inserted"]

    return {
        "status": "ok",
        "total_chunks": len(chunks),
        "cluster_count": len(result_clusters),
        "threshold": threshold,
        "applied": applied,
        "removed": removed,
        "inserted": inserted,
        "clusters": result_clusters,
    }


def _apply_clusters(db_path: Path, clusters_to_apply: list) -> dict:
    """将合并摘要写回 BM25 索引，并删除原重叠 chunks。"""
    from .bm25 import BM25Index

    rowids_to_delete = []
    new_items = []
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")

    for idx, (cluster_chunks, summary, sources) in enumerate(clusters_to_apply, 1):
        # 仅支持 int rowid（BM25）
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
            new_source,           # source
            new_source,           # file_path
            "consolidated",       # tags
            summary,              # content
            "",                   # parent_text
            "",                   # parent_id
            "",                   # summary
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
