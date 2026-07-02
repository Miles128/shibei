"""Shibei — 中文语义知识库，给 AI Agent 用的外部大脑。

三层接口：
  CLI:    shibei search/import/list/serve
  MCP:    shibei serve  → 任何 MCP agent 可接入
  Python: from shibei import Shibei
"""

from .config import Config, load_config
from .profile import list_profiles, create_profile, delete_profile
from .bm25 import BM25Index


def __getattr__(name):
    if name == "Shibei":
        from .services import Shibei
        return Shibei
    if name == "Importer":
        from .importer import Importer
        return Importer
    if name == "Searcher":
        from .searcher import Searcher
        return Searcher
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__version__ = "0.1.0"
__all__ = [
    "Shibei",
    "Config",
    "Searcher",
    "Importer",
    "BM25Index",
    "load_config",
    "list_profiles",
    "create_profile",
    "delete_profile",
]
