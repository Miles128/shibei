"""LLM 客户端 — 基于 urllib 的 OpenAI-compatible HTTP 客户端，零第三方 HTTP 依赖。"""

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class LLMOptions:
    """LLM 调用参数。

    api_key 优先使用传入值；未传入时从环境变量 SHIBEI_LLM_API_KEY 读取。
    """

    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    timeout: float = 30.0
    temperature: float = 0.3
    max_tokens: int = 100
    api_key: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)


class LLMClient:
    """OpenAI-compatible 聊天补全客户端。"""

    def __init__(self, options: LLMOptions | None = None):
        self.options = options or LLMOptions()

    def _api_key(self) -> str:
        """返回实际使用的 API key，优先显式配置，其次环境变量。"""
        if self.options.api_key:
            return self.options.api_key
        key = os.environ.get("SHIBEI_LLM_API_KEY", "")
        return key

    def chat(self, messages: list[dict[str, str]], **extra: Any) -> str:
        """发送 chat completion 请求，返回 assistant 文本内容。

        Args:
            messages: OpenAI 格式消息列表，如
                [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
            **extra: 额外请求字段（覆盖默认参数）

        Returns:
            assistant 回复文本（已 strip）

        Raises:
            urllib.error.URLError: 网络或 HTTP 错误
            ValueError: 响应解析失败或缺少 API key
        """
        api_key = self._api_key()
        if not api_key:
            raise ValueError("缺少 LLM API key，请设置 SHIBEI_LLM_API_KEY 环境变量")

        url = f"{self.options.base_url.rstrip('/')}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.options.model,
            "messages": messages,
            "temperature": self.options.temperature,
            "max_tokens": self.options.max_tokens,
        }
        payload.update(extra)

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        headers.update(self.options.extra_headers)

        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.options.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_text = exc.read().decode("utf-8", errors="ignore")
            logger.warning("LLM API HTTP 错误 %s: %s", exc.code, error_text)
            raise
        except urllib.error.URLError as exc:
            logger.warning("LLM 请求网络错误: %s", exc.reason)
            raise
        except json.JSONDecodeError as exc:
            logger.warning("LLM 响应 JSON 解析失败: %s", exc)
            raise ValueError(f"LLM 响应解析失败: {exc}") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("LLM 响应缺少 choices/message/content: %s", body)
            raise ValueError(f"LLM 响应格式异常: {exc}") from exc

        return content.strip()
