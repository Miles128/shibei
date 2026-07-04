"""Dreaming 模块 — 知识库自主思考，发现隐藏连接与洞察。

核心思路：
  1. 扫描所有 chunks，提取关键词
  2. 按关键词聚类，找到主题关联
  3. 跨文档交叉引用
  4. 生成洞察报告
  5. 持久化 dream 结果，支持增量对比

数据源：
  - BM25 (FTS5 SQLite)：从 chunks 表读取
"""

import re
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from collections import Counter, defaultdict
from typing import Optional

from .tokenizer import is_noise_token

logger = logging.getLogger(__name__)


# 整理（housekeeping）默认配置。
# dreaming 只做零 LLM 依赖的整理：清理过期记忆、识别可压缩记忆候选、
# 发现相似 chunk 候选。具体压缩/合并由外部 Agent 自行判断并调用
# shibei_update_memory / shibei_apply_consolidation 执行。
DEFAULT_HOUSEKEEPING = {
    "cleanup_expired_memories": True,
    "compress_max_age_days": 30,
    "compress_keep_recent": 10,
    "consolidate_threshold": 0.6,
    "consolidate_min_cluster_size": 2,
    "consolidate_max_chunks": 500,
}


# 停用词（中文高频无意义词 + 英文常见词）
STOP_WORDS = set("的了是在我有和人这中大为上个国不一到说时要就出会也得里后自"
                 "对着过家学下可她那些产二十用主行方又如前所本见经头面起把好"
                 "将与很想能开已但从而其做之比较没什么可以因为所以如果虽然只是"
                 "或者以及然后可能已经应该不是大家比较一些这个那个为什么什么"
                 # 英文停用词
                 "the to is in of and for on at by an a or it as be are was were "
                 "has have had with from this that not but all can will do if my no "
                 "so up out one about how its us him he she we them than who what "
                 "https http com www org html php aspx".split())


def extract_keywords(text: str, top_n: int = 8) -> list[str]:
    """从文本中提取关键词（中文优先，过滤 Markdown 格式噪音）。

    优先使用 jieba（若安装），降级到正则 bigram。
    """
    # 尝试 jieba（可选依赖）
    try:
        import jieba.analyse
        kws = jieba.analyse.extract_tags(text, topK=top_n)
        kws = [w for w in kws if not is_noise_token(w)]
        if kws:
            return kws[:top_n]
    except ImportError:
        pass

    # 降级：正则提取
    # 提取中文词（2-6字）
    cn_words = re.findall(r'[\u4e00-\u9fff]{2,6}', text)
    # 提取英文词（3+ 字母，过滤常见技术噪音词）
    en_words = [w for w in re.findall(r'[A-Za-z]{3,}', text)
                if w.lower() not in STOP_WORDS and not w.isupper() and not is_noise_token(w)]

    # 中文词加权（×2），英文词权重=1
    words = []
    for w in cn_words:
        if w not in STOP_WORDS and len(w) >= 2 and not is_noise_token(w):
            words.append(w)
            words.append(w)  # 中文词权重 ×2
    for w in en_words:
        words.append(w.lower())

    counter = Counter(words)
    return [word for word, _ in counter.most_common(top_n)]


def load_chunks_from_bm25(db_path: Path, max_chunks: int = 500) -> list[dict]:
    """从 BM25 FTS5 索引加载 chunks（采样，避免超大库全量加载）。"""
    fts_db = db_path / "fts5.db"
    if not fts_db.exists():
        return []

    with sqlite3.connect(str(fts_db), timeout=30.0) as conn:
        total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        # 采样：均匀取 max_chunks 条
        if total > max_chunks:
            step = total // max_chunks
            rows = conn.execute(
                f"SELECT rowid, source, file_path, tags, content FROM chunks "
                f"WHERE rowid IN (SELECT rowid FROM chunks WHERE rowid % {step} = 0 LIMIT {max_chunks})"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT rowid, source, file_path, tags, content FROM chunks"
            ).fetchall()

    chunks = []
    for rowid, source, file_path, tags, content in rows:
        chunks.append({
            "_rowid": rowid,
            "source": source,
            "file_path": file_path,
            "tags": tags,
            "text": content[:500],
            "keywords": extract_keywords(content),
        })
    return chunks


def load_chunks(db_path: Path, max_chunks: int = 500) -> list[dict]:
    """从 BM25 FTS5 索引加载 chunks（采样，避免超大库全量加载）。

    Args:
        db_path: 数据存储根路径（含 fts5.db）
        max_chunks: 最大加载条数
    """
    return load_chunks_from_bm25(db_path, max_chunks)


def build_topic_graph(chunks: list[dict]) -> dict[str, list[str]]:
    """构建主题图：关键词 → 相关 chunk sources。"""
    keyword_docs: dict[str, set[str]] = defaultdict(set)
    for chunk in chunks:
        for kw in chunk["keywords"]:
            keyword_docs[kw].add(chunk["source"])

    # 只保留连接 2+ 文档的关键词
    return {kw: list(docs) for kw, docs in keyword_docs.items() if len(docs) >= 2}


def find_cross_references(chunks: list[dict]) -> list[dict]:
    """找到跨文档引用（不同文件共享关键词）。"""
    topic_graph = build_topic_graph(chunks)

    # 按文档对聚合连接强度（跳过同名文件）
    connections: dict[tuple, dict] = defaultdict(lambda: {"keywords": [], "count": 0})
    for kw, sources in topic_graph.items():
        for i in range(len(sources)):
            for j in range(i + 1, len(sources)):
                name_a = Path(sources[i]).name.lower()
                name_b = Path(sources[j]).name.lower()
                # 跳过同名文件（如 readme.md 连 readme.md）
                if name_a == name_b:
                    continue
                key = tuple(sorted([sources[i], sources[j]]))
                connections[key]["keywords"].append(kw)
                connections[key]["count"] += 1

    # 按连接强度排序
    results = []
    for (src_a, src_b), info in sorted(
        connections.items(), key=lambda x: x[1]["count"], reverse=True
    ):
        results.append({
            "doc_a": Path(src_a).name,
            "doc_b": Path(src_b).name,
            "shared_keywords": info["keywords"][:8],
            "strength": info["count"],
        })
    return results


def find_patterns(chunks: list[dict]) -> dict[str, list[str]]:
    """检测跨文档模式（重复出现的主题）。"""
    # 按 tags 分组统计关键词
    tag_keywords: dict[str, list[str]] = defaultdict(list)
    for chunk in chunks:
        for tag in chunk["tags"].split(","):
            tag = tag.strip()
            if tag:
                tag_keywords[tag].extend(chunk["keywords"])

    # 每个 tag 下的高频关键词
    patterns = {}
    for tag, kws in tag_keywords.items():
        counter = Counter(kws)
        top = [w for w, c in counter.most_common(5) if c >= 2]
        if top:
            patterns[tag] = top
    return patterns


def generate_insights(chunks: list[dict], cross_refs: list[dict], patterns: dict) -> list[str]:
    """基于分析结果生成洞察。"""
    insights = []

    # 1. 高频主题洞察
    all_keywords = []
    for chunk in chunks:
        all_keywords.extend(chunk["keywords"])
    top_global = Counter(all_keywords).most_common(10)
    if top_global:
        kw_list = ", ".join([f"{w}({c})" for w, c in top_global[:5]])
        insights.append(f"全局高频主题：{kw_list}")

    # 2. 交叉引用洞察
    if cross_refs:
        top_refs = cross_refs[:3]
        for ref in top_refs:
            kw_str = "、".join(ref["shared_keywords"][:3])
            insights.append(
                f"跨文档连接：「{ref['doc_a']}」←→「{ref['doc_b']}」"
                f"通过「{kw_str}」关联（强度={ref['strength']}）"
            )

    # 3. 模式洞察
    for tag, kws in patterns.items():
        kw_str = "、".join(kws[:4])
        insights.append(f"[{tag}] 标签下反复出现的主题：{kw_str}")

    # 4. 孤立文档（无交叉引用的文档）
    all_sources = set(c["source"] for c in chunks)
    connected = set()
    for ref in cross_refs:
        for c in chunks:
            if Path(c["source"]).name == ref["doc_a"]:
                connected.add(c["source"])
            if Path(c["source"]).name == ref["doc_b"]:
                connected.add(c["source"])
    isolated = all_sources - connected
    if isolated:
        names = [Path(s).name for s in list(isolated)[:3]]
        insights.append(f"孤立文档（无跨文档连接）：{', '.join(names)}")

    return insights


def _run_housekeeping(
    db_path: Path,
    housekeeping: Optional[dict] = None,
) -> dict:
    """执行 dreaming 配套的整理任务（零 LLM 依赖）。

    dreaming 默认执行：
      - 清理过期记忆
      - 识别可能需要压缩的旧记忆候选
      - 识别相似的 chunk 候选

    具体压缩/合并由外部 Agent 判断并执行，Shibei 自身不调用 LLM。
    任何子任务失败都只记录 warning，不影响整体 dream 结果。
    """
    if housekeeping is False:
        return {"enabled": False}

    cfg = DEFAULT_HOUSEKEEPING.copy()
    if isinstance(housekeeping, dict):
        cfg.update(housekeeping)

    result: dict = {"enabled": True, "memory": {}, "consolidation": {}}

    # 记忆：清理过期 + 识别可压缩候选
    try:
        from .memory import MemoryStore

        store = MemoryStore(db_path)

        if cfg["cleanup_expired_memories"]:
            deleted = store.cleanup_expired()
            result["memory"]["expired_deleted"] = deleted

        candidates = store.find_compressible(
            max_age_days=int(cfg["compress_max_age_days"]),
            keep_recent=int(cfg["compress_keep_recent"]),
        )
        result["memory"]["compressible_candidates"] = candidates
        result["memory"]["compressible_count"] = len(candidates)
    except Exception as e:
        logger.warning("记忆整理失败: %s", e)
        result["memory"]["error"] = str(e)

    # 知识库：识别相似 chunk 候选
    try:
        from .consolidator import consolidate

        consolidation = consolidate(
            db_path,
            threshold=cfg["consolidate_threshold"],
            min_cluster_size=int(cfg["consolidate_min_cluster_size"]),
            max_chunks=int(cfg["consolidate_max_chunks"]),
        )
        result["consolidation"] = {
            "status": consolidation.get("status"),
            "total_chunks": consolidation.get("total_chunks", 0),
            "cluster_count": consolidation.get("cluster_count", 0),
            "clusters": consolidation.get("clusters", []),
        }
    except Exception as e:
        logger.warning("知识库合并整理失败: %s", e)
        result["consolidation"]["error"] = str(e)

    return result


def _persist_dream_result(db_path: Path, result: dict) -> None:
    """将最近一次 dream 结果持久化到 db_path/dream_result.json。"""
    try:
        persist_path = db_path / "dream_result.json"
        persist_path.parent.mkdir(parents=True, exist_ok=True)
        import json
        persist_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("持久化 dream 结果失败: %s", e)


def dream(
    db_path: Path,
    housekeeping: Optional[dict] = None,
    format: str = "brief",
    persist: bool = True,
) -> dict:
    """执行 dreaming — 知识库洞察 + 整理候选发现（零 LLM 依赖）。

    Args:
        db_path: 数据存储根路径。
        housekeeping: 整理配置；False 禁用，True/None 使用默认，dict 自定义。
        format: "brief" 返回统计与洞察摘要；"full" 额外返回 cross_references/patterns 详情。
        persist: 是否将结果持久化到 db_path/dream_result.json，供后续 diff。

    dreaming 默认执行：
      - 清理过期记忆
      - 识别可能需要压缩的旧记忆候选
      - 识别相似的 chunk 候选

    具体压缩/合并由外部 Agent 判断并执行；Shibei 自身不调用 LLM。
    """
    chunks = load_chunks(db_path)

    if not chunks:
        return {"status": "empty", "message": "知识库为空，无法 dream"}

    cross_refs = find_cross_references(chunks)
    patterns = find_patterns(chunks)
    insights = generate_insights(chunks, cross_refs, patterns)

    hk_result = _run_housekeeping(
        db_path,
        housekeeping=housekeeping,
    )

    result: dict = {
        "status": "ok",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "format": format,
        "stats": {
            "total_chunks": len(chunks),
            "unique_docs": len(set(c["source"] for c in chunks)),
            "cross_references": len(cross_refs),
            "topics_with_connections": len(set(
                kw for ref in cross_refs for kw in ref["shared_keywords"]
            )),
        },
        "insights": insights,
        "housekeeping": hk_result,
    }

    if format == "full":
        result["cross_references"] = cross_refs[:10]
        result["patterns"] = patterns

    if persist:
        _persist_dream_result(db_path, result)

    return result
