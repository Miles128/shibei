"""LLM 查询改写 — 把口语查询扩成 BM25 友好的关键词。

失败时降级返回原查询，不阻断搜索。
"""

import logging
import os
import re

from .config import LLMConfig
from .llm import LLMClient, LLMOptions

logger = logging.getLogger(__name__)

DEFAULT_REWRITE_PROMPT = (
    "你是检索查询改写器。把用户的口语问题改写成适合中文全文检索（BM25）的关键词。"
    "要求：\n"
    "1. 只输出空格分隔的关键词/同义表达，不要句子、不要解释、不要回答问题\n"
    "2. 保留专有名词、技术名、英文缩写\n"
    "3. 最多 {max_terms} 个词\n"
    "4. 去除人称代词和语气词（我、你、讲讲、怎么看等）\n\n"
    "用户查询：{query}"
)

_WHITESPACE_RE = re.compile(r"\s+")


class LLMQueryRewriter:
    """基于 LLM 的查询关键词扩写器。"""

    def __init__(
        self,
        llm_cfg: LLMConfig,
        max_terms: int = 8,
        prompt: str = "",
        client: LLMClient | None = None,
    ):
        self.llm_cfg = llm_cfg
        self.max_terms = max(1, max_terms)
        self.prompt = prompt or DEFAULT_REWRITE_PROMPT
        self._client = client
        if self._client is None and os.environ.get("SHIBEI_LLM_API_KEY"):
            self._client = LLMClient(
                LLMOptions(
                    base_url=llm_cfg.base_url,
                    model=llm_cfg.model,
                    timeout=llm_cfg.timeout,
                    temperature=llm_cfg.temperature,
                    max_tokens=min(llm_cfg.max_tokens, 80),
                )
            )

    def available(self) -> bool:
        """是否具备调用条件（有 client 且有 API key）。"""
        if self._client is None:
            return False
        try:
            return bool(self._client._api_key())
        except Exception:
            return False

    def rewrite(self, query: str) -> dict:
        """改写查询，返回结构化信息。

        Returns:
            {
                "original": str,
                "rewritten": str,
                "terms": list[str],
                "applied": bool,
                "error": str | None,
            }
        """
        query = query.strip()
        empty = {
            "original": query,
            "rewritten": query,
            "terms": [],
            "applied": False,
            "error": None,
        }
        if not query:
            return empty

        if not self.available():
            empty["error"] = "llm_unavailable"
            return empty

        assert self._client is not None
        try:
            raw = self._client.chat(
                [
                    {
                        "role": "user",
                        "content": self.prompt.format(
                            query=query,
                            max_terms=self.max_terms,
                        ),
                    }
                ]
            )
        except Exception as exc:  # noqa: BLE001 — 降级，不阻断搜索
            logger.warning("LLM 查询改写失败: %s", exc)
            empty["error"] = str(exc)
            return empty

        terms = self._parse_terms(raw)
        if not terms:
            empty["error"] = "empty_terms"
            return empty

        rewritten = " ".join(terms)
        return {
            "original": query,
            "rewritten": rewritten,
            "terms": terms,
            "applied": rewritten != query,
            "error": None,
        }

    def _parse_terms(self, raw: str) -> list[str]:
        """从模型输出中提取关键词列表。"""
        text = raw.strip().strip('"“”').replace("\n", " ")
        text = _WHITESPACE_RE.sub(" ", text)
        # 兼容逗号/顿号分隔
        text = text.replace("，", " ").replace(",", " ").replace("、", " ")
        parts = [p.strip() for p in text.split() if p.strip()]
        # 过滤过长“句子”
        terms: list[str] = []
        seen: set[str] = set()
        for p in parts:
            if len(p) > 40:
                continue
            if p in seen:
                continue
            seen.add(p)
            terms.append(p)
            if len(terms) >= self.max_terms:
                break
        return terms
