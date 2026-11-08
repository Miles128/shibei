"""Shibei 基础测试"""

from shibei.config import load_config


def test_load_default_config():
    """默认配置可加载"""
    cfg = load_config("nonexistent.yaml")
    assert cfg.storage.path == "~/.shibei/db"
    assert cfg.storage.search_engine == "bm25"


def test_load_custom_config(tmp_path):
    """自定义配置可加载（不依赖仓库根目录 config.yaml）"""
    config_path = tmp_path / "custom_config.yaml"
    config_path.write_text(
        """
storage:
  path: ~/.shibei/db
sources:
  - ~/Documents/notes
  - ~/Documents/articles
tagging:
  rules:
    - pattern: "(面试|复盘)"
      tag: interview
    - pattern: "(AI|大模型)"
      tag: ai
    - pattern: "(Redis|Kafka)"
      tag: backend
    - pattern: ".*"
      tag: note
""",
        encoding="utf-8",
    )
    cfg = load_config(str(config_path))
    assert cfg.storage.path == "~/.shibei/db"
    assert len(cfg.sources) == 2
    assert len(cfg.tagging.rules) >= 4


def test_explicit_path_wins_over_env(tmp_path, monkeypatch):
    """多库 sidecar 前提：显式 path 不能被 SHIBEI_CONFIG 覆盖。"""
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text("storage:\n  path: /tmp/a\nsources: []\n", encoding="utf-8")
    b.write_text("storage:\n  path: /tmp/b\nsources: []\n", encoding="utf-8")
    monkeypatch.setenv("SHIBEI_CONFIG", str(a))

    assert load_config(str(a)).storage.path == "/tmp/a"
    assert load_config(str(b)).storage.path == "/tmp/b"
    # 未显式传 path 时才回落到环境变量
    assert load_config().storage.path == "/tmp/a"


def test_tagger_regex():
    """regex 标签推断（兼容旧配置）"""
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


def test_tagger_path_rules():
    """路径 glob 标签推断"""
    from shibei.tagger import Tagger
    from shibei.config import TaggingConfig, PathTagRule

    cfg = TaggingConfig(path_rules=[
        PathTagRule(pattern="面试/HSBC/*.md", tags="interview,hsbc"),
        PathTagRule(pattern="面试/*.md", tags="interview"),
    ])
    tagger = Tagger(cfg)

    from pathlib import Path
    assert tagger.infer(Path("/notes/面试/HSBC/prep.md")) == ["interview", "hsbc"]
    assert tagger.infer(Path("/notes/面试/algo.md")) == ["interview"]
    assert tagger.infer(Path("/notes/other.md")) == []
