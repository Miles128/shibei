"""配置加载器 — YAML + 环境变量合并"""

import os
from pathlib import Path
from dataclasses import dataclass, field

import yaml


@dataclass
class StorageConfig:
    path: str = "~/.shibei/db"
    search_engine: str = "bm25"  # 仅支持 bm25


@dataclass
class ChunkingConfig:
    max_chars: int = 800
    split_pattern: str = r"\n(?=## )"
    overlap: int = 0  # 相邻 chunk 重叠字符数（0=不重叠）
    # parent-child chunk：小 chunk 索引，搜索时返回 parent 上下文
    parent_window: int = 0  # parent 包含的 child 数（0=禁用 parent-child）
    # 中文句子边界（用于不切断词的切分）
    sentence_boundaries: str = "。！？!?\n"


@dataclass
class TagRule:
    pattern: str = ""
    tag: str = ""


@dataclass
class TaggingConfig:
    rules: list[TagRule] = field(default_factory=list)


DEFAULT_IGNORE_DIRS = (
    ".git",
    "node_modules",
    "target",
    "dist",
    "build",
    "__pycache__",
    ".venv",
    "venv",
    ".next",
    ".nuxt",
    "DerivedData",
    "Pods",
    "coverage",
    "vendor",
    "tauri-codegen-assets",
)


@dataclass
class Config:
    storage: StorageConfig = field(default_factory=StorageConfig)
    sources: list[str] = field(default_factory=lambda: ["~/Documents/notes"])
    extensions: list[str] = field(default_factory=lambda: [".md", ".txt", ".docx", ".xlsx", ".csv"])
    ignore_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_DIRS))
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    tagging: TaggingConfig = field(default_factory=TaggingConfig)

    @property
    def db_path_expanded(self) -> Path:
        return Path(self.storage.path).expanduser()

    @property
    def sources_expanded(self) -> list[Path]:
        return [Path(s).expanduser() for s in self.sources]


def load_config(path: str = "config.yaml") -> Config:
    """加载 YAML 配置，环境变量覆盖关键项。

    环境变量：
      SHIBEI_CONFIG       — 配置文件路径（默认 config.yaml）
      SHIBEI_SOURCES      — 覆盖数据源（逗号分隔）
    """
    config_path = os.environ.get("SHIBEI_CONFIG", path)

    raw = {}
    if Path(config_path).exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}

    cfg = Config()

    if "storage" in raw:
        cfg.storage = StorageConfig(**raw["storage"])

    # Sources
    if "sources" in raw:
        cfg.sources = raw["sources"]
    if os.environ.get("SHIBEI_SOURCES"):
        cfg.sources = os.environ["SHIBEI_SOURCES"].split(",")

    # Extensions
    if "extensions" in raw:
        cfg.extensions = raw["extensions"]

    # Ignore dirs (skip during scan)
    if "ignore_dirs" in raw:
        cfg.ignore_dirs = raw["ignore_dirs"]

    # Chunking
    if "chunking" in raw:
        cfg.chunking = ChunkingConfig(**raw["chunking"])

    # Tagging
    if "tagging" in raw and "rules" in raw["tagging"]:
        cfg.tagging.rules = [TagRule(**r) for r in raw["tagging"]["rules"]]

    return cfg
