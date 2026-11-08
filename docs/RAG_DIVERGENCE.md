# shibei 与 NoteAI 的召回分叉：根因、结论与整合

两个项目都做 RAG 召回，但演化出了不同的默认值。这份文档记录**为什么分叉**、
**每项谁更好**、以及**已经整合了什么、故意没整合什么**。

## 一、分叉的根因

> 先纠正一个说法：这些默认值**不是任何人拍板的「产品决策」**，而是两个项目在不同起点上
> 各自演化出的局部最优。本文档的目的就是把这些隐式默认值摊开，方便之后显式选择。

不是「一个追求快、一个追求稳」这么简单，是四个约束不同：

| 约束 | shibei | NoteAI |
|---|---|---|
| 输入形态 | 任意文档：md / txt / docx / xlsx / csv | 只有笔记：带 frontmatter 的 Markdown |
| 查询形态 | Agent 改写后的**关键词**（MCP 契约要求先改写） | 用户**自然语言**提问 |
| 写入模型 | 用户显式 `import`，同步完成 | 持续 ingest（watcher + pipeline + 断点续传） |
| 查询预算 | ~10ms，不能加载模型 | 300ms–1s 可接受，模型常驻 |

由此推出：

- shibei 必须格式无关 → 通用字符/句子分块，不能假设标题结构；
- shibei 查询是关键词 → 覆盖率门槛（coverage gate）有效且便宜；
- NoteAI 查询是问句 → 覆盖率门槛会误杀，必须靠 HyDE / MMR / rerank；
- NoteAI 写入是常态 → 必须有 manifest、lease、幂等重建；
- shibei 查询不能加载模型 → 重排/向量默认关闭，按需开启。

**结论：默认值差异是合理的，不应该强行统一成同一套 pipeline。**

## 二、逐项结论

| 能力 | 谁更好 | 理由 | 处理 |
|---|---|---|---|
| **结构化分块**（标题层级 / 代码块 / 表格原子） | **NoteAI** | shibei 的句子切分会切断代码块和表格，且丢标题层级 | 已抽成 `ragkit.chunk_markdown`；shibei 尚未接入（需要 `section` 元数据列） |
| **通用分块**（多格式 / parent-child） | **shibei** | NoteAI 假设 Markdown，docx/xlsx 无法套用 | 保留各自实现，只共享 Markdown 策略 |
| **融合** | 平手 | RRF 适合跨源，加权线性适合同索引内 dense/sparse | 已抽成 `ragkit.fusion`，两边分别使用 |
| **覆盖率门槛** | **shibei**（仅关键词查询） | 能拦掉「只命中一个泛词」的误召回；但会误杀自然语言问句 | 已抽到 `ragkit.coverage`；NoteAI **默认不启用**，只在关键词模式建议开 |
| **MMR 去重** | **NoteAI** | shibei 完全没有；长文档召回经常出现近重复片段 | 已抽到 `ragkit.mmr`，shibei 新增 `mmr=True`（TUI 键 `6`） |
| **重排工程化** | **NoteAI** | INT8 量化、空闲卸载、失败冷却，shibei 只有朴素单例 | 建议后续把 NoteAI 的加载/卸载策略移植到 shibei |
| **上下文扩展**（邻块/反链/对象上下文） | **NoteAI**（笔记域） | 依赖 workspace 的知识图谱，shibei 没有也不该有 | 不整合；shibei 保留 `parent_text` 作为通用基线 |
| **HyDE** | **NoteAI** | 弱结果时用 LLM 生成假设答案再检索；shibei 无 LLM 预算 | 不整合 |
| **查询改写 / 同义词扩展** | **shibei** | 规则改写 + 同义词表 + 可选 LLM 改写，确定性强 | 可反向移植到 NoteAI 的关键词搜索 |
| **动态 top-k** | **NoteAI** | 按问题复杂度决定引用条数；shibei 固定 5 | 建议后续移植 |
| **查询缓存** | **NoteAI** | 5 分钟 TTL，键覆盖 query/topics/tags/file/doc_kind | 键算法已抽到 `ragkit.cache`；shibei 尚未接入 |

## 三、已整合（本次）

```
ragkit/                    零依赖、纯函数
├── fusion.py              rrf_gain / rrf_scores / weighted_fuse / normalize
├── coverage.py            build_groups / coverage_ratio / meets_coverage
├── mmr.py                 mmr_select（有向量用余弦，无向量用 Jaccard）
├── chunking.py            chunk_markdown（标题层级 + 代码/表格原子 + overlap）
└── cache.py               make_cache_key
```

- **shibei**：`federate.py` 用 `rrf_gain`；`tokenizer.query_coverage_groups` 转发到
  `ragkit.coverage`；`recall(mmr=True)` 走 `ragkit.mmr`，TUI 新增 `6` 号开关。
- **NoteAI**：`_mmr_dedup` 与 `_cache_key` 改为调用 `ragkit`，行为不变（48 个相关单测通过）。
- **版本**：两个项目统一到 Python 3.12（`requires-python`、`.python-version`、
  ruff/mypy target）；`ragkit` 本身保持 `>=3.10` 以便单测在旧环境跑。

## 四、故意没整合的

1. **pipeline 顺序**：shibei 是 `coverage → rerank`，NoteAI 是 `MMR → rerank → expand`。
   顺序由「成本」和「数据依赖」决定，不是口味：
   - coverage 是词面过滤，必须在 rerank 前，否则白付 cross-encoder 成本；
   - MMR 需要候选池 + 向量，必须在 rerank 前，否则 top-k 已被近重复占满；
   - expand 会引入新文本，必须在 rerank 后，否则相关性打分建立在扩展文本上。
   抽成可配置 pipeline 会允许上面这些错误组合，所以不抽。
2. **存储与索引生命周期**：
   - shibei：单个 SQLite 文件（FTS5 虚拟表 + content 表 + 向量表），一次 import 在一个事务里完成；
     manifest 只记 `文件 -> MD5`，用来判断要不要重导整个文件。
   - NoteAI：workspace 目录下的 zvec 向量集合 + bm25s 稀疏索引 + chunk 级 manifest
     （`files: 相对路径 -> [chunk_id]`，带 `version`）。写入是「按文件替换 chunk + 重建稀疏索引」，
     需要检测三份数据是否同版本。
   换存储或换生命周期模型都是重写，不是配置。
3. **覆盖率默认开启**：NoteAI 的查询是问句，默认开 coverage 会显著降低召回。
4. **HyDE**：需要 LLM，shibei 的冷路径不能引入。

## 五、后续可做（按收益排序）

1. shibei 接入 `chunk_markdown`：给 content 表加 `section` 列，Markdown 文件用结构化分块，
   其他格式仍走通用分块。
2. 把 NoteAI 的重排加载/卸载/冷却移植到 `shibei.rerank`（或 `ragkit` 的可选适配器）。
3. shibei 接入 `ragkit.cache`（按库 + query + flags 做 TTL）。
4. NoteAI 在「关键词搜索」入口启用 `ragkit.coverage`（对话入口保持关闭）。
5. shibei 反向移植 `select_dynamic_top_k`（按问题复杂度选 k）。

## 六、跨项目接入

两份实现仍然独立，但通过 `/v1/recall` 信封可以互相挂成远程库：

- shibei 侧：注册表支持 `url:` 类型的远程 brain；
- NoteAI 侧：加一个 loopback shim，把 `retrieve()` 映射成同一信封。

这样 TUI、MCP、联邦召回都能直接消费 NoteAI 的结果，而不需要合并内核。

## 七、瘦身建议：NoteAI 的存储层可以砍掉一半

实测数据（`~/Documents/My_Notes`，180 个文件）：

| 指标 | 数值 |
|---|---|
| chunk 数 | 3,278 |
| 索引体积 | zvec 11 MB + bm25s 6.2 MB = **17 MB** |
| 512 维精确余弦（numpy） | **0.04 ms/query** |
| 100,000 chunk 精确余弦 | 3.1 ms/query |
| shibei 导入 239 个文件 / 6,233 chunk | **2.0 s**，查询 1.5 ms |

3,278 个 chunk 用 HNSW 是**杀鸡用牛刀**：精确检索比近似检索更准，而且快一个数量级。
zvec + bm25s + manifest 版本检测 + 两套缓存 + 三份数据一致性，都是为了
「十万级以上 + 多进程并发写」准备的，但当前场景是「桌面单用户 + 3k chunk」。

### 目标结构：一个 SQLite 文件

```sql
files(path PK, hash, doc_kind, topic, updated_at)
chunks(id PK, file_path, section, content, tokens, doc_kind, topic, tags, vector BLOB)
chunks_fts USING fts5(tokens, content='chunks', content_rowid='id')
```

- 写：按文件一个事务（`DELETE ... WHERE file_path=?` + `INSERT`），FTS 触发器同步；
- 读：FTS5 取 top-64 + numpy 精确余弦取 top-64 → 融合 → MMR → rerank；
- 去掉：zvec、bm25s、`_INDEX_VERSION`、collection/bm25 缓存、manifest 版本修复、三份数据对齐。

预计存储层从 ~1,370 行降到 ~350 行，依赖少两个，索引从两套变一套。
检索延迟几乎不变（rerank 才是大头）。

### 保留（质量杠杆，成本很低）

- fastembed + rerank（最大质量收益）
- MMR（0.04ms 级别）
- HyDE（仅在弱结果时触发，可选）
- 动态 top-k / question_shape（纯计算）
- 上下文扩展（可简化为「同 section 邻块 + 反链」两条 SQL）

## 八、按决策落地的三项

1. **增量索引（可选）**：`shibei watch --interval N` 轮询 sources，复用现有文件级
   manifest（MD5）做增量导入；不引入 watchdog 依赖，也不升级 chunk 级 manifest。
2. **Markdown 标题层级**：`chunking.structural=true`（默认）时，`.md` 走
   `ragkit.chunk_markdown`（H2/H3 层级、代码块/表格原子化、overlap），
   标题写回 chunk 首行，结果里新增 `section` 字段；非 Markdown 仍走通用分块。
3. **远程库**：`shibei brains add NAME --url http://host:port --token ...`，
   `RemoteBrain` 按同一份 `/v1/recall` 信封转发，可以直接参与 `brain="*"` 联邦；
   `noteai` 这类外部 RAG 只要实现 `/v1/recall` 就能挂进来。

## 九、zvec 实测与减法

### 已验证：zvec 0.5 自带 FTS（jieba）

```python
FieldSchema("content", DataType.STRING,
            index_param=FtsIndexParam(tokenizer_name="jieba"))
Query(field_name="content", fts=Fts(match_string="缓存穿透"))
```

同一进程内插入后立刻查询可以命中中文。**但重开 collection 后 FTS 索引不恢复**
（`stats.index_completeness` 只有 dense，`create_index` 也不补数据），所以
暂时不能删 bm25s——否则重启后全文检索会静默失效。

结论：zvec 保留。升级到 **0.8** 后 FTS 索引可以跨进程持久化，已用它替掉 bm25s。

### 已完成的减法

- `metadata.json`（148KB）合并进 manifest，旧文件自动迁移并删除；
- 去掉 `_METADATA_CACHE` 一套缓存 + 锁，统一走 `_MANIFEST_CACHE`；
- topics/tags 与版本信息统一写入一份 manifest，少一条写路径；
- NoteAI 658+ 单测全绿。

### 剩余可减（按收益/风险）

1. 把 `manifest.json`（版本）与 `file_manifest.json`（文件→chunk）合成一个文件；
2. ~~zvec 0.8 验证 FTS 持久化后，删除 bm25s~~（已完成：依赖、6.2MB 索引、index_bm25.py、缓存与重建逻辑全部移除）；
3. `_COLLECTION_CACHE` 不能删（`zvec.open` 重载 mmindex 很贵）。
