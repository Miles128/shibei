"""融合与归一化。"""

from ragkit.fusion import normalize, rrf_gain, rrf_scores, weighted_fuse


def test_rrf_gain_prefers_top_rank():
    assert rrf_gain(1) > rrf_gain(2) > rrf_gain(10)
    assert rrf_gain(1, k=0) == 1.0


def test_rrf_scores_accumulates_across_lists():
    scores = rrf_scores(
        [["a", "b", "c"], ["b", "a"]],
        k=60,
    )
    # a: 1/61 + 1/62 ; b: 1/62 + 1/61 ; c: 1/63
    assert scores["a"] == scores["b"]
    assert scores["a"] > scores["c"]


def test_rrf_scores_supports_weights():
    scores = rrf_scores([["a"], ["b"]], k=0, weights=[2.0, 1.0])
    assert scores["a"] == 2.0
    assert scores["b"] == 1.0


def test_normalize_minmax_and_flat():
    assert normalize([1, 2, 3]) == [0.0, 0.5, 1.0]
    assert normalize([5, 5, 5]) == [1.0, 1.0, 1.0]
    assert normalize([]) == []


def test_weighted_fuse_handles_missing_side():
    fused = weighted_fuse(
        {"a": 1.0, "b": 0.0},
        {"b": 1.0, "c": 0.0},
        dense_weight=0.5,
        sparse_weight=0.5,
    )
    assert set(fused) == {"a", "b", "c"}
    # a/b 各在一路拿到满分，c 两路都是最低分
    assert fused["a"] == fused["b"] > fused["c"]


def test_weighted_fuse_without_normalization():
    fused = weighted_fuse(
        {"a": 0.8},
        {"a": 0.2},
        dense_weight=0.7,
        sparse_weight=0.3,
        normalize_scores=False,
    )
    assert abs(fused["a"] - (0.7 * 0.8 + 0.3 * 0.2)) < 1e-9
