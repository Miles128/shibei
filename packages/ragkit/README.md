# ragkit

shibei 与 NoteAI 共用的召回原语。**零依赖、纯函数、无状态。**

| 模块 | 内容 |
|---|---|
| `fusion` | `rrf_gain` / `rrf_scores`（名次融合）、`weighted_fuse`（分数融合）、`normalize` |
| `coverage` | `build_groups` / `coverage_ratio` / `meets_coverage`（查询词覆盖门槛） |
| `mmr` | `mmr_select`（有向量用余弦，无向量用词集合 Jaccard） |
| `chunking` | `chunk_markdown`（标题层级 + 代码块/表格原子化 + overlap） |
| `cache` | `make_cache_key`（稳定 sha1 键） |

## 不放在这里的东西

存储引擎（FTS5 / zvec）、索引生命周期（manifest/lease）、LLM 增强（HyDE）、
上下文扩展。它们的差异是产品决策，不是重复实现。

## 本地开发

```bash
uv pip install -e packages/ragkit
pytest packages/ragkit/tests
```
