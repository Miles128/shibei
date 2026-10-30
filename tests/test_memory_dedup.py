"""在线去重决策解析测试。"""

from shibei.memory_dedup import parse_decisions


CANDIDATES = [
    [{"id": "old-1", "fact": "用户喜欢 Python", "score": 3.0}],
    [],
]


def test_parse_store_update_skip():
    raw = """[
      {"index": 0, "action": "update", "target_id": "old-1"},
      {"index": 1, "action": "store", "target_id": ""}
    ]"""
    decisions = parse_decisions(raw, 2, CANDIDATES)
    assert decisions is not None
    assert decisions[0]["action"] == "update"
    assert decisions[0]["target_id"] == "old-1"
    assert decisions[1]["action"] == "store"


def test_invalid_target_falls_back_to_store():
    raw = '[{"index": 0, "action": "update", "target_id": "nope"}]'
    decisions = parse_decisions(raw, 1, CANDIDATES)
    assert decisions is not None
    assert decisions[0] == {"action": "store", "target_id": ""}


def test_missing_entries_and_bad_json():
    decisions = parse_decisions('[{"index": 0, "action": "skip"}]', 2, CANDIDATES)
    assert decisions is not None
    assert decisions[1]["action"] == "store"
    assert parse_decisions("not json", 1, CANDIDATES) is None
