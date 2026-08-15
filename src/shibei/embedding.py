"""向量嵌入 — 轻量混合检索的可选底座（P1 向量 hybrid）。

双后端：
- provider=api  ：OpenAI-compatible /embeddings 接口（key 走 SHIBEI_LLM_API_KEY）
- provider=local：可选依赖 fastembed（本地 ONNX 推理，如 BAAI/bge-small-zh-v1.5）

设计原则（延续项目「零依赖核心路径」）：
- 默认关闭；两个后端都不可用时 Embedder.available() 返回 False，
  调用方自动降级纯 BM25，不抛异常、不阻断。
"""

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)


def _api_key() -> str:
    return os.environ.get("SHIBEI_LLM_API_KEY", "")


class Embedder:
    """向量嵌入器。构造不抛异常，可用性通过 available() 判断。"""

    def __init__(
        self,
        provider: str = "api",
        model: str = "BAAI/bge-small-zh-v1.5",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 30.0,
        batch_size: int = 16,
    ):
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.batch_size = max(1, batch_size)
        self._local_model = None  # fastembed 延迟加载

    # ---------- 可用性 ----------

    def available(self) -> bool:
        """当前配置下是否真的能产出向量（不抛异常）。"""
        if self.provider == "local":
            try:
                import fastembed  # noqa: F401
                return True
            except ImportError:
                return False
        # api 后端
        return bool(_api_key())

    def dim(self) -> int:
        """返回向量维度；不可用时返回 0。"""
        if self.provider == "local":
            try:
                from fastembed import TextEmbedding
                m = TextEmbedding(model_name=self.model)
                return int(m.model_output_dim)
            except Exception as exc:  # noqa: BLE001
                logger.warning("获取本地模型维度失败: %s", exc)
                return 0
        return 0  # api 后端维度由服务端决定，调用方按返回向量长度自适应

    # ---------- 核心 ----------

    def embed(self, texts: list[str]) -> Optional[list[list[float]]]:
        """把文本列表转成向量列表。失败返回 None（调用方降级 BM25）。

        Args:
            texts: 非空文本列表

        Returns:
            list[list[float]] 或 None
        """
        texts = [t.strip() for t in texts]
        texts = [t for t in texts if t]
        if not texts:
            return None
        try:
            if self.provider == "local":
                return self._embed_local(texts)
            return self._embed_api(texts)
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding 生成失败，降级纯 BM25: %s", exc)
            return None

    def _embed_local(self, texts: list[str]) -> list[list[float]]:
        from fastembed import TextEmbedding

        if self._local_model is None:
            self._local_model = TextEmbedding(model_name=self.model)
        vectors = []
        for batch in _batches(texts, self.batch_size):
            for v in self._local_model.embed(batch):
                vectors.append([float(x) for x in v])
        return vectors

    def _embed_api(self, texts: list[str]) -> list[list[float]]:
        key = _api_key()
        if not key:
            raise ValueError("缺少 SHIBEI_LLM_API_KEY，无法调用 embedding API")

        url = f"{self.base_url.rstrip('/')}/embeddings"
        vectors: list[list[float]] = []
        for batch in _batches(texts, self.batch_size):
            payload = {
                "model": self.model,
                "input": batch,
            }
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
            }
            request = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                error_text = exc.read().decode("utf-8", errors="ignore")
                logger.warning("embedding API HTTP 错误 %s: %s", exc.code, error_text)
                raise
            except urllib.error.URLError as exc:
                logger.warning("embedding 网络错误: %s", exc.reason)
                raise
            except json.JSONDecodeError as exc:
                logger.warning("embedding 响应 JSON 解析失败: %s", exc)
                raise ValueError(f"embedding 响应解析失败: {exc}") from exc

            try:
                items = body["data"]
                # 按 input 顺序取回（OpenAI 兼容契约）
                items.sort(key=lambda x: int(x.get("index", 0)))
                for item in items:
                    vectors.append([float(x) for x in item["embedding"]])
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                logger.warning("embedding 响应格式异常: %s", body)
                raise ValueError(f"embedding 响应格式异常: {exc}") from exc
        return vectors


def _batches(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度（纯 Python，零依赖；小语料可用）。"""
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
