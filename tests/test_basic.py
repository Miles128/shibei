"""Shibei 基础测试"""

from shibei.config import load_config


def test_load_default_config():
    """默认配置可加载"""
    cfg = load_config("nonexistent.yaml")
    assert cfg.storage.path == "~/.shibei/db"
    assert cfg.storage.search_engine == "bm25"


def test_load_custom_config():
    """自定义配置可加载"""
    cfg = load_config("config.yaml")
    assert cfg.storage.path == "~/.shibei/db"
    assert len(cfg.sources) == 2
    assert len(cfg.tagging.rules) >= 4


def test_tagger():
    """标签推断"""
    from shibei.tagger import Tagger
    from shibei.config import TaggingConfig, TagRule

    cfg = TaggingConfig(rules=[
        TagRule(pattern="(面试|复盘)", tag="interview"),
        TagRule(pattern="(AI|大模型)", tag="ai"),
        TagRule(pattern=".*", tag="misc"),
    ])
    tagger = Tagger(cfg)

    from pathlib import Path
    tags = tagger.infer(Path("/home/面试/AI产品经理面试准备.md"), "大模型面试题")
    assert "interview" in tags
    assert "ai" in tags
    assert "misc" in tags
