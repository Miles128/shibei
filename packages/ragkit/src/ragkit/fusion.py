"""排名融合：RRF（名次）与加权线性（分数）。

两种融合适用的场景不同，不要二选一：

- ``rrf``            ：多路结果的分数量纲不可比（跨库、跨模型）时用，只依赖名次；
- ``weighted_fuse``  ：同一套索引内 dense/sparse 分数已归一化、需要调权重时用。
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Sequence
from typing import TypeVar

K = TypeVar("K", bound=Hashable)


def rrf_gain(rank: int, k: int = 60) -> float:
    """单路名次贡献：``1 / (k + rank)``，rank 从 1 开始。"""
    if rank < 1:
        raise ValueError("rank 从 1 开始")
    return 1.0 / (k + rank)


def rrf_scores(
    rankings: Iterable[Sequence[K]],
    k: int = 60,
    weights: Sequence[float] | None = None,
) -> dict[K, float]:
    """多路排名融合，返回 ``id -> 分数``。

    同一 id 出现在多路时分数累加；名次越靠前贡献越大。
    """
    rankings = list(rankings)
    if weights is not None and len(weights) != len(rankings):
        raise ValueError("weights 数量必须与 rankings 一致")
    scores: dict[K, float] = {}
    for index, ranking in enumerate(rankings):
        weight = 1.0 if weights is None else float(weights[index])
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + weight * rrf_gain(rank, k)
    return scores


def normalize(
    values: Iterable[float],
    method: str = "minmax",
) -> list[float]:
    """归一化到 0..1。``minmax`` 对全等序列返回全 1，避免除零。"""
    items = [float(v) for v in values]
    if not items:
        return []
    if method != "minmax":
        raise ValueError(f"unknown normalize method: {method}")
    lo, hi = min(items), max(items)
    if hi - lo < 1e-12:
        return [1.0 for _ in items]
    return [(value - lo) / (hi - lo) for value in items]


def weighted_fuse(
    dense: dict[K, float],
    sparse: dict[K, float],
    dense_weight: float = 0.5,
    sparse_weight: float = 0.5,
    normalize_scores: bool = True,
) -> dict[K, float]:
    """dense/sparse 两路加权融合；任一路缺失按 0 计。

    ``normalize_scores=True`` 时先分别做 minmax，否则假定调用方已归一化。
    """
    if normalize_scores:
        dense_ids = list(dense)
        sparse_ids = list(sparse)
        dense_norm = dict(zip(dense_ids, normalize(dense[d] for d in dense_ids)))
        sparse_norm = dict(zip(sparse_ids, normalize(sparse[s] for s in sparse_ids)))
    else:
        dense_norm, sparse_norm = dict(dense), dict(sparse)

    fused: dict[K, float] = {}
    for key in set(dense_norm) | set(sparse_norm):
        fused[key] = (
            dense_weight * dense_norm.get(key, 0.0)
            + sparse_weight * sparse_norm.get(key, 0.0)
        )
    return fused
