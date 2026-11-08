"""单文件重建：只动目标文件，不误删其他来源。"""

from pathlib import Path

import yaml

from shibei.services import Shibei


def _setup(tmp_path: Path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "a.md").write_text("## A\nRedis 缓存。\n", encoding="utf-8")
    (notes / "b.md").write_text("## B\nKafka 分区。\n", encoding="utf-8")
    db = tmp_path / "db"
    db.mkdir()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "storage": {"path": str(db)},
                "sources": [str(notes)],
                "extensions": [".md"],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return cfg, notes


def test_reindex_single_file_keeps_others(tmp_path):
    cfg, notes = _setup(tmp_path)
    brain = Shibei(str(cfg))
    brain.import_all()
    before = brain.list_chunks(limit=100)["total"]
    assert before >= 2

    (notes / "a.md").write_text("## A\nRedis 集群与哨兵。\n", encoding="utf-8")
    result = brain.reindex(str(notes / "a.md"))

    assert result["files"] == 1
    items = brain.list_chunks(limit=100)["items"]
    texts = "\n".join(item["text"] for item in items)
    assert "集群" in texts
    assert "Kafka" in texts, "其他文件不应被 prune 掉"
    assert brain.list_chunks(limit=100)["total"] == before


def test_reindex_forces_even_if_unchanged(tmp_path):
    cfg, notes = _setup(tmp_path)
    brain = Shibei(str(cfg))
    brain.import_all()

    result = brain.reindex(str(notes / "a.md"))
    assert result["files"] == 1
    assert result["chunks"] >= 1


def test_reindex_rejects_empty_source(tmp_path):
    cfg, _ = _setup(tmp_path)
    brain = Shibei(str(cfg))
    try:
        brain.reindex("")
    except ValueError as exc:
        assert "source" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
