"""统一召回内核 — CLI / MCP / DSH / HTTP / TUI 共用的单一口径。

只有一个 ``recall()``：所有入口都通过它取召回结果，差异只体现在参数上
（``mode`` 决定是否压缩成答前上下文包，``trace`` 决定是否附诊断信息）。

返回体固定带 ``schema_version``，方便外部消费者做兼容判断。
"""

from __future__ import annotations

import time
from copy import deepcopy
from typing import Any

from .config import Config
from .searcher import Searcher

SCHEMA_VERSION = 1
MODES = ("raw", "pack")

# pack 模式下压缩文本，避免答前上下文包撑爆宿主窗口
_PACK_TRIM = {"text": 240, "parent_text": 400, "memory": 240}


def apply_overrides(
    cfg: Config,
    *,
    rewrite: 'bool | None' = None,
    llm_rewrite: 'bool | None' = None,
    expand: 'bool | None' = None,
    fuzzy: 'bool | None' = None,
    synonyms: 'str | None' = None,
    vector: 'bool | None' = None,
    rerank: 'bool | None' = None,
) -> Config:
    """按调用方传入的开关覆盖检索配置；全部为 None 时原样返回。"""
    if not any(
        x is not None
        for x in (rewrite, llm_rewrite, expand, fuzzy, synonyms, vector, rerank)
    ):
        return cfg

    cfg = deepcopy(cfg)
    if rewrite:
        cfg.rewriter.enabled = True
    if llm_rewrite:
        cfg.rewriter.enabled = True
        cfg.rewriter.use_llm = True
    if expand is True:
        cfg.expander.enabled = True
    elif expand is False:
        cfg.expander.enabled = False
    if synonyms:
        cfg.expander.synonyms_path = synonyms
        cfg.expander.enabled = True
    if fuzzy:
        cfg.expander.fuzzy = True
        cfg.expander.enabled = True
    if vector is not None:
        cfg.vector.enabled = vector
    if rerank is not None:
        cfg.rerank.enabled = rerank
    return cfg


def _trim_items(items: list[dict], limit: int, key: str) -> list[dict]:
    out = []
    for item in items:
        item = dict(item)
        text = item.get(key) or ""
        if len(text) > limit:
            item[key] = text[:limit] + "…"
        out.append(item)
    return out


def _dropped(stages: list[dict]) -> dict:
    """从 retrieve 阶段的候选数推算被丢在哪一步。"""
    dropped: dict[str, int] = {}
    for stage in stages:
        if stage.get("name") != "retrieve":
            continue
        coverage = int(stage.get("coverage_dropped") or 0)
        if coverage:
            dropped["coverage_gate"] = coverage
        fused = int(stage.get("fused") or 0)
        returned = int(stage.get("returned") or 0)
        if fused - coverage - returned > 0:
            dropped["limit"] = fused - coverage - returned
    return dropped


def _degraded(cfg: Config, requested: dict, result: dict) -> list[str]:
    """列出「要了但没生效」的环节，供 UI 直接展示。"""
    out: list[str] = []
    hits = result.get("results") or []
    if hits and hits[0].get("why") is None:
        out.append("why: 未生成命中路由（trace 关闭）")
    if (requested.get("llm_rewrite") or cfg.rewriter.use_llm) and not result.get(
        "llm_rewrite"
    ):
        out.append("llm_rewrite: 未生效（检查 SHIBEI_LLM_API_KEY）")
    if (requested.get("vector") or cfg.vector.enabled) and not result.get("hybrid"):
        out.append("vector: 未生效（embedding provider 不可用）")
    if (requested.get("rerank") or cfg.rerank.enabled) and not result.get("rerank"):
        out.append("rerank: 未生效（fastembed 未安装或模型加载失败）")
    return out


def _apply_full_text(cfg: Config, results: list[dict]) -> None:
    """结果里的 text 是截断过的，按 rowid 回表取全文。"""
    rowids: list[int] = [
        int(r["_rowid"]) for r in results if r.get("_rowid") is not None
    ]
    if not rowids:
        return
    from .bm25 import BM25Index

    full = BM25Index(
        str(cfg.db_path_expanded / "fts5.db"),
        vector_config=cfg.vector,
    ).fetch_rows(rowids)
    for item in results:
        row = full.get(int(item.get("_rowid") or 0))
        if not row:
            continue
        for key in ("source", "file_path", "tags", "text", "parent_text"):
            if row.get(key):
                item[key] = row[key]


def recall(
    cfg: Config,
    query: 'str | None' = None,
    message: str = "",
    limit: int = 5,
    tag: 'str | None' = None,
    mode: str = "raw",
    memories: bool = True,
    persona: bool = False,
    write_hints: bool = False,
    trace: bool = False,
    full_text: bool = False,
    rewrite: 'bool | None' = None,
    llm_rewrite: 'bool | None' = None,
    expand: 'bool | None' = None,
    fuzzy: 'bool | None' = None,
    synonyms: 'str | None' = None,
    vector: 'bool | None' = None,
    rerank: 'bool | None' = None,
) -> dict:
    """统一召回入口。

    Args:
        query: 检索词。为空时从 ``message`` 抽取（pack 模式）或直接使用 ``message``。
        message: 用户原文，用于 ``suggest_remember`` 与默认抽词。
        mode: ``raw`` 返回未压缩结果；``pack`` 返回答前上下文包。
        persona: pack 模式默认开启；raw 模式按需开启。
        trace: 是否收集阶段耗时、命中路由与丢弃统计。
        full_text: 是否按 rowid 回表取全文（默认结果里的 text 只有 300 字）。
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    message = (message or "").strip()
    original = (query or "").strip()
    if not original:
        if mode == "pack":
            from .context import extract_query_terms

            original = extract_query_terms(message)
        original = original or message

    started = time.perf_counter()
    stages: list[dict] = []
    effective_cfg = apply_overrides(
        cfg,
        rewrite=rewrite,
        llm_rewrite=llm_rewrite,
        expand=expand,
        fuzzy=fuzzy,
        synonyms=synonyms,
        vector=vector,
        rerank=rerank,
    )

    if original:
        result = Searcher(effective_cfg).search(
            original,
            limit=limit,
            tag=tag,
            include_memories=memories,
            trace=stages if trace else None,
        )
    else:
        result = {"query": "", "query_used": "", "total": 0,
                  "results": [], "memories": [], "related": []}

    results = [dict(r) for r in result.get("results", [])]
    memories_out = [dict(m) for m in result.get("memories", [])]

    if full_text and results:
        _apply_full_text(effective_cfg, results)

    if trace and results:
        from .tokenizer import query_terms

        terms = query_terms(result.get("query_used") or original)
        for item in results:
            why = item.setdefault("why", {"routes": [], "ranks": {}})
            hay = f'{item.get("text") or ""}\n{item.get("parent_text") or ""}'
            why["matched_terms"] = [t for t in terms if t in hay]

    if mode == "pack":
        results = _trim_items(results, _PACK_TRIM["text"], "text")
        results = _trim_items(results, _PACK_TRIM["parent_text"], "parent_text")
        memories_out = _trim_items(memories_out, _PACK_TRIM["memory"], "text")

    envelope: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "message": message,
        "query": result.get("query", original),
        "query_used": result.get("query_used", original),
        "total": result.get("total", len(results)),
        "results": results,
        "memories": memories_out,
        "related": result.get("related", []),
    }
    for key in ("rewrite", "llm_rewrite", "expansion", "hybrid", "rerank"):
        if key in result:
            envelope[key] = result[key]

    if mode == "pack":
        persona = True
    if persona:
        block = _persona_block(effective_cfg)
        if block:
            envelope["persona"] = block

    if write_hints:
        from .context import suggest_remember_from_message

        envelope["suggest_remember"] = suggest_remember_from_message(message)

    if trace:
        envelope["trace"] = {
            "stages": stages,
            "total_ms": round((time.perf_counter() - started) * 1000, 1),
            "dropped": _dropped(stages),
            "degraded": _degraded(
                effective_cfg,
                {
                    "llm_rewrite": llm_rewrite,
                    "vector": vector,
                    "rerank": rerank,
                },
                result,
            ),
        }
    return envelope


def _persona_block(cfg: Config) -> dict | None:
    """按类别聚合的高密度事实，pack 模式随包注入。"""
    try:
        from .memory import MemoryStore

        data = MemoryStore(cfg.db_path_expanded).persona()
    except Exception:  # noqa: BLE001
        return None
    if not data.get("total"):
        return None
    return {
        "categories": {
            cat: [f["fact"][:120] for f in facts]
            for cat, facts in data["categories"].items()
        },
        "total": data["total"],
    }
