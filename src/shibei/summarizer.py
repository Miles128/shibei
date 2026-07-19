"""Chunk 摘要生成器 — 为每个 chunk 生成一句话中文摘要。

LLM 调用失败或功能禁用时，降级返回空字符串，不阻断导入流程。
"""

import logging

from .config import LLMConfig
from .llm import LLMClient, LLMOptions

logger = logging.getLogger(__name__)


DEFAULT_SUMMARY_PROMPT = (
    "请用一句话中文摘要下面这段内容，只输出摘要本身，不要解释，"
    "控制在 {max_length} 字以内：\n\n{content}"
)


class ChunkSummarizer:
    """基于 LLM 的 chunk 一句话摘要生成器。"""

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self._client: LLMClient | None = None
        if cfg.enabled:
            self._client = LLMClient(
                LLMOptions(
                    base_url=cfg.base_url,
                    model=cfg.model,
                    timeout=cfg.timeout,
                    temperature=cfg.temperature,
                    max_tokens=cfg.max_tokens,
                )
            )

    def summarize(self, content: str) -> str:
        """为 chunk 内容生成一句话中文摘要。

        Args:
            content: chunk 原文

        Returns:
            中文摘要；功能未启用或调用失败时返回空字符串
        """
        if not self.cfg.enabled or self._client is None:
            return ""

        if not content or not content.strip():
            return ""

        # 控制输入长度，避免过长的正文消耗过多 token
        max_len = self.cfg.max_content_length
        text = content.strip()
        if max_len > 0 and len(text) > max_len:
            text = text[:max_len]

        prompt = self.cfg.prompt or DEFAULT_SUMMARY_PROMPT
        try:
            summary = self._client.chat(
                [
                    {
                        "role": "user",
                        "content": prompt.format(
                            content=text,
                            max_length=self.cfg.max_summary_length,
                        ),
                    }
                ]
            )
        except Exception as exc:  # noqa: BLE001
            # 摘要失败不阻断导入，降级为空字符串
            logger.warning("chunk 摘要生成失败: %s", exc)
            return ""

        # 清理模型可能附带的引号或多余空行
        summary = summary.strip().strip('"“”').replace("\n", " ").strip()
        if summary in {"", "无", "None", "null"}:
            return ""
        return summary
