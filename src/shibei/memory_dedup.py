"""在线记忆冲突检测（批量）。

提炼自 TencentDB Agent Memory 的 L1 dedup：
1. 每条新记忆先召回 top-K 旧记忆（BM25/向量，由调用方完成）
2. 所有新记忆 + 候选池交给一次 LLM 调用
3. 输出 store / update / skip；update 走版本链（supersedes）
"""

from __future__ import annotations

import json
from typing import Any

from .memory_extract import strip_json_fences

DEDUP_SYSTEM_PROMPT = """你是记忆冲突检测器。
给定「新记忆」和每条新记忆的「候选旧记忆」，判断每条新记忆应该如何处理：

- skip：候选旧记忆已经等价或更完整，新记忆没有增量。
- update：新记忆修正/取代了某条旧记忆（事实变了、时间更新、决策改了）。
- store：新记忆是独立的新信息，或不确定。

只返回合法 JSON 数组，不要 Markdown 代码块：
[{"index":0,"action":"store","target_id":""}]

规则：
- update 必须给出 target_id，且只能从该条新记忆的候选里选。
- 宁可 store，不要误 skip；不确定就 store。
- 不要合并多条候选；一条新记忆最多替代一条旧记忆。"""


def build_user_prompt(
    items: list[dict],
    candidates: list[list[dict]],
) -> str:
    lines = ["【新记忆】"]
    for i, item in enumerate(items):
        kind = item.get("memory_type") or "memory"
        lines.append(f"{i}. [{kind}] {item.get('content', '')}")
    lines.append("")
    lines.append("【候选旧记忆】")
    for i, pool in enumerate(candidates):
        if not pool:
            lines.append(f"{i}. （无）")
            continue
        lines.append(f"{i}. " + json.dumps(
            [
                {
                    "id": c.get("id", ""),
                    "fact": (c.get("fact") or "")[:200],
                    "score": c.get("score", 0),
                }
                for c in pool
            ],
            ensure_ascii=False,
        ))
    lines.append("")
    lines.append(f"返回 {len(items)} 条决策的 JSON 数组。")
    return "\n".join(lines)


def parse_decisions(raw: str, count: int, candidates: list[list[dict]]) -> list[dict] | None:
    """解析 LLM 决策；任何结构问题返回 None（调用方回退为全部 store）。"""
    try:
        data = json.loads(strip_json_fences(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, list):
        return None

    by_index: dict[int, dict] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        index_raw = entry.get("index")
        if index_raw is None:
            continue
        try:
            index = int(index_raw)
        except (TypeError, ValueError):
            continue
        action = str(entry.get("action") or "store").lower()
        if action not in {"store", "update", "skip"}:
            action = "store"
        target = str(entry.get("target_id") or "")
        if action == "update":
            allowed = {c.get("id") for c in candidates[index] if index < len(candidates)}
            if target not in allowed:
                action = "store"
                target = ""
        by_index[index] = {"action": action, "target_id": target}

    return [by_index.get(i, {"action": "store", "target_id": ""}) for i in range(count)]


def decide(
    client: Any,
    items: list[dict],
    candidates: list[list[dict]],
) -> list[dict] | None:
    """一次批量 LLM 调用；失败返回 None。"""
    if not items or not any(candidates):
        return None
    try:
        raw = client.chat([
            {"role": "system", "content": DEDUP_SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(items, candidates)},
        ])
    except Exception:  # noqa: BLE001
        return None
    return parse_decisions(raw, len(items), candidates)
