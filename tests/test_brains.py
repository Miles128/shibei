"""多库注册表测试。"""

from pathlib import Path

import yaml

from shibei.bm25 import BM25Index
from shibei.brains import (
    Brains,
    load_brains,
    register_brain,
    register_remote,
    save_brains,
    unregister_brain,
)


def make_brain(root: Path, name: str, text: str) -> Path:
    base = root / name
    db = base / "db"
    db.mkdir(parents=True, exist_ok=True)
    BM25Index(str(db / "fts5.db")).add_batch([
        (f"/{name}.md", f"/{name}.md", "", text, "", ""),
    ])
    cfg = base / "config.yaml"
    cfg.write_text(f"storage:\n  path: {db}\nsources: []\n", encoding="utf-8")
    return cfg


def test_registry_roundtrip(tmp_path):
    registry = tmp_path / "brains.yaml"
    cfg = make_brain(tmp_path, "work", "工作笔记")

    register_brain("work", str(cfg), "工作", path=registry)
    entries = load_brains(registry)
    assert list(entries) == ["work"]
    assert entries["work"]["config"] == str(cfg.resolve())
    assert entries["work"]["description"] == "工作"

    assert unregister_brain("work", path=registry) is True
    assert load_brains(registry) == {}
    assert unregister_brain("work", path=registry) is False


def test_save_brains_writes_yaml(tmp_path):
    registry = tmp_path / "brains.yaml"
    save_brains({"a": {"config": "/tmp/a.yaml"}}, path=registry)
    raw = yaml.safe_load(registry.read_text(encoding="utf-8"))
    assert raw == {"a": {"config": "/tmp/a.yaml"}}


def test_brains_default_is_launching_config(tmp_path):
    cfg = make_brain(tmp_path, "work", "工作笔记")
    registry = tmp_path / "brains.yaml"
    brains = Brains(str(cfg), registry=registry)

    assert brains.names() == ["default"]
    assert brains.default_name == "default"
    assert brains.path_of("default") == str(cfg.resolve())
    # 空注册表不落盘
    assert not registry.exists()


def test_brains_lazy_instance_and_locks(tmp_path):
    cfg = make_brain(tmp_path, "work", "工作笔记")
    brains = Brains(str(cfg), registry=tmp_path / "brains.yaml")

    first = brains.get("default")
    second = brains.get("default")
    assert first is second
    assert brains.lock_for("default") is brains.lock_for("default")
    assert brains.lock_for("default") is not brains.lock_for("other")


def test_brains_hot_add_without_restart(tmp_path):
    cfg_a = make_brain(tmp_path, "a", "库 A")
    cfg_b = make_brain(tmp_path, "b", "库 B")
    registry = tmp_path / "brains.yaml"
    brains = Brains(str(cfg_a), registry=registry)
    assert brains.names() == ["default"]

    register_brain("work", str(cfg_b), path=registry)
    assert brains.names() == ["default", "work"]
    assert brains.get("work").list_chunks(limit=0)["total"] == 1

    unregister_brain("work", path=registry)
    assert brains.names() == ["default"]


def test_launching_config_registered_under_name_is_not_aliased(tmp_path):
    """启动配置已注册成 work 时，不该再额外生成 default 别名（否则联邦会重复计数）。"""
    cfg = make_brain(tmp_path, "work", "工作笔记")
    registry = tmp_path / "brains.yaml"
    register_brain("work", str(cfg), path=registry)

    brains = Brains(str(cfg), registry=registry)
    assert brains.names() == ["work"]
    assert brains.default_name == "work"
    assert brains.get("default") is brains.get("work")
    assert brains.path_of("default") == str(cfg.resolve())


def test_unknown_brain_raises(tmp_path):
    cfg = make_brain(tmp_path, "a", "库 A")
    brains = Brains(str(cfg), registry=tmp_path / "brains.yaml")
    try:
        brains.get("nope")
    except KeyError as exc:
        assert "nope" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected KeyError")


def test_describe_reports_chunks(tmp_path):
    cfg = make_brain(tmp_path, "a", "库 A")
    brains = Brains(str(cfg), registry=tmp_path / "brains.yaml")
    described = brains.describe()
    assert described[0]["name"] == "default"
    assert described[0]["chunks"] == 1
    assert described[0]["default"] is True


def test_register_remote_entry(tmp_path):
    cfg = make_brain(tmp_path, "work", "工作笔记")
    registry = tmp_path / "brains.yaml"
    register_remote(
        "noteai", "http://127.0.0.1:9", token="t", brain="default", path=registry
    )

    brains = Brains(str(cfg), registry=registry)
    assert "noteai" in brains.names()
    assert brains.path_of("noteai") is None

    from shibei.remote import RemoteBrain

    assert isinstance(brains.get("noteai"), RemoteBrain)

    described = {item["name"]: item for item in brains.describe(with_counts=False)}
    assert described["noteai"]["remote"] is True
    assert described["noteai"]["url"] == "http://127.0.0.1:9"
