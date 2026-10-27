"""Cross-encoder 重排测试（不加载真实模型）。"""

from shibei.config import RerankConfig
from shibei import rerank as rerank_mod


def test_rerank_reorders_by_score(monkeypatch):
    class FakeModel:
        def rerank(self, query, documents, batch_size=64, **kwargs):
            # 第二个文档更相关
            return [0.1, 0.9]

    cfg = RerankConfig(enabled=True, model="fake")
    monkeypatch.setitem(rerank_mod._MODEL_CACHE, "fake", FakeModel())
    results = [{"text": "a"}, {"text": "b"}]
    out = rerank_mod.Reranker(cfg).rerank("q", results, top_k=2)
    assert [r["text"] for r in out] == ["b", "a"]
    assert out[0]["rerank_score"] == 0.9


def test_rerank_failure_keeps_order(monkeypatch):
    class BadModel:
        def rerank(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setitem(rerank_mod._MODEL_CACHE, "bad", BadModel())
    cfg = RerankConfig(enabled=True, model="bad")
    results = [{"text": "a"}, {"text": "b"}]
    out = rerank_mod.Reranker(cfg).rerank("q", results, top_k=2)
    assert out == results
