"""ragkit — shibei 与 NoteAI 共用的召回原语。

这里只放「无状态、纯函数、零重依赖」的东西：

- ``fusion``   ：RRF / 加权融合 / 归一化
- ``coverage`` ：查询词覆盖率门槛
- ``mmr``      ：最大边际相关性去重
- ``chunking`` ：结构化 Markdown 分块
- ``cache``    ：稳定的查询缓存键

存储、索引生命周期、LLM 增强（HyDE）不在本包内——它们属于各自产品的
运行模型，强行统一会让两边都退化。
"""

from .fusion import normalize, rrf_gain, rrf_scores, weighted_fuse
from .coverage import build_groups, coverage_ratio, meets_coverage
from .mmr import mmr_select
from .chunking import chunk_markdown
from .cache import make_cache_key

__all__ = [
    "normalize",
    "rrf_gain",
    "rrf_scores",
    "weighted_fuse",
    "build_groups",
    "coverage_ratio",
    "meets_coverage",
    "mmr_select",
    "chunk_markdown",
    "make_cache_key",
]
