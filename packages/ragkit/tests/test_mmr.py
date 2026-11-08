"""MMR 去重。"""

from ragkit.mmr import mmr_select


def _items(texts, scores):
    return [
        {"text": text, "score": score}
        for text, score in zip(texts, scores)
    ]


def test_returns_input_when_fewer_than_k():
    items = _items(["a", "b"], [1.0, 0.9])
    assert mmr_select(items, 5) == items


def test_lexical_mmr_prefers_diversity():
    items = _items(
        [
            "Redis 缓存穿透布隆过滤器",
            "Redis 缓存穿透布隆过滤器方案",
            "Kafka 分区与消费者组",
        ],
        [0.9, 0.88, 0.6],
    )
    picked = mmr_select(items, 2, lambda_param=0.5)
    assert len(picked) == 2
    assert picked[0] is items[0]
    # 与第一条高度重复的 items[1] 应被跳过
    assert picked[1] is items[2]


def test_dense_mmr_uses_vectors():
    items = [
        {"text": "a", "score": 1.0, "vec": [1.0, 0.0]},
        {"text": "b", "score": 0.9, "vec": [0.99, 0.01]},
        {"text": "c", "score": 0.5, "vec": [0.0, 1.0]},
    ]
    picked = mmr_select(items, 2, vector_key="vec")
    assert [item["text"] for item in picked] == ["a", "c"]


def test_embed_fallback_when_no_vectors():
    items = [
        {"text": "Redis 缓存", "score": 1.0},
        {"text": "Redis 缓存", "score": 0.9},
        {"text": "Kafka 分区", "score": 0.4},
    ]
    picked = mmr_select(items, 2, embed=lambda text: [len(text) * 1.0, 1.0])
    assert len(picked) == 2


def test_empty_and_zero_k():
    assert mmr_select([], 3) == []
    assert mmr_select(_items(["a"], [1.0]), 0) == []
