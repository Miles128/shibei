"""配置加载器 — YAML + 环境变量合并"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import yaml


@dataclass
class ChromaConfig:
    path: str = "~/.shibei/db"
    collection: str = "knowledge_base"
    search_engine: str = "bm25"  # "bm25" | "vector" | "hybrid"


@dataclass
class EmbeddingConfig:
    model: str = "moka-ai/m3e-base"
    device: str = "cpu"
    hf_endpoint: Optional[str] = "https://hf-mirror.com"


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
class SummarizeConfig:
    enabled: bool = True  # 默认开启：为每个 chunk 生成一句话摘要，增强语义召回
    max_length: int = 100  # 摘要最大字符数
    # 为空则使用 config.llm.model；可单独指定摘要模型
    model: Optional[str] = None


@dataclass
class TagRule:
    pattern: str = ""
    tag: str = ""


@dataclass
class TaggingConfig:
    rules: list[TagRule] = field(default_factory=list)


@dataclass
class LLMConfig:
    enabled: bool = False
    provider: str = "openai-compatible"  # openai | openai-compatible
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    temperature: float = 0.3
    max_tokens: int = 1024
    timeout: float = 30.0
    system_prompt: str = "You are a helpful assistant for a Chinese personal knowledge base."


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
    chroma: ChromaConfig = field(default_factory=ChromaConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    sources: list[str] = field(default_factory=lambda: ["~/Documents/notes"])
    extensions: list[str] = field(default_factory=lambda: [".md", ".txt", ".docx", ".xlsx", ".csv"])
    ignore_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_DIRS))
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    summarize: SummarizeConfig = field(default_factory=SummarizeConfig)
    tagging: TaggingConfig = field(default_factory=TaggingConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)

    @property
    def chroma_path_expanded(self) -> Path:
        return Path(self.chroma.path).expanduser()

    @property
    def sources_expanded(self) -> list[Path]:
        return [Path(s).expanduser() for s in self.sources]


def load_config(path: str = "config.yaml") -> Config:
    """加载 YAML 配置，环境变量覆盖关键项。

    环境变量：
      SHIBEI_CONFIG       — 配置文件路径（默认 config.yaml）
      SHIBEI_MODEL        — 覆盖嵌入模型
      SHIBEI_SOURCES      — 覆盖数据源（逗号分隔）
      SHIBEI_LLM_API_KEY  — 覆盖 LLM API Key（避免明文写入配置文件）
      HF_ENDPOINT         — HuggingFace 镜像
    """
    config_path = os.environ.get("SHIBEI_CONFIG", path)

    raw = {}
    if Path(config_path).exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}

    cfg = Config()

    # Chroma
    if "chroma" in raw:
        cfg.chroma = ChromaConfig(**raw["chroma"])

    # Embedding
    if "embedding" in raw:
        cfg.embedding = EmbeddingConfig(**raw["embedding"])
    if os.environ.get("SHIBEI_MODEL"):
        cfg.embedding.model = os.environ["SHIBEI_MODEL"]

    # HF endpoint: config > env > default
    if os.environ.get("HF_ENDPOINT"):
        cfg.embedding.hf_endpoint = os.environ["HF_ENDPOINT"]
    if cfg.embedding.hf_endpoint:
        os.environ.setdefault("HF_ENDPOINT", cfg.embedding.hf_endpoint)

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

    # Summarize
    if "summarize" in raw:
        cfg.summarize = SummarizeConfig(**raw["summarize"])

    # Tagging
    if "tagging" in raw and "rules" in raw["tagging"]:
        cfg.tagging.rules = [TagRule(**r) for r in raw["tagging"]["rules"]]

    # LLM
    if "llm" in raw:
        cfg.llm = LLMConfig(**raw["llm"])
    if os.environ.get("SHIBEI_LLM_API_KEY"):
        cfg.llm.api_key = os.environ["SHIBEI_LLM_API_KEY"]

    return cfg
