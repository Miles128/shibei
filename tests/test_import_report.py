"""导入诊断报告：逐文件状态、失败原因、chunk 数。"""

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
            }
        ),
        encoding="utf-8",
    )
    return cfg, notes, db


def test_report_records_ok_and_failed(tmp_path, monkeypatch):
    cfg, notes, db = _setup(tmp_path)
    bad = notes / "b.md"

    import shibei.importer as importer

    original = importer.parse_file

    def fake_parse(path):
        if Path(path).name == "b.md":
            raise ValueError("解码失败")
        return original(path)

    monkeypatch.setattr(importer, "parse_file", fake_parse)

    brain = Shibei(str(cfg))
    result = brain.import_all()
    assert result["failed"] == 1

    report = brain.import_report()
    statuses = {
        Path(item["path"]).name: item["status"] for item in report["files"]
    }
    assert statuses["a.md"] == "ok"
    assert statuses["b.md"] == "failed"
    assert report["summary"]["failed"] == 1

    failed = next(item for item in report["files"] if item["status"] == "failed")
    assert "解码失败" in failed["error"]
    assert (db / "import_report.json").exists()


def test_second_import_marks_unchanged_as_skipped(tmp_path):
    cfg, _, _ = _setup(tmp_path)
    brain = Shibei(str(cfg))
    brain.import_all()

    brain.import_all()
    report = brain.import_report()
    statuses = [
        item["status"] for item in report["files"] if item["status"] == "skipped"
    ]
    assert len(statuses) >= 2


def test_empty_report_when_no_import(tmp_path):
    cfg, _, _ = _setup(tmp_path)
    brain = Shibei(str(cfg))
    report = brain.import_report()
    assert report["files"] == []
    assert report["summary"] == {}
