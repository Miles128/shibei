"""Cross-encoder 重排（可选）。

BM25 + 向量的初筛擅长「召回」，但 top 里经常混入关键词命中、语义不相关的
chunk。这里用 fastembed 的本地 cross-encoder 对初筛结果二次打分，再取前 N。

默认关闭；fastembed 不可用或模型加载失败时原样返回初筛顺序。
"""

from __future__ import annotations

import logging
from typing import Any

from .config import RerankConfig

logger = logging.getLogger(__name__)

_MODEL_CACHE: dict[str, Any] = {}


class Reranker:
    def __init__(self, cfg: RerankConfig):
        self.cfg = cfg

    def available(self) -> bool:
        try:
            import fastembed  # noqa: F401
            return True
        except ImportError:
            return False

    def _model(self):
        model = _MODEL_CACHE.get(self.cfg.model)
        if model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            model = TextCrossEncoder(model_name=self.cfg.model)
            _MODEL_CACHE[self.cfg.model] = model
        return model

    def rerank(self, query: str, results: list[dict], top_k: int) -> list[dict]:
        """对前 top_k 条结果重排；失败时返回原始顺序。"""
        if not results:
            return results
        head = results[:top_k]
        tail = results[top_k:]
        try:
            scores = list(self._model().rerank(
                query,
                [r.get("text", "") for r in head],
                batch_size=self.cfg.batch_size,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning("rerank 失败，保留初筛顺序: %s", exc)
            return results

        if len(scores) != len(head):
            logger.warning("rerank 分数数量不匹配（%s vs %s），保留初筛顺序", len(scores), len(head))
            return results

        for item, score in zip(head, scores):
            item["rerank_score"] = round(float(score), 4)
        head.sort(key=lambda r: r.get("rerank_score", 0.0), reverse=True)
        return head + tail
