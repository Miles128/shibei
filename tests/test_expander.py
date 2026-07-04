"""QueryExpander 测试 — 同义词扩展与模糊匹配"""

import json
import tempfile
from pathlib import Path

from shibei.config import Config, ExpanderConfig, StorageConfig
from shibei.expander import QueryExpander, _edit_distance, _load_synonyms
from shibei.searcher import Searcher


def _make_synonyms_file(tmpdir: str, data: dict, suffix: str = ".yaml") -> Path:
    p = Path(tmpdir) / f"synonyms{suffix}"
    if suffix == ".json":
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    else:
        import yaml
        p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


def test_edit_distance():
    """编辑距离基础计算"""
    assert _edit_distance("手机", "手机") == 0
    assert _edit_distance("手机", "手鸡") == 1
    assert _edit_distance("手机", "手提电话") == 3
    assert _edit_distance("", "abc") == 3


def test_load_synonyms_yaml():
    """YAML 同义词表加载"""
    with tempfile.TemporaryDirectory() as tmpdir:
        p = _make_synonyms_file(tmpdir, {"手机": ["移动电话", "手机"], "AI": ["人工智能"]})
        syns = _load_synonyms(p)
        assert syns["手机"] == ["手机", "移动电话"]
        assert syns["AI"] == ["AI", "人工智能"]


def test_load_synonyms_json():
    """JSON 同义词表加载"""
    with tempfile.TemporaryDirectory() as tmpdir:
        p = _make_synonyms_file(
            tmpdir, {"手机": ["移动电话", "手机"]}, suffix=".json"
        )
        syns = _load_synonyms(p)
        assert syns["手机"] == ["手机", "移动电话"]


def test_expand_synonyms():
    """同义词扩展查询"""
    expander = QueryExpander(synonyms_path=None)
    expander.synonyms = {"手机": ["手机", "移动电话"], "AI": ["AI", "人工智能"]}
    expander._dictionary = {"手机", "移动电话", "AI", "人工智能"}

    result = expander.expand("手机 AI")
    assert result["original"] == "手机 AI"
    assert "手机" in result["expanded"]
    assert "移动电话" in result["expanded"]
    assert "人工智能" in result["expanded"]
    assert result["expansions"]["手机"] == ["移动电话"]
    assert result["expansions"]["AI"] == ["人工智能"]


def test_expand_fuzzy():
    """编辑距离模糊匹配"""
    expander = QueryExpander(
        synonyms_path=None,
        fuzzy=True,
        fuzzy_max_distance=1,
        fuzzy_max_candidates=2,
    )
    expander.synonyms = {"手机": ["手机", "移动电话"]}
    expander._dictionary = {"手机", "移动电话"}

    result = expander.expand("手鸡")
    assert result["fuzzy"]["手鸡"] == ["手机"]
    assert "手机" in result["expanded"]


def test_expand_no_match_returns_original():
    """无匹配时返回原查询"""
    expander = QueryExpander(synonyms_path=None)
    expander.synonyms = {"手机": ["手机", "移动电话"]}
    expander._dictionary = {"手机", "移动电话"}

    result = expander.expand("银行风控")
    assert result["expanded"] == "银行风控"
    assert not result["expansions"]
    assert not result["fuzzy"]


def test_searcher_with_expander():
    """Searcher 集成 expander 后能通过同义词召回文档"""
    from shibei.bm25 import BM25Index

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        bm25 = BM25Index(str(db_path / "fts5.db"))
        bm25.add_batch([
            ("/ai.md", "/ai.md", "ai", "人工智能正在改变世界。", "", ""),
            ("/llm.md", "/llm.md", "ai", "大模型技术突破。", "", ""),
        ])

        synonyms_p = _make_synonyms_file(tmpdir, {"AI": ["人工智能"]})
        cfg = Config(
            storage=StorageConfig(path=str(db_path)),
            expander=ExpanderConfig(
                enabled=True,
                synonyms_path=str(synonyms_p),
            ),
        )
        searcher = Searcher(cfg)

        # 用同义词 "AI" 搜索，应召回包含 "人工智能" 的文档
        result = searcher.search("AI", limit=5)
        assert result["total"] >= 1
        texts = " ".join(r["text"] for r in result["results"])
        assert "人工智能" in texts
        assert "expansion" in result
        assert result["expansion"]["expansions"]["AI"] == ["人工智能"]


def test_searcher_with_fuzzy():
    """Searcher 集成 fuzzy 后能通过编辑距离召回文档"""
    from shibei.bm25 import BM25Index

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        bm25 = BM25Index(str(db_path / "fts5.db"))
        bm25.add_batch([
            ("/ai.md", "/ai.md", "ai", "人工智能正在改变世界。", "", ""),
        ])

        synonyms_p = _make_synonyms_file(tmpdir, {"人工智能": ["人工智能"]})
        cfg = Config(
            storage=StorageConfig(path=str(db_path)),
            expander=ExpanderConfig(
                enabled=True,
                synonyms_path=str(synonyms_p),
                fuzzy=True,
                fuzzy_max_distance=1,
            ),
        )
        searcher = Searcher(cfg)

        # 输入错别字 "人工智”，应通过 fuzzy 找到 "人工智能"
        result = searcher.search("人工智", limit=5)
        assert result["total"] >= 1
