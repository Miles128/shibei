"""MMR（Maximal Marginal Relevance）去重。

目标不是「选出最相关的 k 条」，而是「选出相关性高且彼此不重复的 k 条」。

两种相似度：

- 有向量时用余弦（NoteAI 的 dense_vec）；
- 没有向量时用词集合的 Jaccard（中文按字符 bigram，英文/数字按整词）。

不依赖 numpy，结果与 numpy 实现一致（浮点误差内）。
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Sequence

_ALNUM_RE = re.compile(r"[A-Za-z0-9_]+")
_CN_RE = re.compile(r"[\u4e00-\u9fff]")


def _lexical_tokens(text: str) -> set[str]:
    text = text or ""
    tokens = {token.lower() for token in _ALNUM_RE.findall(text)}
    chars = _CN_RE.findall(text)
    for index in range(len(chars) - 1):
        tokens.add(chars[index] + chars[index + 1])
    if not chars:
        return tokens
    tokens.update(chars)
    return tokens


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = len(left | right)
    return len(left & right) / union if union else 0.0


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right:
        return 0.0
    dot = 0.0
    norm_left = 0.0
    norm_right = 0.0
    for a, b in zip(left, right):
        dot += a * b
        norm_left += a * a
        norm_right += b * b
    if norm_left <= 0 or norm_right <= 0:
        return 0.0
    return dot / (math.sqrt(norm_left) * math.sqrt(norm_right))


def mmr_select(
    items: 'list[dict]',
    top_k: int,
    *,
    relevance_key: str = "score",
    vector_key: 'str | None' = None,
    text_key: str = "text",
    lambda_param: float = 0.5,
    embed: 'Callable[[str], Sequence[float]] | None' = None,
) -> 'list[dict]':
    """贪心选 k 条；``lambda_param`` 越大越偏相关，越小越偏多样。"""
    if top_k <= 0 or not items:
        return []
    if len(items) <= top_k:
        return list(items)

    vectors: 'list[Sequence[float] | None]' = []
    for item in items:
        vector = item.get(vector_key) if vector_key else None
        if vector:
            vectors.append([float(value) for value in vector])
        elif embed is not None:
            vectors.append(list(embed(str(item.get(text_key) or ""))))
        else:
            vectors.append(None)

    use_dense = all(vector is not None for vector in vectors)
    if not use_dense:
        token_sets = [_lexical_tokens(str(item.get(text_key) or "")) for item in items]

    def similarity(left: int, right: int) -> float:
        if use_dense:
            return _cosine(vectors[left] or [], vectors[right] or [])
        return _jaccard(token_sets[left], token_sets[right])

    def relevance(index: int) -> float:
        try:
            return float(items[index].get(relevance_key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    selected = [0]
    remaining = set(range(1, len(items)))
    while len(selected) < top_k and remaining:
        best_index = -1
        best_score = -math.inf
        for index in remaining:
            max_sim = max(similarity(index, chosen) for chosen in selected)
            score = lambda_param * relevance(index) - (1 - lambda_param) * max_sim
            if score > best_score:
                best_score = score
                best_index = index
        if best_index < 0:
            break
        selected.append(best_index)
        remaining.discard(best_index)

    return [items[index] for index in selected]
