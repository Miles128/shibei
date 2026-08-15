"""答前上下文包 — 从用户消息抽取检索词并召回文档/记忆。

供 MCP `shibei_context` 与 CLI/SDK 使用，宿主无关。
"""

import logging
import re

from .config import Config
from .searcher import Searcher

logger = logging.getLogger(__name__)

# 去人称/语气/口语填充词
_STOP_PHRASES = [
    "讲讲", "说说", "介绍一下", "帮我", "请问", "怎么看", "怎么样",
    "什么是", "有没有", "能不能", "可不可以", "一下", "一下吧",
]
_STOP_WORDS = {
    "我", "你", "您", "他", "她", "我们", "你们", "他们",
    "的", "了", "吗", "呢", "吧", "啊", "呀", "嘛",
    "是", "有", "在", "和", "与", "或", "及",
    "这个", "那个", "什么", "怎么", "怎样", "如何",
    "一下", "一些", "一个", "请", "帮", "想", "要",
    "关于", "对于", "因为", "所以", "但是", "然后",
    "谢谢", "哈哈", "嗯", "哦",
}

_FIRST_PERSON_RE = re.compile(
    r"(我|本人).{0,40}(喜欢|习惯|计划|决定|负责|做过|开发过|偏好|目标是)"
)
_ALNUM_RE = re.compile(r"[A-Za-z0-9_]{2,}")
_CN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")


def extract_query_terms(message: str) -> str:
    """规则抽取检索词：去人称/语气词，保留实体与名词片段。"""
    text = (message or "").strip()
    if not text:
        return ""

    for phrase in _STOP_PHRASES:
        text = text.replace(phrase, " ")

    # 优先 jieba 精确分词
    try:
        from .tokenizer import jieba_available
        if jieba_available():
            import jieba
            tokens = [
                t.strip()
                for t in jieba.cut(text)
                if t.strip() and t.strip() not in _STOP_WORDS and len(t.strip()) >= 2
            ]
            # 保留英文 token
            tokens.extend(_ALNUM_RE.findall(message))
            seen: set[str] = set()
            ordered: list[str] = []
            for t in tokens:
                if t not in seen:
                    seen.add(t)
                    ordered.append(t)
            if ordered:
                return " ".join(ordered[:12])
    except Exception as exc:  # noqa: BLE001
        logger.debug("jieba 抽词失败，降级正则: %s", exc)

    terms: list[str] = []
    seen = set()
    for m in _CN_RE.findall(text):
        if m in _STOP_WORDS or m in seen:
            continue
        seen.add(m)
        terms.append(m)
    for m in _ALNUM_RE.findall(message or ""):
        if m in seen:
            continue
        seen.add(m)
        terms.append(m)
    return " ".join(terms[:12])


def suggest_remember_from_message(message: str) -> dict:
    """启发式判断是否建议写入记忆，并给出 fact 草稿。"""
    msg = (message or "").strip()
    if not msg:
        return {"should": False, "fact": "", "reason": ""}

    if _FIRST_PERSON_RE.search(msg):
        fact = msg
        if len(fact) > 200:
            fact = fact[:200].rstrip() + "…"
        return {
            "should": True,
            "fact": fact,
            "reason": "first_person_disclosure",
        }

    # 简短「我…」陈述
    if msg.startswith("我") and 4 <= len(msg) <= 120 and ("？" not in msg and "?" not in msg):
        return {
            "should": True,
            "fact": msg,
            "reason": "short_first_person",
        }

    return {"should": False, "fact": "", "reason": ""}


def build_context(
    cfg: Config,
    message: str,
    limit: int = 5,
    tag: str | None = None,
    write_hints: bool = True,
    query: str | None = None,
) -> dict:
    """从用户消息构建答前上下文包。

    Args:
        message: 用户原文（用于 suggest_remember 与默认抽词）。
        query: 可选检索词。宿主 Agent 用自己的 LLM 扩写后应传入，优先于规则抽词。

    Returns:
        {
            "message": str,
            "query_used": str,
            "results": [...],
            "memories": [...],
            "related": [...],
            "rewrite": ...,
            "llm_rewrite": ...,
            "expansion": ...,
            "suggest_remember": {"should", "fact", "reason"} | omitted,
            "total": int,
        }
    """
    message = (message or "").strip()
    # Agent 侧改写优先；否则规则抽词；再退回原文
    query = (query or "").strip() or extract_query_terms(message) or message

    search_result = Searcher(cfg).search(
        query, limit=limit, tag=tag, include_memories=True
    )

    # 截断结果文本，控制 MCP 上下文体积
    results = []
    for r in search_result.get("results", []):
        item = dict(r)
        text = item.get("text") or ""
        if len(text) > 240:
            item["text"] = text[:240] + "…"
        parent = item.get("parent_text") or ""
        if len(parent) > 400:
            item["parent_text"] = parent[:400] + "…"
        results.append(item)

    # 记忆同样截断，避免长记忆撑爆 context pack
    memories = []
    for m in search_result.get("memories", []):
        item = dict(m)
        text = item.get("text") or ""
        if len(text) > 240:
            item["text"] = text[:240] + "…"
        memories.append(item)

    # 轻量画像层（P0）：按类别聚合的高密度事实，答前随包注入
    persona = None
    try:
        from .memory import MemoryStore

        p = MemoryStore(cfg.db_path_expanded).persona()
        if p.get("total"):
            persona = {
                "categories": {
                    cat: [f["fact"][:120] for f in facts]
                    for cat, facts in p["categories"].items()
                },
                "total": p["total"],
            }
    except Exception as exc:  # noqa: BLE001
        logger.debug("persona 聚合失败: %s", exc)

    pack = {
        "message": message,
        "query_used": search_result.get("query_used", query),
        "total": search_result.get("total", len(results)),
        "results": results,
        "memories": memories,
        "related": search_result.get("related", []),
    }
    if persona:
        pack["persona"] = persona
    if "rewrite" in search_result:
        pack["rewrite"] = search_result["rewrite"]
    if "llm_rewrite" in search_result:
        pack["llm_rewrite"] = search_result["llm_rewrite"]
    if "expansion" in search_result:
        pack["expansion"] = search_result["expansion"]

    if write_hints:
        pack["suggest_remember"] = suggest_remember_from_message(message)

    return pack
