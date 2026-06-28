"""嵌入模型管理 — 懒加载 + 缓存"""

from typing import Optional
from sentence_transformers import SentenceTransformer
from chromadb.utils import embedding_functions

from .config import EmbeddingConfig

_model: Optional[SentenceTransformer] = None
_ef: Optional[embedding_functions.SentenceTransformerEmbeddingFunction] = None


def get_embedding_model(cfg: EmbeddingConfig):
    """获取 SentenceTransformer 模型（懒加载，全局单例）"""
    global _model
    if _model is None:
        _model = SentenceTransformer(cfg.model, device=cfg.device)
    return _model


def get_embedding_function(cfg: EmbeddingConfig):
    """获取 ChromaDB embedding function（供 collection 用）"""
    global _ef
    if _ef is None:
        _ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=cfg.model, device=cfg.device
        )
    return _ef


def reset_embedding_function():
    """重置缓存（切换模型时调用）"""
    global _model, _ef
    _model = None
    _ef = None
