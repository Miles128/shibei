"""查询改写模块 — 基于规则/关键词映射把口语查询改写成更适合 BM25 的查询。

策略：
  1. 加载配置中的规则列表（source -> target）。
  2. 按顺序对查询串做子串替换。
  3. 返回结构化信息，供上层日志/展示。

不依赖任何 LLM，保持 BM25 索引结构不变。
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RewriteRule:
    """单条改写规则。"""

    source: str = ""      # 待替换关键词
    target: str = ""      # 替换后的关键词
    enabled: bool = True  # 是否启用


class QueryRewriter:
    """基于规则的查询改写器。"""

    def __init__(self, rules: list[RewriteRule]):
        self.rules = [r for r in rules if r.enabled and r.source]

    def rewrite(self, query: str) -> dict:
        """改写查询，返回结构化信息。

        Returns:
            {
                "original": "原始查询",
                "rewritten": "改写后查询",
                "applied": [{"from": ..., "to": ...}, ...],
            }
        """
        query = query.strip()
        if not query or not self.rules:
            return {
                "original": query,
                "rewritten": query,
                "applied": [],
            }

        rewritten = query
        applied: list[dict[str, str]] = []

        for rule in self.rules:
            if rule.source in rewritten:
                rewritten = rewritten.replace(rule.source, rule.target)
                applied.append({"from": rule.source, "to": rule.target})
                logger.debug("改写: %s -> %s", rule.source, rule.target)

        return {
            "original": query,
            "rewritten": rewritten,
            "applied": applied,
        }

    def rewrite_query_for_bm25(self, query: str) -> str:
        """直接返回改写后的 BM25 查询字符串。"""
        return self.rewrite(query)["rewritten"]
