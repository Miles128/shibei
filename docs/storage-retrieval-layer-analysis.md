# 拾贝（Shibei）核心存储与检索层源码分析报告

> 分析范围：`src/shibei/` 下的 config / bm25 / importer / searcher / memory / tokenizer / chunker / manifest / dedup / parsers / tagger，以及数据流直接关联的 summarizer / llm / services / rewriter / expander / related，加上 `config.yaml` 与 `config.example.yaml`。
> 分析方法：逐文件通读 + 3 个针对 FTS5 行为的运行实验验证（见 §8.0）。
> 所有行号以当前工作区源码为准。

---

## 目录

1. [架构总览](#1-架构总览)
2. [模块逐一分析](#2-模块逐一分析)
3. [完整数据流](#3-完整数据流)
4. [数据库 Schema 与 FTS5 细节](#4-数据库-schema-与-fts5-细节)
5. [记忆系统深入](#5-记忆系统深入)
6. [中文分词策略](#6-中文分词策略)
7. [模块耦合与设计模式](#7-模块耦合与设计模式)
8. [弱点、潜在 Bug、安全隐患与改进建议](#8-弱点潜在-bug安全隐患与改进建议)
9. [值得注意的问题清单](#9-值得注意的问题清单)

---

## 1. 架构总览

### 1.1 系统定位

拾贝是一个"给 AI Agent 用的中文轻量知识库 + 长期记忆系统"：文档知识库（chunks/chunks_fts）与 Agent 对话记忆（memories/memories_fts）**共用一个 SQLite 数据库文件**，仅靠 `source`/`tags` 列区分来源（`memory.py:8-10` 的模块注释明确说明这一设计）。搜索全部走 SQLite FTS5 的 BM25，不依赖任何向量库，LLM 是可选增强（摘要/改写）。

### 1.2 分层与依赖方向

```
services.py（门面，全懒加载）
   ├── importer.py  → tagger / manifest / parsers / dedup / chunker / summarizer (+懒加载 bm25)
   ├── searcher.py  → bm25 (+懒加载 tokenizer / rewriter / llm_rewriter / expander / related / memory)
   ├── memory.py    → tokenizer
   └── bm25.py      → tokenizer
叶子模块：tokenizer / chunker / parsers / dedup / manifest / config（零内部依赖）
```

依赖方向干净、无环（环通过"函数内懒加载 import"化解，见 §7）。

### 1.3 磁盘布局（数据落盘位置）

| 路径 | 内容 | 写入者 |
|---|---|---|
| `<db_path>/fts5.db` | SQLite：`chunks`、`chunks_fts`、`memories`、`memories_fts` 四张表 | `bm25.py` / `memory.py` |
| `<db_path>/manifest.json` | 文件级 MD5 清单，`{file_path: md5}` | `manifest.py` |
| `<db_path>/chunk_hashes.json` | chunk 级内容 MD5 去重表，`{md5: source}` | `dedup.py` |

`db_path` 默认 `~/.shibei/db`（`config.py:13`，经 `db_path_expanded` 展开，`config.py:132-134`）。

---

## 2. 模块逐一分析

### 2.1 config.py — 配置系统（218 行）

**职责**：YAML + 环境变量合并，产出强类型 dataclass 配置树。

**关键类型**：

| 类型 | 行号 | 关键字段 |
|---|---|---|
| `StorageConfig` | 12-14 | `path`, `search_engine`（注释"仅支持 bm25"） |
| `ChunkingConfig` | 18-25 | `max_chars=800`, `split_pattern=r"\n(?=## )"`, `overlap=0`, `parent_window=0`, `sentence_boundaries` |
| `TagRule` / `PathTagRule` | 29-37 | 内容 regex / 路径 glob 两种打标签规则 |
| `TaggingConfig` | 41-43 | `rules`, `path_rules` |
| `ExpanderConfig` | 47-53 | 同义词表路径、编辑距离模糊兜底参数 |
| `RewriteRule` / `RewriterConfig` | 57-70 | 口语→关键词改写规则 |
| `RelatedConfig` | 74-79 | 相关文档发现开关（默认关，注释说明宽标签 fanout 慢） |
| `LLMConfig` | 83-97 | 摘要模型、prompt 模板（API key 只走 `SHIBEI_LLM_API_KEY` 环境变量） |
| `Config` | 119-138 | 聚合 + `db_path_expanded` / `sources_expanded` 属性 |
| `load_config` | 141-218 | YAML 加载 + 环境变量覆盖 |

**输入输出**：`load_config(path="config.yaml") -> Config`；环境变量 `SHIBEI_CONFIG`（配置文件路径）、`SHIBEI_SOURCES`（逗号分隔覆盖数据源，163-164 行）。

**值得注意的实现点**：
- 改写规则在 YAML 里用 `from:`/`to:` 键，`load_config` 在 199-208 行手动映射到 `RewriteRule.source/target`（与 `rewriter.py` 里另一个 `RewriteRule` dataclass 重复定义——见 §7 耦合问题）。
- `ignore_dirs` 默认值 `DEFAULT_IGNORE_DIRS`（100-116 行）覆盖 .git/node_modules/.venv 等。
- 无任何配置校验：未知键直接抛 `TypeError`（`StorageConfig(**raw["storage"])`），拼错的键则静默忽略。

### 2.2 tokenizer.py — 中文分词（168 行）

**职责**：统一 jieba 检测与分词，供 `bm25.py` 与 `memory.py` 共用；索引用 `tokenize_text`，查询用 `tokenize_query`。

**关键函数**：

| 函数 | 行号 | 语义 |
|---|---|---|
| `jieba_available()` | 32-41 | 模块级缓存检测 |
| `tokenize_text(text) -> str` | 50-77 | 索引侧：jieba 精确分词；降级 bigram+单字；输出空格分隔 token |
| `tokenize_query(text) -> str` | 80-110 | 查询侧：**引号短语 + jieba 子词 + bigram，OR 连接**，直接生成 FTS5 表达式 |
| `is_noise_token(token)` | 23-27 | 过滤 `#*-=|>_+`、纯数字、空白 |
| `_bigram_tokenize` / `_bigram_query_tokenize` | 113-168 | 无 jieba 兜底 |

**核心设计**：`_iter_mixed_segments`（44-47）用 `_MIXED_SEGMENT_RE`（15-17）把文本切成"纯中文段 / 字母数字段"交替流，中文段走 jieba/bigram，英文段用 `_ALNUM_RE` 提取完整词。`tokenize_query` 对中文整段加 `"..."` 短语、每个 jieba 子词加引号、每对相邻字符加 bigram 引号，全部 `OR` 连接——**这是对 FTS5 注入的关键防御**（见 §8 安全隐患分析）。

### 2.3 chunker.py — 分块器（198 行）

**职责**：多级分块，支持 overlap、中文句子边界、parent-child。

**关键函数**：

| 函数 | 行号 | 语义 |
|---|---|---|
| `chunk_text(...)` | 104-146 | 主入口：L1 按标题切 → L2 按句子切 → L3 按 max_chars 合并 → L4 overlap → L5 parent-child |
| `_split_by_sentences` | 28-56 | 按 `boundaries`（默认 `。！？!?\n`）切句，保留句末标点 |
| `_merge_sentences` | 59-101 | 句子累积到 `max_chars`；单句超长硬切；overlap 从上一 chunk 末尾取字符 |
| `_build_parent_child` | 149-198 | 滑动窗口把相邻 N 个 child 合并成 parent |

**返回结构**（`chunker.py:11-12` 注释）：
- `parent_window=0` → `list[str]` 普通 chunk；
- `parent_window>0` → `list[ChildChunk]`，`ChildChunk`（19-25 行）含 `text/parent_text/parent_id/child_index`。

**细节**：`chunk_text` 第 135 行 `len(sec) < 10` 的 section 被整体丢弃；L1 切分只认 `\n(?=## )` 的二级标题（默认配置）；parent 滑动窗口使每个 child 都有 parent、parent 之间重叠（153-178 行）。

### 2.4 parsers.py — 格式解析（92 行）

**职责**：按后缀分发到解析器，统一返回纯文本。

**关键点**：
- 插件注册表 `_PARSERS`（15 行）+ `register_parser`（18-26 行），内置 md/markdown/txt/docx/xlsx/csv（80-89 行注册）。
- `parse_file`（29-35 行）：不认识的扩展抛 `ValueError`（importer 会捕获转 warning）。
- `_parse_docx`（46-50）只取段落文本、不含表格；`_parse_xlsx`（53-66）`read_only + data_only` 流式读、每行 ` | ` 连接、带 `# 表名` 标题；`_parse_csv`（69-77）无 BOM 处理。
- docx/openpyxl 均为函数内懒加载，未安装时导入不炸、调用时报错（由 importer 捕获）。

### 2.5 tagger.py — 自动标签（75 行）

**职责**：路径 glob 规则（优先）+ 内容/路径 regex 规则（兜底）推断标签。

**关键函数**：`Tagger.infer`（21-50）、`_match_path_rule`（52-70）。

**细节**：
- 路径规则把文件路径拆出**所有后缀子串**（`a/b/c.md`、`b/c.md`、`c.md`，60-65 行）参与 `fnmatch`，从而支持 `pattern="面试/HSBC/*"` 这种"相对 source root"写法。
- regex 规则作用于 `f"{file_path}\n{content[:500]}"`（37 行）——**只扫正文前 500 字**。
- 路径规则与 regex 规则**都累积**（不是 first-match-wins），靠 `seen` 集合去重；但两条路径规则可以同时命中（互相不冲突）。

### 2.6 dedup.py — 去重（103 行）

**职责**：chunk 级内容 MD5 精确去重，持久化到 `chunk_hashes.json`。

**关键函数**：

| 函数 | 行号 | 语义 |
|---|---|---|
| `content_hash(content)` | 26-29 | 内容 strip 后 MD5 |
| `ChunkHashStore` | 32-71 | hash→source 映射的 JSON 持久化；`remove_by_source` 按 source 批量清理 |
| `dedup_chunks(chunks, existing_hashes)` | 74-103 | 保留首次出现、返回 `(unique, skipped, new_hashes)` |

**语义**：跨文件去重——同一内容只保留首次入库的 source（模块 docstring 7-9 行）。注意去重只看 `content`（第 3 个字段），不看 tags/parent/summary。

### 2.7 manifest.py — 增量追踪（77 行）

**职责**：文件级 MD5 清单，支撑增量导入。

**关键函数**：`file_hash`（42-45，全文件读入算 MD5）、`classify(current_files)`（47-77，返回 `(to_add, to_update, to_remove)`）。

**语义**：新增=清单无此路径；变更=hash 不同；删除=清单有但磁盘无。`save()`（25-28）直接覆写 JSON，无原子写。

### 2.8 bm25.py — FTS5 BM25 索引（310 行）

**职责**：chunks 原文表 + chunks_fts 内容无关索引、批量增删、双字段检索 + RRF 融合。

**关键常量**（16-19 行）：`DB_NAME="fts5.db"`、`CONTENT_TABLE="chunks"`、`FTS_TABLE="chunks_fts"`、`SUMMARY_COL="summary"`。

**关键类/函数**：

| 成员 | 行号 | 语义 |
|---|---|---|
| `BM25Index.__init__` | 28-30 | 打开 DB 并 `_init_db` |
| `_init_db` | 32-80 | 建 `chunks`（含旧 schema 自动 ALTER 加 `parent_text/parent_id/summary`，44-51）；建 `chunks_fts`（`content=''` **contentless 模式**，61-67）；FTS 列结构不符时删表重建并回填（55-78） |
| `_rebuild_fts_index` | 82-95 | `delete-all` + 全量回填，修复 contentless 大批量删除后的索引损坏 |
| `add_batch` | 103-131 | 兼容 4/6/7 元组；先插 chunks 拿 rowid，再按 rowid 插 FTS；**一个事务提交** |
| `search` | 133-151 | 正文 + 摘要双字段检索（各取 `limit*3`），RRF 融合 |
| `_search_field` | 153-185 | 单字段 FTS MATCH + 可选 `c.tags LIKE` 过滤，JOIN chunks 取原文 |
| `_row_to_item` | 187-206 | 行→结果 dict；`text[:300]` 截断；**score 归一化 `1/(1+abs(rank))`** |
| `_rrf_fuse_rows` | 208-243 | RRF(k=60) 融合 |
| `remove_sources` / `delete_by_rowids` | 270-310 | 先删 chunks 再整体重建 FTS |

**设计要点**：`chunks_fts` 是 **contentless FTS5**（`content=''`），实验验证 contentless 表不支持 `DELETE FROM`，因此删除一律走"删 chunks 行 + 整表重建索引"（这也是 `_rebuild_fts_index` 存在的原因，见 §4.3）。

### 2.9 memory.py — 对话长期记忆（450 行，重点）

**职责**：Agent 事实记忆的 SQLite+FTS5 存储；写入、BM25 搜索、supersedes 版本链、过期清理、压缩候选。

**关键类**：`AbstractMemoryStore`（34-73，ABC 接口）+ `MemoryStore`（75-450，SQLite 实现）。

**表结构**（`_init_db`，89-115 行）：

```sql
CREATE TABLE IF NOT EXISTS memories (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,          -- UUID
    fact TEXT NOT NULL,
    category TEXT,
    source TEXT,
    tags TEXT,
    created_at TEXT NOT NULL,         -- UTC ISO8601
    expires_at TEXT,
    supersedes TEXT,                  -- 本记忆替代了谁
    superseded_by TEXT                -- 本记忆被谁替代
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    fact, tags, category,
    content='memories', content_rowid='rowid'   -- external content 模式！
);
```

**关键方法**：

| 方法 | 行号 | 语义 |
|---|---|---|
| `add` | 121-185 | strip 校验 → UUID → source 默认 `"agent"` 且并入 tags → **相似度检测定 supersedes** → 插 memories + 同步 FTS → 回写旧记忆 `superseded_by` |
| `_find_most_similar` | 187-204 | BM25 召回（`max(limit*3,10)`）后按 **token 集合 Jaccard** 重排序 |
| `search` / `_search_bm25` | 218-282 | FTS MATCH + 可选 category 过滤 + 默认排除 `superseded_by` 非空；`ORDER BY rank` |
| `get` / `list` / `count` | 284-358 | 单查 / 列表（同样默认排除被替代）/ 计数（排除被替代） |
| `delete` / `forget` | 315-347 | 删 FTS 行 → 删 memories 行 → 清空他人指向自己的 `superseded_by`（**被删记忆的旧版本"复活"**） |
| `cleanup_expired` | 360-383 | 删 `expires_at <= now` 且**未被替代**的过期记忆 |
| `find_compressible` | 385-419 | 返回"超过 max_age_days 且不在 keep_recent 内"的记忆候选，压缩动作留给外部 Agent |
| `update` | 421-450 | 改 fact/tags：删 FTS → UPDATE → 重插 FTS |

### 2.10 summarizer.py — Chunk 摘要（80 行，数据流关联）

`ChunkSummarizer`（20-80 行）：`LLMConfig.enabled` 时才建 `LLMClient`；`summarize(content)` 截断到 `max_content_length`，调 LLM 一句话中文摘要，**任何异常降级返回 `""`**（71-74 行），绝不阻断导入。prompt 模板 `DEFAULT_SUMMARY_PROMPT`（14-17）用 `str.format` 注入 `content`/`max_length`（64-67 行）。LLM 客户端 `llm.py:LLMClient`（30-100）基于 `urllib` 零第三方依赖，API key 走 `SHIBEI_LLM_API_KEY`。

### 2.11 importer.py — 文档导入（324 行）

**职责**：扫描目录 → 并行解析 → 标签 → 分块 → 摘要 → 去重 → 写 BM25，并维护 manifest 与 chunk hash store。

**关键函数**：

| 函数 | 行号 | 语义 |
|---|---|---|
| `Importer.run` | 38-209 | 主流程（见 §3.1） |
| `_parse_one` | 93-100 | 解析异常全捕获，单文件失败不中断整批 |
| `_import_bm25` | 211-224 | 删旧 source + `add_batch` |
| `clean_orphans` | 226-272 | 对比 manifest 与磁盘，删孤儿 source（支持 dry_run） |
| `list_sources` | 275-324 | source 列表 + 按 tag 统计 |

### 2.12 searcher.py — 搜索管线（194 行）

**职责**：策略模式搜索入口，组装改写 → 分词 → 扩展 → BM25 → 记忆召回 → 相关文档。

**关键点**：

| 成员 | 行号 | 语义 |
|---|---|---|
| `SearchEngine` ABC | 15-25 | 策略接口 |
| `BM25Engine.search` | 33-94 | 规则改写（`QueryRewriter`，rewriter.py）→ 可选 LLM 改写（`LLMQueryRewriter`，llm_rewriter.py）→ `tokenize_query` → 可选同义词/模糊扩展（`QueryExpander`，expander.py）→ 再 `tokenize_query` → `BM25Index.search` |
| `_ENGINES` / `register_engine` | 97-105 | 引擎注册表（插件点） |
| `Searcher.search` | 114-137 | 引擎分发 + `_search_memories` + `_find_related` |
| `_search_memories` | 139-156 | 记忆召回，映射为 `{"type":"memory",...}` 并入结果 |
| `_find_related` | 158-194 | 对前 `top_k` 个结果查 related（`RelatedFinder`，related.py），按 score 去重排序 |

**related.py 机制**（47-190 行）：tag `LIKE` 找候选 source → 拉 chunks（上限 5000 行）→ 标签共现（Jaccard）+ Markdown 链接（`[[wiki]]`/`[text](target)`）+ token 关键词共现三路打分融合。

**rewriter.py**（26-63 行）：顺序子串替换，返回结构化 `applied` 信息。
**expander.py**（140-268 行）：同义词表（YAML/JSON）+ 编辑距离（自实现 Levenshtein，113-137 行）模糊兜底，输出空格分隔扩展词串。

---

## 3. 完整数据流

### 3.1 文档导入链路（`Importer.run`，importer.py:38-209）

```
磁盘文件
  │ ① 扫描 importer.py:59-73  rglob("*{ext}") × 每扩展名、ignore_dirs 过滤
  ▼
current_files {abs_path: Path}
  │ ② 增量分类 manifest.py:47-77  MD5 对比 → (to_add, to_update, to_remove)
  ▼
changed = to_add + to_update
  │ ③ 并行解析 importer.py:93-107  ThreadPoolExecutor(max_workers=min(32, cpu+4))
  │     parsers.py:29-35 按后缀 dispatch → 纯文本
  ▼
content
  │ ④ 打标签 importer.py:121  tagger.py:21-50  path_rules + regex(内容前500字) → tags[]
  │ ⑤ 分块 importer.py:123-131  chunker.py:104-146
  │     L1 "\n(?=## )" → L2 句子边界 → L3 max_chars=800 合并 → L4 overlap → L5 parent-child
  ▼
chunks_data（7 元组: source, file_path, tags, content, parent_text, parent_id, summary=""）
  │ ⑥ 摘要 importer.py:154-158  summarizer.py:37-80  LLM 一句话摘要（失败→""）
  ▼
  │ ⑦ 去重 importer.py:160-183  dedup.py:74-103  按 content MD5（跨文件）
  │     full 模式只清 to_remove 的 hash（⚠️ 见问题 P1）
  ▼
unique_chunks
  │ ⑧ 写库 importer.py:211-224 → bm25.py
  │     a) remove_sources(to_remove + to_update)  bm25.py:270-285 删 chunks 行 + FTS 整表重建
  │     b) add_batch(unique) bm25.py:103-131
  │          chunks 表 INSERT（rowid 自增）→ chunks_fts INSERT(rowid, tokenize_text(content),
  │          tokenize_text(summary))  —— 落表：chunks / chunks_fts
  ▼
  │ ⑨ 元数据持久化 importer.py:189-197
  │     manifest.set_hash(全量重算 MD5) → manifest.json
  │     ChunkHashStore.add 新增 hash → chunk_hashes.json
  ▼
返回 {"files", "chunks", "skipped", "removed", "deduped", "status"}
```

**每一步落点**：内容与摘要分别落在 `chunks.content/summary` 与 `chunks_fts.content_tokens/summary_tokens`；parent 上下文落在 `chunks.parent_text/parent_id`；文件指纹在 `manifest.json`；chunk 指纹在 `chunk_hashes.json`。

### 3.2 搜索链路（`Searcher.search` → `BM25Engine.search`）

```
用户查询
  │ ① 规则改写 searcher.py:41-48  rewriter.py:53-58  子串替换（config 规则）
  │ ② 可选 LLM 改写 searcher.py:49-62  llm_rewriter（仅 use_llm=true）
  │ ③ tokenize_query searcher.py:64  引号短语 + jieba 子词 + bigram，OR 连接
  │ ④ 可选扩展 searcher.py:66-78  expander.py:212-268  同义词 + 编辑距离 → 再 tokenize_query
  ▼
BM25Index.search bm25.py:133-151
  │ ⑤ 双字段检索 bm25.py:149-150
  │     content_tokens 路 + summary_tokens 路（各 limit*3）→ _search_field（FTS MATCH + tags LIKE）
  │ ⑥ RRF 融合 bm25.py:208-243  k=60：两路 rank 各计 1/(60+rank) 累加，行级去重取 top limit
  ▼
results[]（含 score/text[:300]/parent_text[:800]/rank）
  │ ⑦ 记忆召回 searcher.py:130-131 → memory.py:218-282  memories_fts MATCH，排除 superseded，ORDER BY rank
  │ ⑧ 相关文档 searcher.py:133-137 → related.py:47-100  （默认关闭）
  ▼
{query, query_used, total(=len(results)), results, memories[], related[], 改写/扩展元信息}
```

### 3.3 记忆写入链路（`MemoryStore.add`，memory.py:121-185）

```
fact
  │ ① strip 校验（空→ValueError）
  │ ② source 默认 "agent" 并入 tags（sorted 去重）
  │ ③ 相似检测 memory.py:146-152 → _find_most_similar(187-204)
  │     memories_fts MATCH 召回 top(10) → token 集合 Jaccard 重排 → top1
  │     若 score ≥ supersede_threshold(0.7) → supersedes = top.id
  ▼
  │ ④ INSERT memories（supersedes 记录旧 id）
  │ ⑤ INSERT memories_fts(rowid, tokenize_text(fact), tokenize_text(tags), category)  手动同步
  │ ⑥ 若 supersedes：UPDATE 旧记忆 SET superseded_by = 新id  → 版本链闭合
  ▼
返回 get(memory_id)（多一次连接读取）
```

---

## 4. 数据库 Schema 与 FTS5 细节

### 4.1 chunks / chunks_fts（bm25.py:35-67）

```sql
CREATE TABLE IF NOT EXISTS chunks (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,          -- 文件绝对/相对路径字符串（导入时的 slug）
    file_path TEXT,       -- 文件路径
    tags TEXT,            -- 逗号分隔
    content TEXT,         -- chunk 原文
    -- 以下三列由 ALTER 自动迁移补齐（bm25.py:44-51）
    parent_text TEXT DEFAULT '',
    parent_id   TEXT DEFAULT '',
    summary     TEXT DEFAULT ''
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content_tokens,       -- tokenize_text(content)，空格分隔
    summary_tokens,       -- tokenize_text(summary)
    content=''            -- contentless 模式（无原文、无触发器）
);
```

**contentless 模式的工作方式**：
- FTS5 里 `content=''`（空串）= contentless 表：FTS 索引**不存原文**，只存 token 倒排结构，节省一半空间；查询必须 JOIN chunks 表取原文（`bm25.py:165-184` 正是 `FROM chunks_fts f JOIN chunks c ON f.rowid = c.rowid`）。
- rowid 必须与 chunks.rowid 严格对齐（`add_batch` 用 `cur.lastrowid` 保证，bm25.py:125-130）。
- **contentless 表不支持 DELETE/UPDATE**（已实验验证：`cannot DELETE from contentless fts5 table`）。因此删除只能：(a) 用特殊命令 `INSERT INTO chunks_fts(chunks_fts) VALUES('delete-all')` 清空；(b) 从 chunks 删行后**整表重建**。这就是 `_rebuild_fts_index`（82-95）存在的直接原因，也是 `remove_sources`/`delete_by_rowids` 每次删除都 O(N) 重建的原因。
- 一旦 chunks 与 chunks_fts 的 rowid 失配（如外部直接改库、中断的迁移），FTS 会出现幽灵行或漏行；`_init_db` 的列结构检查（55-59）只在列数变化时触发重建，**不校验行级一致性**。

### 4.2 memories / memories_fts（memory.py:91-114）

```sql
CREATE TABLE IF NOT EXISTS memories (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,        -- UUIDv4
    fact TEXT NOT NULL,
    category TEXT, source TEXT, tags TEXT,
    created_at TEXT NOT NULL,       -- UTC ISO8601（seconds 精度）
    expires_at TEXT,
    supersedes TEXT,                -- 旧记忆 id
    superseded_by TEXT              -- 新记忆 id
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    fact, tags, category,
    content='memories', content_rowid='rowid'   -- external content 模式
);
```

**external content 模式的工作方式**：
- `content='memories', content_rowid='rowid'`：FTS 表不存原文，需要时从 memories 表按 rowid 读回；查询里直接 `JOIN memories m ON m.rowid = f.rowid`（memory.py:243-245）。
- 与 contentless 不同，external content **支持 DELETE**（`DELETE FROM memories_fts WHERE rowid=?` 可用），所以删除可以逐行同步。
- **没有定义任何触发器**：FTS 同步完全靠代码手动——insert 时同步插（170-176）、删除时先删 FTS 再删内容（334-337）、update 时删 FTS→改内容→重插 FTS（436-448）。任何绕过 `MemoryStore` 的直接 SQL 写 memories 都会造成 FTS 静默失同步。
- 索引内容：`fact`/`tags` 先经 `tokenize_text` 再入库，`category` 原始字符串入库（175 行）——三者 token 化程度不一致（见问题 P17）。

### 4.3 两种 FTS5 模式对比（本项目内的取舍）

| 维度 | chunks_fts | memories_fts |
|---|---|---|
| 模式 | contentless（`content=''`） | external content（`content='memories'`） |
| 是否存原文 | 否 | 否（从内容表读） |
| 支持 DELETE | **否**（只能 delete-all/重建） | 是 |
| 同步方式 | 手动 + 整表重建 | 手动逐行（无触发器） |
| 删除成本 | O(N) 全量重建 | O(1) 单行 |
| 失同步风险 | rowid 失配 | 内容表被直改 |

**不一致之处**：两个记忆/文档子系统共用同一 db 文件，却选了两种模式，导致维护心智两套；文档侧完全可以用 external content + 触发器或 `content=''` 配合逐行 'delete' 特殊命令避免全量重建（见建议 §8）。

### 4.4 RRF 融合实现（bm25.py:208-243）

```
content_rows（content_tokens 路，limit*3 条）
summary_rows（summary_tokens 路，limit*3 条）
  ↓ 每个 rowid：
     rrf_scores[rowid] += 1/(k + rank)    k=60，rank 从 1 起
     best_item[rowid] 首次出现时缓存该行 dict（content 路优先）
  ↓
按 rrf_scores 降序取前 limit，重排 rank=1..n
```

要点：
- 这是**同一索引内两个字段**（正文 vs 摘要）的 RRF，不是多引擎/多索引融合——语义上是"正文命中或摘要命中都计分"，对"摘要含关键词但正文不含"的召回有效。
- `best_item` 取"先到先得"：同一 rowid 同时命中两路时，摘要路的命中信息（如果摘要 rank 更优）被丢弃，但 RRF 分数两路都累加，排序不受影响（`best_item` 只影响返回内容）。
- 两路各只取 `limit*3` 预筛，超出的候选不参与融合——大库高召回场景下 RRF 只作用于截断后的子集。
- `score` 字段（`_row_to_item`，199 行）与 RRF 分数无关，是单独算的 BM25 归一化值——**该归一化方向反了**（见问题 P5）。

---

## 5. 记忆系统深入

### 5.1 版本链（supersedes）机制

**数据结构**：`memories.supersedes`（本记忆替代的旧记忆 id）+ `memories.superseded_by`（替代本记忆的新记忆 id），双向指针构成链。

**建链流程**（`add`，121-185 行）：
1. 用新 fact 做 BM25 召回（`_search_bm25`，默认排除已被替代的记忆，191-193 行）；
2. 对召回候选取 token **集合** Jaccard 相似度（`_find_most_similar`，197-204 行），取 top1；
3. 若 `score >= supersede_threshold`（默认 0.7）→ 新记忆 `supersedes=旧id`；
4. 插入后回写旧记忆 `superseded_by=新id`（178-182 行）。

**链的可见性**：`search`/`list`/`count` 默认 `WHERE (superseded_by IS NULL OR superseded_by = '')`（253-254、305-306、355 行）——只暴露链头（最新事实）。`include_superseded=True` 可看历史。

**链的断裂与复活**：
- `delete`（319-347）删掉链中某记忆后，把所有指向它的 `superseded_by` 清空（338-345）→ **被删记忆的旧版本自动"复活"为有效记忆**。这是合理语义（删掉新版本回退旧版本），但同样发生在删除链中间节点时（如链 A→B→C 删 B，C 的 superseded_by 清空，C 和 A 同时有效——可能产生矛盾事实并存）。
- `update`（421-450）只改 fact/tags，**不动 supersedes/superseded_by**：压缩后的事实与链语义可能脱节（压缩改写后可能不再相似于它替代的对象）。
- 链的**长度无限制**：反复写相似事实会产生 A→B→C→D…长链，历史版本永久保留、无 TTL、无清理入口（`cleanup_expired` 也不碰被替代的行，见下）。

**阈值语义**：0.7 作用于 token 集合 Jaccard，**与词频、词序无关**——"我喜欢苹果"与"苹果喜欢我"集合完全相同，Jaccard=1.0 必然触发 supersede；反之两句话仅换用同义词（"苹果"vs"apple"）则 Jaccard 低，不会建链。这是记忆合并的召回精度上限所在（无语义相似度）。

### 5.2 过期机制

- `expires_at` 由调用方传入（`services.remember` 原样透传，services.py:97-113），内部 `_now()` 生成 UTC ISO8601（117-119 行）。
- `cleanup_expired`（360-383）：`WHERE expires_at != '' AND expires_at <= now AND 未被替代`，逐行删 FTS + 删内容，单事务。
- **三个局限**：
  1. 过期判断是**字符串字典序比较**（`expires_at <= ?`，373 行）——对同格式同 UTC 偏移的 ISO 字符串成立；一旦调用方传入不同格式（`2025-08-15 12:00:00`）或不同时区偏移（`+08:00`），比较结果静默错误（如 `20:00+08:00` 字典序大于 `13:00+00:00`，但实际是同一时刻）。
  2. **已被 superseded 的过期记忆永不过期清理**（372 行条件排除）——与"历史版本永久保留"叠加，库只增不减。
  3. 无自动调度：必须外部显式调 `cleanup_memories`（services.py:133-135）。

### 5.3 压缩机制

**本模块不实现压缩，只提供候选**：`find_compressible(max_age_days=30, keep_recent=10)`（385-419）返回"超过 30 天且不在最近 10 条内"的有效记忆候选（id/fact/tags/category/created_at），由外部 Agent 判断生命周期后调用 `update`/`update_batch` 完成压缩（docstring 393 行）。

**注意**：docstring 提到 `update_batch`，但类里**只有 `update`，没有 `update_batch`**——API 与文档不一致（见问题 P11）。`dreamer.py`（未在本次范围）是实际的"做梦/整合"消费者，`services.dream`（services.py:77-89）对外暴露。

### 5.4 记忆 FTS 同步清单

| 操作 | FTS 同步 | 行号 |
|---|---|---|
| add | 插 FTS（tokenize 后） | 170-176 |
| delete | 先删 FTS 再删内容 | 334-337 |
| update | 删 FTS → 改内容 → 重插 FTS | 436-448 |
| cleanup_expired | 先删 FTS 再删内容 | 379-380 |
| 外部直改 memories | **无同步** | — |

---

## 6. 中文分词策略

**两级降级**（tokenizer.py:1-8 docstring）：

```
jieba 可用 ──→ jieba.cut 精确分词（索引侧）
          └──→ 查询侧：整段短语 + jieba 子词 + 相邻 bigram，OR 连接
jieba 不可用 → bigram + 单字 + 英文完整词（索引侧）
          └──→ 查询侧：连续中文短语 + bigram
```

**索引侧（`tokenize_text`）**：
- 文本先被 `_MIXED_SEGMENT_RE` 切成"纯中文段 / 字母数字段"交替（44-47 行）；
- 中文段：`jieba.cut`，过滤空 token 与噪音（`is_noise_token`：Markdown 符号、**纯数字**、空白，20-27 行）；
- 英文段：`_ALNUM_RE.findall` 取 `[A-Za-z0-9_]+` 完整词；
- 降级 `_bigram_tokenize`（113-141）：对每个连续中文串输出 **整串 + 每单字 + 每相邻二元组** 三套 token——"今天天气" → `今天天气 今 天 气 今天 天天 天气`（整串 token 几乎无命中价值，属冗余）。

**查询侧（`tokenize_query`）**：
- 中文段生成：`"整段"` 短语 + 每个 jieba 子词 `"子词"` + 每个相邻二元 `"xx"`，全部 `OR`（94-102 行）；
- 英文段：不引号的裸词（104-106 行）；
- 无 token 时**返回原始文本**（107 行）——这是问题 P6 的根源。

**关键事实**：
1. FTS5 表本身用默认 unicode61 tokenizer，入库的已是空格分隔的预分词串，unicode61 再按空格切分——**"预分词 + 空格拼接"模式**在 bm25 与 memory 中一致，查询端引号短语与索引 token 精确对齐。
2. 整段短语 `"今天天气很好"` 在索引里并不存在对应 token（索引只有 jieba 词/二元组），属于无害但无效的查询项。
3. **纯数字被当噪音丢弃**（`_NOISE_RE` 含 `\d`）：文档里的"2024"、"007"不进索引，查"2024"必然空结果。
4. 英文大小写不敏感（unicode61 折叠），下划线 `_` 在索引端被保留为 token 字符、在 FTS 端被当分隔符——"hello_world" 索引成 `hello world` 两个连续 token，查询端一致，无问题。
5. 首次 `jieba.cut` 触发词典懒加载，首个 chunk 的分词有明显延迟（无 `jieba.initialize()` 预热）。

---

## 7. 模块耦合与设计模式

### 7.1 耦合度评估

| 模块 | 依赖 | 被依赖 | 评价 |
|---|---|---|---|
| tokenizer | 无（jieba 可选） | bm25, memory, related, expander | 理想叶子 |
| chunker | 无 | importer | 理想叶子 |
| parsers | 无（docx/openpyxl 懒加载） | importer | 理想叶子 |
| dedup | 无 | importer | 理想叶子 |
| manifest | 无 | importer | 理想叶子 |
| config | 无 | 全部 | 理想叶子 |
| tagger | config | importer | 低耦合 |
| bm25 | tokenizer | importer, searcher, services, related, clean_orphans | 中 |
| memory | tokenizer | searcher, services | 中 |
| summarizer | config, llm | importer | 低 |
| importer | config/tagger/manifest/parsers/dedup/chunker/summarizer(+懒 bm25) | services | 高（编排者，合理） |
| searcher | config/bm25(+懒 tokenizer/rewriter/llm_rewriter/expander/related/memory) | services, MCP server | 高（编排者，合理） |
| services | 全懒加载 | CLI/MCP | 门面 |

**耦合特征**：
- **好**：核心算法模块（分词/分块/解析/去重/清单）零依赖、可单测；`tests/` 下有对应的 test_tokenizer/test_chunker/test_dedup/test_parsers/test_manifest 等。
- **中**：bm25/memory 与 SQLite 直接耦合（无仓储层），SQL 字符串拼接散落在两处；`RewriteRule` 在 config.py（57-63）与 rewriter.py（17-23）**重复定义**，config 加载时手动映射（config.py:199-208）——两处改一处漏是隐患。
- **循环化解**：services→importer→bm25 等潜在环全部用"函数内懒加载 import"（如 importer.py:213、searcher.py:34/42/140、services.py 全部）绕开，代价是类型检查器无法静态推导，mypy 需 `type: ignore` 兜底（importer.py:134/142）。

### 7.2 设计模式清单

| 模式 | 位置 | 说明 |
|---|---|---|
| 策略 + 注册表 | searcher.py:15-25/97-105 | `SearchEngine` ABC + `_ENGINES` + `register_engine`（插件点） |
| 插件注册表 | parsers.py:15-26/80-89 | `register_parser` 扩展新格式 |
| 门面（Facade） | services.py:11-166 | `Shibei` 统一 CLI/MCP/Python 三入口，方法级懒加载 |
| 模板方法/接口 | memory.py:34-73 | `AbstractMemoryStore` ABC |
| 数据类配置 | config.py 全篇 | dataclass 树 + 属性展开 |
| 优雅降级 | tokenizer/summarizer/llm/bm25 | jieba→bigram、LLM→""、FTS 损坏→重建 |
| 双写同步（手动） | memory.py/bm25.py | 内容表与 FTS 表手工保持 rowid 对齐 |
| 模块级单例缓存 | tokenizer.py:29-41 | jieba 可用性缓存 |

---

## 8. 弱点、潜在 Bug、安全隐患与改进建议

### 8.0 实验验证（本报告论断的事实基础）

在项目 venv 中对 FTS5 行为做了 3 个最小复现：

| # | 实验 | 结果 | 对应问题 |
|---|---|---|---|
| E1 | 构造 contentless 表，插 3 行，`MATCH '银行 OR 风控'` 取 rank | 最相关行 rank 最负（-1.57e-6 vs -1e-6），`1/(1+\|rank\|)` 后最相关行得分**更低** | P5（方向反转） |
| E2 | `tokenize_query("###")` 后直接 `MATCH` | 返回原文 `"###"`，`sqlite3.OperationalError: fts5: syntax error near "#"` | P6（噪音查询崩溃） |
| E3 | `DELETE FROM ft WHERE rowid=1`（contentless） | `cannot DELETE from contentless fts5 table` | 解释 bm25 必须整表重建 |

### 8.1 按文件列出的问题与建议

#### config.py

- **P1 配置健壮性**（157-158、176、187、212、216 行）：`StorageConfig(**raw["storage"])` 遇到未知键直接 `TypeError` 崩溃；拼错的键（如 `storag:`）则被静默忽略，两种失败模式相反。建议：写一个 tolerant 的 dict→dataclass 合并器（只取已知字段 + 警告未知字段）。
- **P2 环境变量覆盖粗糙**（163-164 行）：`SHIBEI_SOURCES.split(",")` 不 strip 空白；且是整表替换而非合并。建议 `[s.strip() for s in ... if s.strip()]`。
- **P3 `search_engine` 无校验**（config.py:14）：配置任何字符串都不报错，运行时才 fallback（searcher.py:121-125 仅 log warning）。建议加载时校验白名单。
- **P4 自定义 prompt 静默失效**（summarizer.py:64-67 + config.py:97）：`prompt.format(content=..., max_length=...)` 若用户模板含其他占位符（如 `{foo}`）会抛 KeyError，被 except 吞掉后**所有摘要静默变空串**。建议 `format_map` + 兜底，或在配置加载时校验模板。

#### tokenizer.py

- **P5（高）score 归一化方向反转**：见 §8.0 E1。`bm25.py:199` 与 `memory.py:269` 的 `1.0/(1.0+abs(rank))` 中，FTS5 的 bm25 rank **越负越相关**，`abs` 之后相关度越高得分反而越低（实验：最相关行 0.9999984 < 最不相关行 0.9999990；在大语料下 rank 可达 -10~-30，反转肉眼可见，如 rank=-15 → 0.0625，rank=-3 → 0.25）。排序本身用 `ORDER BY rank` 不受影响，但**返回给 Agent/用户的 `score` 字段语义颠倒**，任何按 score 做阈值/排序/展示的消费方（含 `Searcher._search_memories`，searcher.py:149）都会出错。修复：`score = 1 - 1/(1+abs(rank))`（或直接对 `-rank` 做归一化），并加单测断言"更负 rank → 更大 score"。
- **P6（高）噪音查询崩溃**：`tokenize_query` 在无 token 时返回**原始文本**（tokenizer.py:107、168），纯噪音查询（`"###"`、`"---"`、`"***"`、空符号串）进入 `MATCH` 后抛 `sqlite3.OperationalError: fts5: syntax error`，经 `bm25.search`/`Searcher.search` 直接冒泡（MCP 场景即 500）。修复：返回 `""` 并在 `bm25.search`/`memory._search_bm25` 开头对空表达式短路返回空结果。
- **P7 纯数字不可检索**（tokenizer.py:20、27）：`_NOISE_RE` 把 `2024`、`007` 整体判为噪音丢弃，含年份/编号的文档搜不到。建议：至少保留 4 位以上数字串，或只过滤"纯 Markdown 符号"。
- **P8 jieba 首切延迟**：无 `jieba.initialize()` 预热（tokenizer.py:60-68）。大库首次导入首个 chunk 会卡在词典加载。建议 importer 启动时预热一次。

#### bm25.py

- **P9 每次搜索都跑 DDL**：`BM25Index.__init__` 无条件执行 `_init_db`（28-30 → 32-80），包含 `CREATE TABLE IF NOT EXISTS`、两次 `PRAGMA table_info`、ALTER 检查；`Searcher.search` 每次请求新建 `BM25Index`（searcher.py:80）与 `MemoryStore`（searcher.py:142，同样跑 DDL，memory.py:89-115）。MCP 服务高 QPS 时是实打实的开销。建议：schema 版本号存 `PRAGMA user_version`，版本匹配时跳过 DDL；或将索引实例提升为进程内单例。
- **P10 tags 过滤是子串 LIKE**（bm25.py:169、173，related.py:79 同）：`c.tags LIKE '%{tag}%'`——(a) 短 tag（如 `in`）会误命中 `interview`；(b) tag 内含 `%`/`_` 会变成通配符。建议 tags 列改用定界存储（如 `,tag,` 包边）做 `LIKE '%,' || ? || ',%'`，或拆 tags 关联表。
- **P11 删除即全量重建**（bm25.py:82-95、284、309）：contentless 不支持逐行 DELETE（E3 验证），每次 `remove_sources`/`delete_by_rowids` 都 O(N) 重建 FTS 并重跑全量 tokenize。对几万 chunk 的库，一次增量导入（通常总有文件变更）就是全量重索引。建议：改用 external content 模式 + FTS5 触发器（或 `content=''` + 逐行 `'delete'` 特殊命令），把删除降到 O(1)。
- **P12 无 WAL / busy_timeout**（bm25.py:33 等 10 处 `sqlite3.connect(..., timeout=30.0)`）：默认 rollback journal，MCP server 与 CLI 并发写时易 "database is locked"。建议统一 `PRAGMA journal_mode=WAL` + `busy_timeout=30000`（可封装一个 `connect()` 帮助函数，顺带消掉 10 处重复样板）。
- **P13 结果截断无省略标记**（bm25.py:201、204）：`content[:300]`/`parent_text[:800]` 硬切且无 `…` 标记、无高亮信息；长中文被切成半个词。建议至少加省略号，进阶可返回 FTS5 `snippet()`/`highlight()`。
- **P14 RRF 预筛窗口**（bm25.py:149-150）：两路各取 `limit*3` 就截断，大库下 RRF 只融合截断子集，理论上漏掉"两路都排 4×limit 之外但融合后应进入 top"的候选。影响有限，可注明为已知近似。

#### memory.py

- **P15 过期比较是字符串字典序**（memory.py:364-375）：只对"同格式同 UTC 偏移的 ISO 串"成立；`expires_at` 来自外部调用方（services.py:103 透传），传入 `+08:00` 或 `YYYY-MM-DD HH:MM:SS` 格式即静默误判。建议 `add()` 入口统一解析/归一化为 UTC epoch 或固定格式。
- **P16 版本链无限增长、永不清扫**（memory.py:360-383 的排除条件 + 无裁剪入口）：被 superseded 的过期记忆既不被 cleanup_expired 删除，也没有任何 TTL/数量上限；长链 + 高频写入下库单调膨胀。建议加"被替代链头超过 N 天/链长超过 M 即可物理删除"的可选策略。
- **P17 FTS 同步无触发器**（memory.py:107-114 + 170-176/334/436-448）：任何直改 memories 表的 SQL（未来功能、手工维护、迁移脚本）都会让 memories_fts 静默失同步——搜索漏召回且无任何报错。建议在 `_init_db` 建三个触发器（INSERT/UPDATE/DELETE）把同步下沉到数据库层，代码里的手动同步改为双保险。
- **P18 `delete` 的 LIKE 前缀兜底**（memory.py:323-331）：`WHERE id LIKE '{memory_id}%'`——若传入非完整 id（截断 UUID），可能命中多条并**静默删掉第一条**；且后面 `superseded_by` 清理既用 `=` 又用 `LIKE`（338-345），冗余且不一致。建议只用精确匹配（id 有 UNIQUE 约束）。
- **P19 `update` 不维护链语义**（memory.py:421-450）：改 fact 后 supersedes/superseded_by/category 原样保留；压缩改写后的事实可能与链上对象不再相似，链关系失真。建议 update 后可选地重跑相似度并重建链，或至少文档化该行为。
- **P20 add 的读-写分离竞态**（memory.py:146-153 与 154-183 是两个连接）：并发 add 可能同时对同一旧记忆写 superseded_by，后者覆盖前者。单 Agent 场景可接受，MCP 多客户端时应加事务或唯一约束。
- **P21 `_find_most_similar` 对 token 集合做 Jaccard**（memory.py:197-204）：与词序/词频无关（"A喜欢B"与"B喜欢A" Jaccard=1.0 必 supersede）；同义词不识别。0.7 阈值在中文口语场景偏激进。建议至少记录为已知特性，或引入词频加权。
- **P22 压缩 API 与文档不符**：`find_compressible` docstring（memory.py:393）提到 `update_batch`，类中不存在该方法。建议补实现或改文档。
- **P23 category 列 token 化不一致**（memory.py:175）：fact/tags 预分词、category 原样入库，FTS 查询对 category 的中文整串匹配依赖 unicode61 行为（CJK 连续串=单 token），与 fact 的检索行为不一致。建议统一预分词。

#### importer.py / manifest.py / dedup.py

- **P24（高）`full=True` 并非"全量重建"**：docstring 说"全量重建"（importer.py:44-45），注释说"full 模式下清空 hash store"（162-163），但代码只对 `to_remove` 清 hash（163-169），**当前文件的旧 hash 全部保留**。后果：① 不变 chunk 被 `dedup_chunks` 跳过、不重入库——所谓"全量"对存量内容实际是 no-op；② 更糟的是 `sources_to_clear` 在 full 模式只含 `to_remove`（186 行），若 chunking 配置变了（max_chars/overlap/parent_window），旧 chunk 不删、新 chunk 因 hash 不同重入 → **bm25 里同一文件出现新旧两套重复 chunk**。修复：full 模式应清空整个 chunk_hashes.json 并先 `bm25.remove_sources(全部 source)` 再全量重入。
- **P25 每次导入每文件至少读 3 遍**：解析读 1 遍（importer.py:95）、`classify` 算 hash 读 1 遍（manifest.py:67）、末尾 `manifest.set_hash` 又算 1 遍（importer.py:193 + manifest.py:43-45）。未变更文件也有 2 遍全量读取。建议：`classify` 把算好的 hash 返回复用，manifest 更新直接用它。
- **P26 三份状态非原子**：bm25（SQLite）删除与新增是**两个独立事务**（importer.py:219-223），manifest.json / chunk_hashes.json 都是无临时文件直写（manifest.py:25-28、dedup.py:52-55）。任一步崩溃都会留下"bm25 有行但 manifest 无记录 / hash 缺失 → 下次导入重复入库"的不一致。建议：bm25 删除+新增合一事务；两个 JSON 用 tmp+rename 原子写。
- **P27 摘要与去重顺序不当**（importer.py:154-158 先摘要、174-183 后去重）：LLM 摘要对**将被去重丢弃的 chunk** 也白白调用（启 LLM 时导入成本翻倍）。建议先去重、再对 unique chunks 摘要。
- **P28 `new_hashes` 死代码**（importer.py:174-176）：`dedup_chunks` 返回的 `new_hashes` 未使用，随后 178-181 又手工重算一遍（`hash_store.has` 恒为 False，等于无条件 add）。建议直接用返回值，删掉冗余循环。
- **P29 无文件大小上限**（parsers.py:39/43/71、manifest.py:45）：`read_text`/`read_bytes` 整文件进内存，配置里加了个 2GB txt 就 OOM。建议扫描时跳过超过阈值（如 50MB）的文件并记 warning。
- **P30 扫描效率**：每扩展名一次 `rglob`（importer.py:62-63），5 个扩展 = 每 source 目录 5 次全树遍历；重复 source 目录也会重复扫。建议单次遍历按后缀分发。
- **P31 `slug` 相对/绝对路径不一致风险**（importer.py:122）：`slug = str(f)`，若配置 source 是相对路径，rglob 产出相对路径，source 键随 CWD 变化 → `remove_sources` 匹配不上 → 陈旧 chunk 残留。建议统一 `resolve()`。
- **P32 跨文件去重的数据丢失**（dedup.py:74-103 + importer.py:178-181）：同一内容只留首见 source，后见文件**整 chunk 不入库**——若首见文件后来被删（`remove_by_source` 释放 hash），该内容在任何文件里都不可检索了（后见文件当初没存）。建议：hash store 记录 source 集合（或允许 N 个 source 共指一 hash），删除时保留仍存活的 source。
- **P33 manifest 容错不一致**：`ChunkHashStore._load` 捕获 JSONDecodeError/OSError 后静默重置（dedup.py:44-50），`Manifest._load` 不捕获（manifest.py:20-23）——manifest.json 损坏直接让导入崩溃。建议对齐容错。
- **P34 MD5 全量读内存**：manifest.py:43-45 `path.read_bytes()`；大文件内存峰值 = 文件大小。建议流式分块读。

#### chunker.py

- **P35 短 section 直接丢弃**（chunker.py:135）：`len(sec) < 10` 的标题节整体不进索引——含"# 结论""## 备注"这类短块会丢。建议阈值可配置或至少记日志。
- **P36 重复标点切分缺陷**（chunker.py:44-56）：`re.split` 的合并逻辑假设"分隔符后面紧跟非分隔符"，连续标点（如"你好。。世界"）会把第二个句号孤立成一句（`'你好。', '。', '世界'`）。建议用 `re.findall(r'[^boundary]*boundary?')` 重写。
- **P37 parent 文本按 child 冗余存储**（chunker.py:176、192-197 → bm25.py:120-124）：每个 child 行都带一份 parent_text，N 个 child 存 N 份重叠 parent，库体积放大（parent_window 越大越明显）。建议 parent 单独成表、child 存 parent_id 外键。
- **P38 overlap 按字符硬切**（chunker.py:94-95）：`buf[-overlap:]` 可能切断词/半角标点；对中文影响小，可接受，但值得注明。

#### searcher.py / related.py / expander.py

- **P39 每请求重建引擎**：见 P9（searcher.py:80、142）。
- **P40 `total` 是截断后的结果数**（searcher.py:85）：`len(results)` 已被 limit 截断，不是真实命中数；MCP 客户端无法判断是否还有更多。建议 `count(*)` 或返回 `has_more`。
- **P41 related 无缓存、逐查询计算**（searcher.py:133-137 → related.py:59-100）：`tags LIKE` fanout + 每候选 source 拉 5000 行做三路打分，宽标签（如 `personal-note`，config.example.yaml:34-35 自己都警告了）下每次搜索都很重。建议结果按 source 缓存（source hash + 内容 hash 做 key）。
- **P42 模糊扩展 O(词典×词长²)**（expander.py:182-194）：每次查询对词典全集跑编辑距离，同义词表大时查询延迟不可控。建议建 BK-tree / 限制词典规模，或把模糊匹配移到导入侧建索引。
- **P43 tag 传空串/None 语义模糊**（bm25.py:163-173）：`tag=""` 走无过滤分支；若调用方期望"无标签"，无法表达。低优先级。

#### llm.py / summarizer.py

- **P44 无重试/超时后无降级信号**：`LLMClient.chat`（llm.py:43-100）无重试；摘要失败仅降级空串，调用方无从区分"未启用"与"调用失败"。低优先级，可在结果里带 `summary_status`。

### 8.2 安全隐患汇总

| 风险 | 位置 | 现状与建议 |
|---|---|---|
| FTS5 查询注入 | tokenizer.py:80-110 | **已防御**：所有 token 双引号包裹，英文段经 `_ALNUM_RE` 白名单字符集（`[A-Za-z0-9_]`）过滤，无 `:`/`(`/`AND`/`NEAR` 等语法字符可逃逸；但 P6 的"无 token 返回原文"路径绕过了该防御（原文含 `:` 等会构造列过滤/语法错误）——修 P6 即闭环 |
| SQL 注入 | 全部 SQL | 表名列名全是模块常量，值全部参数化，**无注入面**（`LIKE '%{tag}%'` 的 `%` 只是通配符语义问题，非注入） |
| YAML 反序列化 | config.py:153、expander.py:75/82 | 全部 `yaml.safe_load`，**安全** |
| LLM prompt 注入 | summarizer.py:58-70 | 文档内容直接进 prompt（摘要/改写），恶意文档可诱导模型输出异常内容；属该架构固有风险，建议至少记录（无法根治，除非做指令隔离） |
| 自定义 prompt 格式串 | summarizer.py:64 | 见 P4，`format` 占位符缺失导致静默降级 |
| API key | llm.py:36-41 | 仅环境变量，无日志泄漏；`extra_headers` 可注入自定义头——无风险 |
| 无鉴权的本地服务 | server.py（未在本次范围） | 若 MCP server 绑定非 loopback，需鉴权；建议确认绑定地址 |
| 文件读取 | parsers.py | 只读配置指定目录，无路径穿越面；无大小上限（P29）属资源风险 |

---

## 9. 值得注意的问题清单

按影响排序（P 编号对应 §8 小节）：

### 高优先级（会产生错误结果或崩溃）

1. **P5 score 归一化方向反转**（bm25.py:199、memory.py:269）：FTS5 rank 越负越相关，`1/(1+abs(rank))` 使更相关的结果得分更低；已实验证实。排序不受影响，但所有消费 score 的地方（展示、阈值、Agent 决策）语义颠倒。
2. **P6 纯噪音查询抛 `sqlite3.OperationalError`**（tokenizer.py:107、168）：`tokenize_query("###")` 返回原文 `"###"`，FTS `MATCH` 直接语法错误，MCP 场景即 500；已实验证实。
3. **P24 `full=True` 全量导入不是重建**（importer.py:76-79、162-186）：hash store 未清空导致存量 chunk 去重跳过；chunking 配置变更时旧 chunk 不删、新 chunk 重入 → **bm25 重复数据**。注释与实现矛盾。
4. **P26 三份状态非原子**（importer.py:219-223 + manifest.py:25-28 + dedup.py:52-55）：bm25 删/增两个事务 + 两个 JSON 直写，崩溃即不一致，下次导入重复入库或漏删。

### 中优先级（数据质量 / 资源）

5. **P25 每次导入每文件 ≥3 次全量读取**（manifest.py:67 + importer.py:193）：增量导入的真实瓶颈。
6. **P9/P39 每次搜索/请求都跑 DDL 初始化**（bm25.py:32-80、searcher.py:80/142）：MCP 高 QPS 下不必要的开销。
7. **P15 过期时间字符串字典序比较**（memory.py:364-375）：时区偏移/格式不一致即静默误判。
8. **P16 版本链无限增长永不清扫**（memory.py:360-383）：被替代的历史 + 过期记忆只增不减。
9. **P17 记忆 FTS 无触发器、纯手动同步**（memory.py:107-114）：任何直改 memories 的 SQL 都会静默失同步。
10. **P32 跨文件去重导致内容"只在一处可检索"**（dedup.py:74-103）：首见文件删除后内容整体消失。
11. **P10 tags 子串 LIKE 误匹配**（bm25.py:169、related.py:79）。
12. **P11 文档侧删除即全量重建索引**（bm25.py:82-95）：增量导入的隐性 O(N) 成本。
13. **P21 版本链阈值基于 token 集合 Jaccard**（memory.py:197-204）：词序无关、同义词不识别，"A喜欢B"/"B喜欢A" 必 supersede。
14. **P12 无 WAL**：并发写易锁库。

### 低优先级（健壮性 / 一致性 / 体验）

15. **P7 纯数字不可检索**（tokenizer.py:20、27）。
16. **P1/P2/P3 配置健壮性**（config.py:157-164）：未知键崩溃 vs 拼错键静默、环境变量不 strip、search_engine 不校验。
17. **P18 delete 的 LIKE 前缀兜底可能误删**（memory.py:323-331）。
18. **P19 update 不维护链语义**（memory.py:421-450）。
19. **P22 `update_batch` 文档有、代码无**（memory.py:393）。
20. **P27 摘要先于去重，浪费 LLM 调用**（importer.py:154-158）。
21. **P28 `new_hashes` 死代码**（importer.py:174-181）。
22. **P29 无文件大小上限**（parsers.py、manifest.py:45）。
23. **P30 每扩展名一次全树 rglob**（importer.py:62-63）。
24. **P31 source 键相对/绝对路径不稳定**（importer.py:122）。
25. **P33 manifest 损坏即崩溃，与 dedup 容错不一致**（manifest.py:20-23）。
26. **P35 短 section（<10 字符）静默丢弃**（chunker.py:135）。
27. **P36 连续标点切分缺陷**（chunker.py:44-56）。
28. **P37 parent_text 按 child 冗余存储**（chunker.py:192-197）。
29. **P40 `total` 是截断后数量**（searcher.py:85）。
30. **P41 related 无缓存、逐查询计算**（related.py:59-100）。
31. **P42 模糊扩展 O(词典×词长²)**（expander.py:182-194）。
32. **P4/P44 LLM 摘要静默降级无信号**（summarizer.py:64-74）。

### 设计优点（保持）

- FTS5 注入防御到位（查询全引号 + 白名单字符集）；
- 优雅降级贯穿全链路（jieba→bigram、LLM→""、FTS 损坏→重建）；
- 核心算法模块零依赖、可单测；插件注册表（parser/engine）扩展点清晰；
- `services.Shibei` 门面 + 懒加载解环；
- contentless FTS 的"删行 + 整表重建"策略虽然 O(N)，但正确规避了 contentless 的删除限制（E3 验证其必要性）；
- 记忆版本链（supersedes）+ 过期 + 压缩候选的设计完整、职责清晰，压缩决策外置给 Agent 是合理取舍。

---

*报告完。全部论断基于工作区源码逐行阅读与 3 个 FTS5 运行实验；行号引用以当前版本为准。*
