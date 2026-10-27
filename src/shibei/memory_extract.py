"""L1 结构化记忆抽取。

思路借鉴 TencentDB Agent Memory 的 L1 抽取（MIT）：一次调用同时做情境切分
与记忆提取，输出 persona / episodic / instruction 三类原子记忆，并带优先级、
置信度与来源消息 id。这里只保留单机需要的部分，不引入 scene 文件与团队模型。
"""

from __future__ import annotations

import json
import re
from typing import Any

EXTRACT_SYSTEM_PROMPT = """你是专业的"情境切分与记忆提取专家"。
分析用户的新消息，判断情境，并提取值得长期保留的结构化记忆。

输出语言：content 与 scene_name 使用与用户消息相同的语言；JSON 字段名保持英文。

## 任务一：情境切分
结合上一个情境，判断当前对话属于哪个情境。
- 继承：没有明显切换，沿用上一个情境。
- 切换：用户明确换话题、意图改变，或提出独立新目标。
- 命名："我在和<用户身份>做<目标活动>"，约 10-40 字，单句。

## 任务二：记忆提取
只从【新消息】中提取，且只提以下三类：

1. persona：用户的稳定属性、偏好、技能、价值观、习惯。
   句式："用户（姓名）喜欢/是/擅长……"
   priority：80-100（健康/禁忌/核心特质）；50-70（一般喜好）；<50 丢弃。
2. episodic：客观发生的动作、决定、计划或结果。
   句式："用户在<时间>于<地点><做了某事>"
   priority：80-100（重要事件/决策）；60-70（一般活动）；<60 丢弃。
3. instruction：用户对 AI 的长期行为规则、格式/语气要求。
   句式："用户要求/希望 AI 以后……"
   priority：-1（严格死命令）；90-100（核心规则）；70-80（重要要求）；<70 丢弃。

## 不要提取
- 寒暄、问候、纯情绪
- 一次性操作（"这次帮我……"）
- 重复内容；AI 自己的输出
- 脱离上下文就看不懂的碎片

## 规则
- 宁缺毋滥；每条记忆必须独立完整，脱离当前对话也能看懂。
- 强关联的事实合并成一条，不要拆碎。
- confidence 是 0-1 的置信度；不确定就降低，不要编造。

## 输出格式
只返回合法 JSON 数组，不要 Markdown 代码块：

[
  {
    "scene_name": "我在和四海做 Lumina 的 dsh 迁移",
    "memories": [
      {
        "content": "用户决定用 dsh 原生 workflow 脚本替代自研 Skill DAG",
        "type": "instruction",
        "priority": 90,
        "confidence": 0.9,
        "source_message_ids": ["msg-1"]
      }
    ]
  }
]

没有值得记的内容时，返回 []。"""


def build_user_prompt(message: str, *, background: str = "", message_id: str = "") -> str:
    parts = []
    if background.strip():
        parts.append(f"【上一个情境】\n{background.strip()}")
    if message_id:
        parts.append(f"【消息 ID】\n{message_id}")
    parts.append(f"【新消息】\n{message.strip()[:4000]}")
    return "\n\n".join(parts)


def strip_json_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _as_id_list(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return ",".join(str(v).strip() for v in value if str(v).strip())
    return ""


def classify_heuristic(fact: str) -> dict:
    """无 LLM 的 L1 粗分类，用于回填旧记忆。

    只保证「有类型/优先级」，不保证精度；需要高质量时用 auto_memory 重抽。
    """
    text = fact.strip()
    instruction_markers = ("以后", "不要", "必须", "记住", "要求", "每次", "禁止", "一律")
    persona_markers = ("喜欢", "偏好", "习惯", "擅长", "常用", "倾向于", "不喜欢")
    if any(marker in text for marker in instruction_markers):
        memory_type, priority = "instruction", 80
    elif any(marker in text for marker in persona_markers):
        memory_type, priority = "persona", 70
    else:
        memory_type, priority = "episodic", 60
    return {
        "memory_type": memory_type,
        "priority": priority,
        "scene_name": "",
        "confidence": 0.5,
    }


def parse_extraction(raw: str, *, default_message_id: str = "") -> list[dict]:
    """把 LLM 输出解析为扁平的 L1 记忆列表。

    容错：Markdown 代码块、单场景对象、字段名 type/memory_type 混用、
    priority/confidence 越界、source_message_ids 为字符串或数组。
    解析失败返回空列表（调用方降级）。
    """
    try:
        data = json.loads(strip_json_fences(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return []

    scenes = data if isinstance(data, list) else [data]
    out: list[dict] = []

    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        scene_name = str(scene.get("scene_name") or scene.get("scene") or "").strip()
        memories = scene.get("memories")
        if memories is None and "content" in scene:
            memories = [scene]
        if not isinstance(memories, list):
            continue

        for item in memories:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or item.get("fact") or "").strip()
            if len(content) < 4:
                continue
            memory_type = str(
                item.get("type") or item.get("memory_type") or "episodic"
            ).strip().lower()
            if memory_type not in {"persona", "episodic", "instruction"}:
                memory_type = "episodic"
            source_ids = _as_id_list(
                item.get("source_message_ids") or item.get("source_message_id")
            )
            if not source_ids and default_message_id:
                source_ids = default_message_id
            out.append({
                "content": content,
                "memory_type": memory_type,
                "priority": int(_clamp(item.get("priority"), -1, 100, 50)),
                "confidence": round(_clamp(item.get("confidence"), 0, 1, 0.6), 2),
                "scene_name": str(item.get("scene_name") or scene_name).strip(),
                "source_message_ids": source_ids,
            })
    return out
