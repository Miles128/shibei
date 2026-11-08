"""缓存键稳定性。"""

from ragkit.cache import make_cache_key


def test_same_parts_same_key():
    assert make_cache_key("a", ["x", "y"], 1) == make_cache_key("a", ["x", "y"], 1)


def test_order_matters_for_lists_but_not_dicts():
    assert make_cache_key(["a", "b"]) != make_cache_key(["b", "a"])
    assert make_cache_key({"a": 1, "b": 2}) == make_cache_key({"b": 2, "a": 1})


def test_none_parts_are_ignored():
    assert make_cache_key("q", None) == make_cache_key("q")


def test_returns_hex_digest():
    key = make_cache_key("查询")
    assert len(key) == 40
    assert all(char in "0123456789abcdef" for char in key)
