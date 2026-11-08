"""跨库联邦召回 — 把多个 brain 的结果按 RRF 排名融合。

为什么不平均分数：BM25 的 IDF 依赖各自语料，两个库的 score 不可比。
唯一跨库可比的是「名次」，所以用 RRF（Reciprocal Rank Fusion）：
``gain = 1 / (k + rank)``，同一条结果被多个库命中时累加。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ragkit.fusion import rrf_gain

from .config import Config
from .recall import recall

RRF_K = 60


def _run_brain(engine: Any, kwargs: dict) -> dict:
    """本地 Config 走 recall()，远程/已实例化的 brain 直接调 recall。"""
    if hasattr(engine, "recall"):
        return engine.recall(**kwargs)
    return recall(engine, **kwargs)


def _dedupe_key(item: dict) -> tuple[str, str]:
    path = str(item.get("file_path") or item.get("source") or "")
    text = (item.get("text") or "").strip()[:160]
    return (path, text)


def recall_federated(
    brains: 'list[tuple[str, Any]]',
    query: 'str | None' = None,
    message: str = "",
    limit: int = 5,
    tag: 'str | None' = None,
    mode: str = "raw",
    memories: bool = True,
    persona: bool = False,
    write_hints: bool = False,
    trace: bool = True,
    full_text: bool = False,
    rewrite: 'bool | None' = None,
    llm_rewrite: 'bool | None' = None,
    expand: 'bool | None' = None,
    fuzzy: 'bool | None' = None,
    synonyms: 'str | None' = None,
    vector: 'bool | None' = None,
    rerank: 'bool | None' = None,
    k: int = RRF_K,
) -> dict:
    if not brains:
        raise ValueError("没有可用的 brain")

    started = time.perf_counter()
    kwargs: dict[str, Any] = {
        "query": query,
        "message": message,
        "limit": limit,
        "tag": tag,
        "mode": mode,
        "memories": memories,
        "persona": persona,
        "write_hints": write_hints,
        "trace": True,
        "full_text": full_text,
        "rewrite": rewrite,
        "llm_rewrite": llm_rewrite,
        "expand": expand,
        "fuzzy": fuzzy,
        "synonyms": synonyms,
        "vector": vector,
        "rerank": rerank,
    }

    per_brain: dict[str, dict] = {}
    errors: dict[str, str] = {}
    names = [name for name, _ in brains]
    with ThreadPoolExecutor(max_workers=min(8, len(brains))) as pool:
        futures = [
            (name, pool.submit(_run_brain, engine, kwargs))
            for name, engine in brains
        ]
        for name, future in futures:
            try:
                per_brain[name] = future.result()
            except Exception as exc:  # noqa: BLE001
                errors[name] = str(exc)

    merged: dict[tuple[str, str], dict] = {}
    for name in names:
        brain_env = per_brain.get(name)
        if not brain_env:
            continue
        for rank, item in enumerate(brain_env.get("results") or [], start=1):
            key = _dedupe_key(item)
            gain = rrf_gain(rank, k)
            current = merged.get(key)
            if current is None:
                copy = dict(item)
                copy["brain"] = name
                copy["brains"] = [name]
                copy["fused_score"] = gain
                copy["origin_score"] = item.get("score")
                copy["origin_score_kind"] = item.get("score_kind")
                copy["score"] = round(gain, 6)
                copy["score_kind"] = "rrf"
                merged[key] = copy
                continue

            current["fused_score"] += gain
            if name not in current["brains"]:
                current["brains"].append(name)
            if len(item.get("text") or "") > len(current.get("text") or ""):
                current["text"] = item.get("text") or ""
                parent = item.get("parent_text")
                if parent:
                    current["parent_text"] = parent
                current["brain"] = name
                current["why"] = item.get("why") or current.get("why")
            current["score"] = round(current["fused_score"], 6)

    results = sorted(
        merged.values(), key=lambda item: item["fused_score"], reverse=True
    )[:limit]
    for index, item in enumerate(results, start=1):
        item["rank"] = index

    memory_index: dict[str, dict] = {}
    for name in names:
        for item in (per_brain.get(name) or {}).get("memories") or []:
            mkey = (item.get("text") or "").strip()
            existing = memory_index.get(mkey)
            if existing is None:
                copy = dict(item)
                copy["brain"] = name
                memory_index[mkey] = copy
            elif float(item.get("score") or 0) > float(existing.get("score") or 0):
                copy = dict(item)
                copy["brain"] = name
                memory_index[mkey] = copy
    memory_out = sorted(
        memory_index.values(), key=lambda m: float(m.get("score") or 0), reverse=True
    )[:limit]

    related_index: dict[str, dict] = {}
    for name in names:
        for item in (per_brain.get(name) or {}).get("related") or []:
            related_index.setdefault(str(item.get("source") or ""), item)

    query_used = ""
    for name in names:
        used = (per_brain.get(name) or {}).get("query_used")
        if used:
            query_used = used
            break

    trace_block = {
        "stages": [
            {
                "name": name,
                "ms": ((per_brain.get(name) or {}).get("trace") or {}).get("total_ms", 0),
                "hits": len((per_brain.get(name) or {}).get("results") or []),
            }
            for name in names
            if name in per_brain
        ],
        "total_ms": round((time.perf_counter() - started) * 1000, 1),
        "dropped": {},
        "degraded": [f"{name}: {reason}" for name, reason in errors.items()],
        "federated": {
            "k": k,
            "brains": names,
            "counts": {
                name: len((per_brain.get(name) or {}).get("results") or [])
                for name in names
                if name in per_brain
            },
            "errors": errors,
        },
    }

    envelope: dict[str, Any] = {
        "schema_version": 1,
        "mode": mode,
        "federated": True,
        "brains": names,
        "message": message,
        "query": query or "",
        "query_used": query_used or query or "",
        "per_brain_query": {
            name: (per_brain.get(name) or {}).get("query_used", "")
            for name in names
            if name in per_brain
        },
        "total": len(results),
        "results": results,
        "memories": memory_out,
        "related": list(related_index.values()),
        "trace": trace_block,
    }
    if persona:
        personas = {
            name: (per_brain.get(name) or {}).get("persona")
            for name in names
            if (per_brain.get(name) or {}).get("persona")
        }
        if personas:
            envelope["persona_by_brain"] = personas
    if write_hints:
        envelope["suggest_remember"] = (per_brain.get(names[0]) or {}).get(
            "suggest_remember"
        )
    return envelope
