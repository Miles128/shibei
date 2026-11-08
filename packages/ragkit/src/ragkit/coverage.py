"""查询词覆盖率 — 过滤「只命中一个泛词」的误召回。

权重按组内第一个词的长度算（同义词组以原始查询词为准），
这样 ``interview`` 这类长英文同义词不会把泛词的分量抬高。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence


def build_groups(
    terms: Iterable[str],
    synonyms: 'dict[str, list[str]] | None' = None,
    fuzzy: 'dict[str, list[str]] | None' = None,
) -> list[list[str]]:
    """把查询词 + 同义词/模糊候选归组；组内任一命中即算覆盖。"""
    groups: dict[str, list[str]] = {}
    for term in terms:
        if term:
            groups.setdefault(term, [term])
    for source in (synonyms or {}, fuzzy or {}):
        for word, alternatives in source.items():
            bucket = groups.setdefault(word, [word])
            for alt in alternatives:
                if alt and alt not in bucket:
                    bucket.append(alt)
    return [list(dict.fromkeys(values)) for values in groups.values()]


def coverage_ratio(
    text: str,
    groups: Sequence[Sequence[str]],
) -> float:
    """返回 0..1 的加权覆盖率；``groups`` 为空时视为完全覆盖。"""
    if not groups:
        return 1.0
    lowered = (text or "").lower()
    total = 0
    matched = 0
    for group in groups:
        alternatives = [item for item in group if item]
        if not alternatives:
            continue
        weight = max(len(alternatives[0]) - 1, 0)
        if weight == 0:
            continue
        total += weight
        if any(item.lower() in lowered for item in alternatives):
            matched += weight
    return matched / total if total else 1.0


def meets_coverage(
    text: str,
    groups: Sequence[Sequence[str]],
    min_ratio: float = 0.5,
) -> bool:
    return coverage_ratio(text, groups) >= min_ratio
