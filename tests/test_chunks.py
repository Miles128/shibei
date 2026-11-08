"""全部片段浏览：bm25.list_chunks / services.list_chunks / sidecar 接口。"""

from pathlib import Path

from shibei.bm25 import BM25Index
from shibei.config import Config, StorageConfig
from shibei.services import Shibei


def _seed(db: Path) -> None:
    BM25Index(str(db / "fts5.db")).add_batch([
        ("/a.md", "/a.md", "ai", "片段零", "", ""),
        ("/a.md", "/a.md", "ai", "片段一", "", ""),
        ("/b.md", "/b.md", "interview", "片段二", "", ""),
        ("/b.md", "/b.md", "interview", "片段三", "", ""),
        ("/c.md", "/c.md", "ai,daily", "片段四", "", ""),
    ])


def test_list_chunks_pagination(tmp_path):
    _seed(tmp_path)
    index = BM25Index(str(tmp_path / "fts5.db"))
    assert index.count_chunks() == 5

    page = index.list_chunks(limit=2, offset=2)
    assert [item["text"] for item in page] == ["片段二", "片段三"]
    assert page[0]["source"] == "/b.md"


def test_list_chunks_filters(tmp_path):
    _seed(tmp_path)
    index = BM25Index(str(tmp_path / "fts5.db"))
    assert index.count_chunks(source="/a.md") == 2
    assert index.count_chunks(tag="ai") == 3
    assert [item["text"] for item in index.list_chunks(source="/a.md")] == [
        "片段零", "片段一"
    ]


def test_services_list_chunks_envelope(tmp_path):
    _seed(tmp_path)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(f"storage:\n  path: {tmp_path}\nsources: []\n", encoding="utf-8")
    brain = Shibei(str(cfg_file))

    data = brain.list_chunks(limit=3, offset=0)
    assert data["total"] == 5
    assert data["limit"] == 3
    assert data["offset"] == 0
    assert len(data["items"]) == 3
    assert {"_rowid", "source", "file_path", "tags", "text"} <= set(data["items"][0])
