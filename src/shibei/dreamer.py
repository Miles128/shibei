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
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from collections import Counter, defaultdict
from typing import Optional

from .llm import LLMClient

logger = logging.getLogger(__name__)


# 整理（housekeeping）默认配置
DEFAULT_HOUSEKEEPING = {
    "cleanup_expired_memories": True,
    "compress_old_memories": True,
    "consolidate_chunks": True,
    "compress_max_age_days": 30,
    "compress_keep_recent": 10,
    "consolidate_threshold": 0.6,
    "consolidate_min_cluster_size": 2,
    "consolidate_max_chunks": 500,
    "consolidate_apply": False,
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
    """从文本中提取关键词（中文优先，过滤噪音）。

    优先使用 jieba（若安装），降级到正则 bigram。
    """
    # 尝试 jieba（可选依赖）
    try:
        import jieba.analyse
        kws = jieba.analyse.extract_tags(text, topK=top_n)
        if kws:
            return kws
    except ImportError:
        pass

    # 降级：正则提取
    # 提取中文词（2-6字）
    cn_words = re.findall(r'[\u4e00-\u9fff]{2,6}', text)
    # 提取英文词（3+ 字母，过滤常见技术噪音词）
    en_words = [w for w in re.findall(r'[A-Za-z]{3,}', text)
                if w.lower() not in STOP_WORDS and not w.isupper()]

    # 中文词加权（×2），英文词权重=1
    words = []
    for w in cn_words:
        if w not in STOP_WORDS and len(w) >= 2:
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
    llm_client: Optional[LLMClient] = None,
    housekeeping: Optional[dict] = None,
) -> dict:
    """执行 dreaming 配套的自动整理任务。

    包括：
      - 清理过期记忆
      - 压缩旧短期记忆（LLM 判断生命周期）
      - 聚类合并相似知识库 chunk

    任何子任务失败都只记录 warning，不影响整体 dream 结果。
    """
    if housekeeping is False:
        return {"enabled": False}

    cfg = DEFAULT_HOUSEKEEPING.copy()
    if isinstance(housekeeping, dict):
        cfg.update(housekeeping)

    result: dict = {"enabled": True, "memory": {}, "consolidation": {}}

    # 记忆清理与压缩
    try:
        from .memory import MemoryStore

        store = MemoryStore(db_path)

        if cfg["cleanup_expired_memories"]:
            deleted = store.cleanup_expired()
            result["memory"]["expired_deleted"] = deleted

        if cfg["compress_old_memories"] and llm_client and llm_client.ready:
            compress_result = store.compress_old(
                max_age_days=cfg["compress_max_age_days"],
                keep_recent=cfg["compress_keep_recent"],
                llm_client=llm_client,
            )
            result["memory"]["compress"] = compress_result
        else:
            result["memory"]["compress"] = {"skipped": True, "reason": "llm_not_ready"}
    except Exception as e:
        logger.warning("记忆整理失败: %s", e)
        result["memory"]["error"] = str(e)

    # 知识库相似 chunk 合并
    try:
        from .consolidator import consolidate

        if cfg["consolidate_chunks"]:
            consolidation = consolidate(
                db_path,
                threshold=cfg["consolidate_threshold"],
                min_cluster_size=cfg["consolidate_min_cluster_size"],
                max_chunks=cfg["consolidate_max_chunks"],
                llm_client=llm_client,
                apply=cfg.get("consolidate_apply", False),
            )
            result["consolidation"] = {
                "status": consolidation.get("status"),
                "total_chunks": consolidation.get("total_chunks", 0),
                "cluster_count": consolidation.get("cluster_count", 0),
                "applied": consolidation.get("applied", False),
                "removed": consolidation.get("removed", 0),
                "inserted": consolidation.get("inserted", 0),
            }
            # 仅在 full 模式下保留详细 clusters，避免 brief 占用过多 token
            result["consolidation"]["clusters"] = consolidation.get("clusters", [])
        else:
            result["consolidation"] = {"skipped": True}
    except Exception as e:
        logger.warning("知识库合并整理失败: %s", e)
        result["consolidation"]["error"] = str(e)

    return result


def dream(db_path: Path,
          persist: bool = True, compare: bool = True,
          format: str = "full",
          llm_client: Optional[LLMClient] = None,
          housekeeping: Optional[dict] = None) -> dict:
    """执行 dreaming — 知识库自主思考 + 自动整理。

    Args:
        db_path: 数据存储根路径
        persist: 是否持久化结果到 ~/.shibei/dreams/
        compare: 是否与上次 dream 结果对比，输出增量变化
        format: 输出格式 "full" 完整 / "brief" 精简（MCP 默认用 brief）
        llm_client: LLM 客户端，启用时增强洞察生成
        housekeeping: 控制是否执行记忆清理/压缩/知识库合并；
                      传 False 禁用，传 dict 覆盖默认配置
    """
    chunks = load_chunks(db_path)

    if not chunks:
        return {"status": "empty", "message": "知识库为空，无法 dream"}

    cross_refs = find_cross_references(chunks)
    patterns = find_patterns(chunks)
    insights = generate_insights(chunks, cross_refs, patterns)

    # LLM 增强洞察：失败时静默降级，不影响基础洞察
    if llm_client and llm_client.ready:
        try:
            all_keywords = []
            for chunk in chunks:
                all_keywords.extend(chunk.get("keywords", []))
            top_keywords = [w for w, _ in Counter(all_keywords).most_common(20)]
            llm_insights = llm_client.enhance_insights(chunks, top_keywords)
            for ins in llm_insights:
                insights.append(f"[LLM] {ins}")
        except (ConnectionError, TimeoutError, ValueError, RuntimeError) as e:
            logger.warning("LLM 洞察增强失败: %s", e)

    # 自动整理（housekeeping）
    hk_result = _run_housekeeping(
        db_path,
        llm_client=llm_client,
        housekeeping=housekeeping,
    )

    result = {
        "status": "ok",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "stats": {
            "total_chunks": len(chunks),
            "unique_docs": len(set(c["source"] for c in chunks)),
            "cross_references": len(cross_refs),
            "topics_with_connections": len(set(
                kw for ref in cross_refs for kw in ref["shared_keywords"]
            )),
        },
        "cross_references": cross_refs[:10],
        "patterns": patterns,
        "insights": insights,
        "housekeeping": hk_result,
    }

    # 增量对比：与上次 dream 结果比较
    if compare:
        prev = load_last_dream(db_path)
        if prev:
            result["diff"] = _diff_dreams(prev, result)
        else:
            result["diff"] = {"message": "首次 dream，无历史对比"}

    # 精简模式：只保留关键信息，大幅减少 token 占用
    if format == "brief":
        brief_result = {
            "status": "ok",
            "timestamp": result["timestamp"],
            "stats": result["stats"],
            "insights": insights,
        }
        if "diff" in result:
            brief_result["diff"] = {
                "stats_delta": result["diff"].get("stats_delta", {}),
                "new_insights": result["diff"].get("new_insights", []),
            }
        # housekeeping 摘要：保留统计，去掉详细 clusters
        hk = result.get("housekeeping", {})
        if hk.get("enabled"):
            brief_hk = {
                "enabled": True,
                "memory": hk.get("memory", {}),
                "consolidation": {
                    k: v for k, v in hk.get("consolidation", {}).items()
                    if k != "clusters"
                },
            }
            brief_result["housekeeping"] = brief_hk
        # 如果 MCP 直接调 brief，不持久化完整数据会丢失历史，所以先 save full 再返回 brief
        if persist:
            save_dream(db_path, result)
        return brief_result

    # 持久化
    if persist:
        save_dream(db_path, result)

    return result


# === Dream 持久化 ===

DREAMS_DIR_NAME = "dreams"


def _dreams_dir(db_path: Path) -> Path:
    """dream 结果存储目录：<db_path>/dreams/"""
    d = db_path / DREAMS_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_dream(db_path: Path, result: dict) -> Path:
    """保存 dream 结果到 <db_path>/dreams/<timestamp>.json

    同时维护 latest.json 软链接（实际是复制，避免跨平台软链问题）。
    """
    ts = result.get("timestamp") or datetime.now().strftime("%Y%m%dT%H%M%S")
    # 文件名安全化：替换冒号
    safe_ts = ts.replace(":", "").replace("-", "")
    filename = f"{safe_ts}.json"
    target = _dreams_dir(db_path) / filename
    with open(target, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 更新 latest.json
    latest = _dreams_dir(db_path) / "latest.json"
    with open(latest, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return target


def load_last_dream(db_path: Path) -> 'dict | None':
    """加载最近一次 dream 结果（不含当前）。"""
    latest = _dreams_dir(db_path) / "latest.json"
    if not latest.exists():
        return None
    try:
        with open(latest) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def list_dreams(db_path: Path, limit: int = 20) -> list[dict]:
    """列出历史 dream 记录（按时间倒序）。"""
    d = _dreams_dir(db_path)
    files = sorted(
        [f for f in d.glob("*.json") if f.name != "latest.json"],
        reverse=True,
    )[:limit]
    results = []
    for f in files:
        try:
            with open(f) as fp:
                data = json.load(fp)
                results.append({
                    "file": f.name,
                    "timestamp": data.get("timestamp", ""),
                    "stats": data.get("stats", {}),
                    "compressed": data.get("compressed", False),
                })
        except (json.JSONDecodeError, OSError):
            continue
    return results


def _diff_dreams(prev: dict, curr: dict) -> dict:
    """对比两次 dream 结果，输出增量变化。"""
    prev_refs = {(r["doc_a"], r["doc_b"]) for r in prev.get("cross_references", [])}
    curr_refs = {(r["doc_a"], r["doc_b"]) for r in curr.get("cross_references", [])}

    prev_patterns = set()
    for tag, kws in prev.get("patterns", {}).items():
        for kw in kws:
            prev_patterns.add((tag, kw))
    curr_patterns = set()
    for tag, kws in curr.get("patterns", {}).items():
        for kw in kws:
            curr_patterns.add((tag, kw))

    prev_insights = set(prev.get("insights", []))
    curr_insights = set(curr.get("insights", []))

    return {
        "new_connections": [
            f"{a} ←→ {b}" for a, b in sorted(curr_refs - prev_refs)
        ][:5],
        "lost_connections": [
            f"{a} ←→ {b}" for a, b in sorted(prev_refs - curr_refs)
        ][:5],
        "new_topics": [f"[{t}] {k}" for t, k in sorted(curr_patterns - prev_patterns)][:5],
        "lost_topics": [f"[{t}] {k}" for t, k in sorted(prev_patterns - curr_patterns)][:5],
        "new_insights": sorted(curr_insights - prev_insights),
        "stats_delta": {
            "chunks": curr["stats"]["total_chunks"] - prev["stats"]["total_chunks"],
            "docs": curr["stats"]["unique_docs"] - prev["stats"]["unique_docs"],
            "cross_refs": curr["stats"]["cross_references"] - prev["stats"]["cross_references"],
        },
    }


def compress_dreams(db_path: Path, keep_recent: int = 5) -> dict:
    """压缩历史 dream 记录：保留最近 N 次全量，更早的只存摘要。

    策略：
      - 最近 keep_recent 次：保留完整 JSON（含 cross_references/patterns/insights）
      - 更早的：只保留 timestamp + stats + compressed 标记，删除大字段
      - latest.json 始终保留全量

    Args:
        db_path: 数据存储根路径
        keep_recent: 保留最近 N 次完整记录

    Returns:
        {"compressed": N, "kept_full": M, "total": T}
    """
    dreams_dir = _dreams_dir(db_path)
    files = sorted(
        [f for f in dreams_dir.glob("*.json") if f.name != "latest.json"],
        reverse=True,  # 新到旧
    )

    total = len(files)
    compressed = 0

    for i, f in enumerate(files):
        if i < keep_recent:
            continue  # 保留最近 N 次完整
        # 旧文件：只保留 timestamp + stats
        try:
            with open(f) as fp:
                data = json.load(fp)
            slim = {
                "timestamp": data.get("timestamp", ""),
                "stats": data.get("stats", {}),
                "compressed": True,
                "compressed_at": datetime.now().isoformat(timespec="seconds"),
            }
            with open(f, "w") as fp:
                json.dump(slim, fp, ensure_ascii=False)
            compressed += 1
        except (json.JSONDecodeError, OSError):
            continue

    return {
        "compressed": compressed,
        "kept_full": min(keep_recent, total),
        "total": total,
    }


def clean_old_dreams(db_path: Path, max_age_days: int = 90) -> dict:
    """清理超过 N 天的 dream 记录（含已压缩的）。

    Args:
        db_path: 数据存储根路径
        max_age_days: 最大保留天数

    Returns:
        {"deleted": N, "remaining": M}
    """
    import time
    dreams_dir = _dreams_dir(db_path)
    files = [f for f in dreams_dir.glob("*.json") if f.name != "latest.json"]

    now = time.time()
    cutoff = now - max_age_days * 86400
    deleted = 0

    for f in files:
        try:
            mtime = f.stat().st_mtime
            if mtime < cutoff:
                f.unlink()
                deleted += 1
        except OSError:
            continue

    remaining = len([f for f in dreams_dir.glob("*.json") if f.name != "latest.json"])
    return {"deleted": deleted, "remaining": remaining}
