"""覆盖率门槛。"""

from ragkit.coverage import build_groups, coverage_ratio, meets_coverage


def test_build_groups_merges_synonyms():
    groups = build_groups(
        ["风控"],
        synonyms={"风控": ["风险控制", "风控策略"]},
        fuzzy={"风控": ["风控"]},
    )
    assert groups == [["风控", "风险控制", "风控策略"]]


def test_coverage_ratio_weights_by_first_term_length():
    groups = [["Redis", "redis缓存"], ["风控"]]
    text = "Redis 的持久化"
    # 只有第一组命中：权重按 "Redis"(5-1=4) 计，第二组 1 未命中
    assert 0 < coverage_ratio(text, groups) < 1


def test_coverage_ratio_full_match_and_empty_groups():
    groups = [["Redis"], ["持久化"]]
    assert coverage_ratio("Redis 持久化", groups) == 1.0
    assert coverage_ratio("任何内容", []) == 1.0


def test_single_char_terms_have_no_weight():
    # 单字权重为 0，不应凭空拉低覆盖率
    assert coverage_ratio("风", [["风"]]) == 1.0


def test_meets_coverage_threshold():
    groups = [["Redis"], ["持久化"]]
    assert meets_coverage("Redis 持久化", groups, 0.5)
    assert not meets_coverage("Redis 简介", groups, 0.9)
