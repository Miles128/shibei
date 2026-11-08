"""把召回结果写入 golden.yaml（与 scripts/eval_retrieval.py 同格式）。"""

from pathlib import Path

import yaml

from shibei.services import Shibei


def _brain(tmp_path: Path) -> Shibei:
    db = tmp_path / "db"
    db.mkdir()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"storage:\n  path: {db}\nsources: []\n", encoding="utf-8")
    return Shibei(str(cfg))


def test_save_eval_sample_appends_and_dedupes(tmp_path):
    brain = _brain(tmp_path)
    target = tmp_path / "golden.yaml"

    first = brain.save_eval_sample("Redis 缓存", ["a.md"], path=str(target))
    assert first["updated"] is False
    assert first["queries"] == 1

    second = brain.save_eval_sample("Redis 缓存", ["b.md"], path=str(target))
    assert second["updated"] is True
    assert second["queries"] == 1

    third = brain.save_eval_sample("Kafka 分区", [], path=str(target))
    assert third["queries"] == 2

    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["queries"] == [
        {"query": "Redis 缓存", "expect": ["b.md"]},
        {"query": "Kafka 分区", "expect": []},
    ]


def test_empty_query_rejected(tmp_path):
    brain = _brain(tmp_path)
    try:
        brain.save_eval_sample("  ", [], path=str(tmp_path / "g.yaml"))
    except ValueError as exc:
        assert "query" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
