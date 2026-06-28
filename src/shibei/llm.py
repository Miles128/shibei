"""LLM 客户端 — 支持 OpenAI-compatible API（DeepSeek、OpenAI 等）。

默认不启用（config.llm.enabled = false）。启用后可用于：
- Dreaming 洞察增强
- 查询改写
- 事实提取 / 记忆过滤
"""

import json
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant for a Chinese personal knowledge base."


@dataclass
class LLMConfig:
    enabled: bool = False
    provider: str = "openai-compatible"  # "openai" | "openai-compatible"
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    temperature: float = 0.3
    max_tokens: int = 1024
    timeout: float = 30.0
    system_prompt: str = DEFAULT_SYSTEM_PROMPT


class LLMClient:
    """统一的 LLM 调用客户端。"""

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    @property
    def ready(self) -> bool:
        """配置是否真正可用：开启且已填写 api_key。"""
        return self.cfg.enabled and bool(self.cfg.api_key.strip())

    def _chat_url(self) -> str:
        base = self.cfg.base_url.rstrip("/")
        return f"{base}/chat/completions"

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            h["Authorization"] = f"Bearer {self.cfg.api_key}"
        return h

    def chat(self, user_prompt: str, system_prompt: Optional[str] = None) -> str:
        """非流式调用，返回完整文本。"""
        if not self.ready:
            raise RuntimeError("LLM is not enabled or not configured")

        payload = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt or self.cfg.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
            "stream": False,
        }

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._chat_url(),
            data=data,
            headers=self._headers(),
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return body["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            logger.error("LLM HTTP %d: %s", e.code, body[:200])
            raise RuntimeError(f"LLM HTTP {e.code}: {body}") from e
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            logger.error("LLM request failed: %s", e)
            raise RuntimeError(f"LLM request failed: {e}") from e

    def chat_stream(self, user_prompt: str, system_prompt: Optional[str] = None) -> Iterator[str]:
        """流式调用，返回 token 迭代器。"""
        if not self.ready:
            raise RuntimeError("LLM is not enabled or not configured")

        payload = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt or self.cfg.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
            "stream": True,
        }

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._chat_url(),
            data=data,
            headers=self._headers(),
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
            for line in resp:
                line = line.decode("utf-8", errors="ignore").strip()
                if not line.startswith("data: "):
                    continue
                chunk = line[6:]
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                    delta = obj["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
                except Exception:
                    continue

    # ---------- 业务封装 ----------

    def enhance_insights(self, chunks: list[dict], top_keywords: list[str]) -> list[str]:
        """基于文档片段生成自然语言洞察。"""
        text = "\n---\n".join(
            f"[{i+1}] {c.get('text', c.get('fact', ''))}"
            for i, c in enumerate(chunks[:8])
        )
        prompt = f"""以下是知识库中的相关片段：

{text}

高频关键词：{', '.join(top_keywords[:10])}

请基于以上内容，输出 3-5 条简洁的中文洞察。每条洞察应指出跨文档的隐藏联系、重复主题或值得注意的趋势。不要复述片段原文，要给出更高层次的总结。

请只输出 JSON 数组格式，例如：
["洞察1","洞察2","洞察3"]"""

        raw = self.chat(prompt)
        return self._extract_json_list(raw)

    def rewrite_query(self, query: str) -> str:
        """把口语查询改写成适合检索的关键词串。"""
        prompt = f"""将以下用户查询改写为更适合知识库检索的关键词串。
保留所有关键实体，补充同义词，去掉口语化修饰，用空格分隔。

用户查询：{query}

请只输出改写后的查询字符串，不要解释。"""

        return self.chat(prompt)

    def extract_facts(self, text: str) -> list[str]:
        """从对话/文本中提取值得记忆的事实。"""
        prompt = f"""从以下对话中抽取所有值得长期记忆的客观事实（用户偏好、背景信息、关键决策等）。
只输出事实，每条事实独立一行，不要输出无关内容。

文本：
{text}

请只输出 JSON 数组格式，例如：
["事实1","事实2"]"""

        raw = self.chat(prompt)
        return self._extract_json_list(raw)

    def summarize(self, text: str, max_length: int = 100) -> str:
        """把一段文本压缩成一句话摘要。"""
        prompt = f"""请将以下文本概括为一句话中文摘要，保留核心信息，不超过 {max_length} 个字符。
不要输出解释、引号或前缀。

文本：
{text}"""
        return self.chat(prompt)

    @staticmethod
    def _extract_json_list(text: str) -> list[str]:
        """从 LLM 输出中提取 JSON 字符串数组，失败则按行分割。"""
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        try:
            obj = json.loads(text)
            if isinstance(obj, list):
                return [str(x) for x in obj if x]
        except Exception:
            pass
        return [line.strip("- *") for line in text.splitlines() if line.strip()]


def client_from_config(cfg) -> LLMClient:
    """从 Shibei 配置创建 LLMClient。"""
    if not hasattr(cfg, "llm"):
        return LLMClient(LLMConfig(enabled=False))

    llm_cfg = cfg.llm
    return LLMClient(
        LLMConfig(
            enabled=getattr(llm_cfg, "enabled", False),
            provider=getattr(llm_cfg, "provider", "openai-compatible"),
            model=getattr(llm_cfg, "model", "deepseek-chat"),
            base_url=getattr(llm_cfg, "base_url", "https://api.deepseek.com/v1"),
            api_key=getattr(llm_cfg, "api_key", ""),
            temperature=getattr(llm_cfg, "temperature", 0.3),
            max_tokens=getattr(llm_cfg, "max_tokens", 1024),
            timeout=getattr(llm_cfg, "timeout", 30.0),
            system_prompt=getattr(
                llm_cfg, "system_prompt", DEFAULT_SYSTEM_PROMPT
            ),
        )
    )
