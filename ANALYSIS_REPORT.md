# 拾贝（Shibei）智能层 / 工程配置 / 测试 深度分析报告

> 分析对象：`/Users/sihai/Documents/My Projects/shibei`
> 分析范围：智能/整理层（dreamer / consolidator / related / expander / rewriter / llm_rewriter / llm / summarizer / tokenizer）+ 工程配置（pyproject.toml / setup.sh / config.py / profile.py）+ 全部测试
> 分析方法：逐文件精读 + 实际运行测试套件 + 关键疑点实证验证（Python 3.12.13 venv，jieba 可用）

---

## 0. 测试运行结果（必读）

### 0.1 运行命令与环境

```bash
cd "/Users/sihai/Documents/My Projects/shibei"
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
```

- 项目根目录存在 `.venv`（Python 3.12.13，软链自 Homebrew python@3.12），pytest 已安装，`PYTHONPATH=src` 可正常 `import shibei`，无需额外安装。
- 运行过程中 jieba 正常加载（`jieba_available() == True`），分词走 jieba 路径。

### 0.2 结果总览

| 指标 | 数值 |
|------|------|
| 收集测试数 | **166** |
| 通过 | **166** |
| 失败 | **0** |
| 跳过 | **0** |
| 耗时 | **0.85 s**（收集 0.05s + 运行 0.80s） |
| 退出码 | 0 |

```
........................................................................ [ 43%]
........................................................................ [ 86%]
......................                                                   [100%]
166 passed in 0.85s
```

### 0.3 失败详情

**无失败、无跳过。** 全部 166 个测试一次性通过，且全程无网络请求（LLM 相关测试全部通过 monkeypatch / MagicMock 注入假客户端）。

### 0.4 按模块分布（测试函数数）

| 测试文件 | 测试数 | 覆盖模块 |
|---|---|---|
| test_chunker.py | 16 | 分块引擎 |
| test_memory.py | 14 | 对话记忆 + 版本链 |
| test_bm25.py | 9 | FTS5/BM25 + 分词 |
| test_expander.py | 8 | 查询扩展 |
| test_dreamer.py | 8 | Dreaming |
| test_dedup.py | 8 | 精确去重 |
| test_consolidator.py | 8 | 相似 chunk 聚类 |
| test_rewriter.py | 7 | 规则改写 |
| test_profile.py | 7 | Profile 管理 |
| test_server.py | 7（9 参数化） | MCP server |
| test_cli.py | 6 | CLI |
| test_context.py | 6 | 答前上下文 |
| test_summarizer.py | 6 | chunk 摘要 |
| test_tokenizer.py | 5 | 分词/噪音过滤 |
| test_parsers.py | 5 | 文档解析 |
| test_manifest.py | 5 | 增量追踪 |
| test_llm_rewriter.py | 5 | LLM 改写 |
| test_llm.py | 4 | LLM 客户端 |
| test_basic.py | 4 | 配置加载 + tagger |
| test_importer.py | 4 | 导入 |
| test_searcher.py | 3 | 搜索入口 |
| test_related.py | 3 | 轻量关联 |
| test_list_sources.py | 3 | 来源列表 |
| test_importer_summary.py | 3 | 摘要导入 |
| test_clean.py | 3 | orphan 清理 |
| test_services.py | 1 | 服务层 |

---

## 1. 项目概览与本次分析范围

拾贝是「中文轻量知识库 + Agent 长期记忆」系统：SQLite FTS5 BM25 全文检索（正文+摘要双列 RRF 融合）+ 对话记忆（版本链）+ MCP server（stdio，9 个工具）。本次深入分析的**智能/整理层**是「检索之外的思考能力」：Dreaming 洞察、相似 chunk 合并、轻量关联、查询改写/扩展、LLM 客户端与摘要，以及支撑这些能力的 Profile 机制、工程打包与测试。

设计哲学（从 git 历史可印证）：**搜索与整理阶段零 LLM 依赖**——`refactor!: 彻底移除 LLM 依赖，dreaming/整理改由外部 Agent 执行`；LLM 仅用于可选的 chunk 摘要与查询改写，且失败一律降级、不阻断主流程。整体代码风格统一：dataclass 配置、模块级日志、函数式小函数 + 类封装、防御式 try/except 降级。

---

## 2. 智能/整理层模块逐个详解

### 2.1 dreamer.py — Dreaming 洞察 + 自动整理（395 行，重点）

#### 2.1.1 定位
`dream()` 是「知识库自主思考」入口：扫描 chunk → 提取关键词 → 跨文档交叉引用 → 标签模式 → 生成洞察 → 顺带执行零 LLM 的 housekeeping 整理，并把结果持久化到 `db_path/dream_result.json` 供后续 diff。

#### 2.1.2 关键词提取（L68-103 `extract_keywords`）
- **首选 jieba**：`jieba.analyse.extract_tags(text, topK=8)`（TF-IDF 权重），过滤 `is_noise_token` 与停用词。
- **降级正则**（无 jieba 时）：
  - 中文：`[\u4e00-\u9fff]{2,6}` 连续片段，**加权 ×2**（同一词 append 两次进 Counter）；
  - 英文：`[A-Za-z]{3,}`，**但 `w.isupper()` 的全大写词会被排除**（见 7.x 问题）。
- 停用词表（L42-65）：约 50 个中文单字/多字 + 40 个英文词 + `https/http/com/www` 等网址噪音，合并为 `STOP_WORDS` 集合（表内存在重复项如「可」「以」，因转 set 无害）。

#### 2.1.3 chunk 加载与采样（L106-136 `load_chunks_from_bm25`）
- 读 `db_path/fts5.db` 的 `chunks` 表；`text` 字段**截断为前 500 字符**（L133，见 7.x 问题）。
- 采样逻辑（L115-120）：`total > max_chunks` 时 `step = total // max_chunks`，取 `rowid % step == 0` 的行，`LIMIT max_chunks`。**实测存在偏差**（见 7.x 问题）。

#### 2.1.4 洞察生成管线（4 类洞察）
1. **主题图**（L149-157 `build_topic_graph`）：关键词 → 关联 source 集合（仅保留连接 ≥2 文档的关键词）。
2. **跨文档引用**（L160-189 `find_cross_references`）：文档两两之间共享关键词的聚合，`strength` = 共享关键词数，按强度降序；跳过 basename 相同的文档对（防 readme 自连）。
3. **标签模式**（L192-209 `find_patterns`）：按 tag 聚合关键词，取出现 ≥2 次的高频词。
4. **洞察文本**（L212-254 `generate_insights`）：① 全局高频主题 Top5；② 最强 3 条跨文档连接；③ 标签下反复出现的主题；④ **孤立文档**（无任何跨文档连接的 source）。

#### 2.1.5 自动整理 housekeeping（L257-320 `_run_housekeeping`，零 LLM）
默认配置（L31-38）：
```python
DEFAULT_HOUSEKEEPING = {
    "cleanup_expired_memories": True,      # 清理过期记忆
    "compress_max_age_days": 30,           # 可压缩候选：30 天前
    "compress_keep_recent": 10,            # 保留最近 10 条
    "consolidate_threshold": 0.6,          # 相似 chunk 阈值
    "consolidate_min_cluster_size": 2,
    "consolidate_max_chunks": 500,
}
```
- 记忆侧：`MemoryStore.cleanup_expired()` 物理删除过期记忆；`find_compressible()` 找出旧记忆候选（**不自动压缩**，由外部 Agent 调用 `shibei_update_memory` 执行）。
- 知识库侧：调用 `consolidator.consolidate()` 发现相似 chunk 候选（**不自动 apply**）。
- 每个子任务独立 try/except，失败只记 warning，不影响整体 dream 结果。
- 设计契约（与 MCP 工具 `shibei_dream` 对应）：Shibei 自身永远不调 LLM，只产出「候选」，决策权交给宿主 Agent。

#### 2.1.6 输出与持久化
- `format="brief"`：stats + insights + housekeeping；`format="full"` 额外返回 `cross_references[:10]` 与 `patterns`。
- `persist=True` 时写 `db_path/dream_result.json`（ensure_ascii=False，UTF-8），失败仅 warning。
- 空库返回 `{"status": "empty", ...}`。

### 2.2 consolidator.py — 相似 chunk 聚类合并（301 行）

#### 2.2.1 算法：**不是 MinHash/LSH**，是朴素两两相似度 + 传递闭包
- **相似度度量**（L29-38 `_overlap_coefficient`）：Overlap 系数 `|A∩B| / min(|A|,|B|)`，刻意不用 Jaccard——因为「一段是另一段的子集」时 Jaccard 偏低而 Overlap 能识别。
- **相似对构建**（L41-56 `_build_similarity_pairs`）：对全部 chunk 两两计算（**O(n²)**），超过 `threshold`（默认 0.6）记为一条边。token 集合来自 `tokenize_text(...).split()`。
- **聚类**（L59-86 `_cluster_by_edges`）：把相似边建成邻接表，DFS 求**传递闭包连通分量**。注意：传递闭包会把「A~B、B~C」的 A 与 C 也拉进同一簇（即使 A、C 不相似），代码注释已说明这是有意的简化。
- 单点簇（孤立 chunk）在 L137 通过 `min_cluster_size` 过滤，不产出。

#### 2.2.2 启发式摘要（L89-98 `_heuristic_summary`）
无 LLM：对簇内 chunk 文本去重后 `"\n".join` 拼接。

#### 2.2.3 consolidate() 主入口（L101-171）
- 参数：`threshold=0.6, min_cluster_size=2, max_chunks=500, apply=False`。
- 结果结构：`{status, total_chunks, cluster_count, clusters:[{size, sources, summary, chunks:[{_rowid, source, text[:200]}]}]}`。
- `apply=True` 时调用 `_apply_clusters` 直接写库。

#### 2.2.4 写库合并（L174-219 `_apply_clusters` / L222-301 `apply_consolidation`）
- 新 chunk 的 `source` 形如 `__consolidated__/{YYYYmmddTHHMMSS}/{idx}#{原文件名}`，tags 固定 `"consolidated"`，通过 `BM25Index.add_batch` 写入（7 元组：source, file_path, tags, content, parent_text="", parent_id="", summary=""）。
- 用 `BM25Index.delete_by_rowids` 删除原 chunk（内部会重建 FTS5 索引，规避 contentless FTS5 大批量删除的 malformed 问题）。
- `apply_consolidation` 是**外部 Agent 驱动版**：接受 `{rowids, summary, sources}` 列表；`summary` 为空时自动用启发式摘要兜底；rowids 非 int 或摘要为空则计 invalid 跳过。
- 删除与插入不在同一事务内（两个独立连接调用），中途失败会留下半完成状态（见 7.x）。

### 2.3 related.py — 轻量关联发现（195 行）

`RelatedFinder` 基于已有 BM25 索引元数据计算关联，不引入图数据库。入口 `find_related(source, limit)` → `_find_related_bm25`（SQL 阶段）→ `_build_relations`（内存计算阶段）。

#### 2.3.1 召回阶段（L59-100 `_find_related_bm25`）
1. 取目标 source 的 tags；
2. 用 `tags LIKE '%tag%'` 找候选 source（用 LIKE 而非全表扫描，注释称避免大库全表扫——但 LIKE 本身也无法走索引，见 7.x）；
3. 候选非空则 `WHERE source IN (...)` 一次拉取（上限 5000 行）；为空则回退「同目录 + file_path LIKE」样本（上限 1000 行）。

#### 2.3.2 三种关联方式（L102-190 `_build_relations`）
1. **标签共现**（L133-145）：`score = |共同标签| / max(|A|,|B|)`，reason「共享标签: ...」。
2. **Markdown 链接**（L147-160）：解析 `[[wikilink]]` 与 `[text](target)`（`_extract_markdown_links`，过滤 http(s)/# 外链），`_normalize_link` 把目标解析为绝对路径；命中一条 +0.5 分（封顶 1.0），reason 追加「文档内链接」。
3. **关键词共现**（L162-185）：基于 BM25 token 的 `Counter` 交集，`score = 共同 token 计数 / max(总数)`，reason 附 Top3 词。注释标注为「Jaccard」，但公式实际是 min-overlap 风格（见 7.x）。

最终按 score 降序取 `limit` 条，score 保留 3 位小数，输出 `{source, score, reason}`。`Searcher` 侧（searcher.py L133-136）默认**关闭**（`related.enabled=false`），开启时对前 `top_k` 条命中结果计算 related。

### 2.4 expander.py — 同义词 + 模糊扩展（272 行）

零向量库的召回增强：把查询词扩展成 OR 词组喂给 BM25。

#### 2.4.1 查询拆词（L22-61 `_extract_query_terms`）
- 有 jieba：对中英混合片段逐段 `jieba.cut` 拆词（保留整段 + 切分词）；英文/数字走 `[A-Za-z0-9_]+`。
- 无 jieba：连续中文片段 + 英文 token。
- 长度 < `min_query_len`（默认 2）的词直接丢弃，避免扩展膨胀。

#### 2.4.2 同义词表加载（L64-110 `_load_synonyms`）
- 支持 YAML/JSON，`.yaml/.yml` 走 PyYAML，`.json` 走 json；其他后缀先试 YAML 再试 JSON。
- 规范化：`{key: "单值"}` 与 `{key: [列表]}` 均可；每个 key 的列表 = `[key] + values` 去重保序。非 dict 数据 warning 后返回空。

#### 2.4.3 编辑距离（L113-137 `_edit_distance`）
标准 Levenshtein 动态规划，**优化为两行滚动数组**，且始终保持短串为列方向（省内存），实现正确（测试覆盖 `""` 与中文串）。

#### 2.4.4 扩展逻辑（L140-268）
- `_lookup_synonyms`：仅精确 key 命中（L165-175 的注释宣称支持「查询词是 key 子串」的查找，但代码直接 `return []`——**死代码/注释与实现不符**，见 7.x）。
- `_fuzzy_candidates`：遍历整个词典求编辑距离（先按长度差 > max_distance 剪枝），按 `(距离, 长度, 词序)` 排序取前 N 个。
- `expand()`：逐词「同义词命中 → 扩展；否则 fuzzy 命中 → 原词+候选；否则原词」，去重保序，返回 `{original, expanded, expansions, fuzzy}` 结构化结果；`expand_query_for_bm25` 直接取字符串。

### 2.5 rewriter.py — 规则查询改写（67 行，最简模块）

- `RewriteRule` dataclass：`source/target/enabled`。
- `QueryRewriter.rewrite()`：按规则列表**顺序**对查询串做 `str.replace` 子串替换，返回 `{original, rewritten, applied:[{from,to}]}`。
- 纯字符串替换、无正则、无智能；规则顺序敏感（若规则 target 命中后续规则的 source 会级联替换，见 7.x）。默认关闭（`rewriter.enabled=false`）。

### 2.6 llm_rewriter.py — LLM 查询改写（140 行）

- 默认 prompt（L15-23）：要求模型输出「空格分隔的关键词/同义表达」，≤ max_terms 个词，保留专有名词，去人称/语气词。
- `available()`：要求已有 client 且 `_api_key()` 非空；无 `SHIBEI_LLM_API_KEY` 环境变量时直接不可用。
- `rewrite()`：调用 `LLMClient.chat` → `_parse_terms` 解析；任何异常降级返回原查询并附 `error`（`llm_unavailable` / 异常消息 / `empty_terms`），**不阻断搜索**。
- `_parse_terms`（L121-140）：去引号、折叠空白、兼容中英文逗号/顿号分隔、过滤 >40 字符的「句子」、去重、截断到 max_terms。
- 注意：内部访问 `self._client._api_key()`（跨类访问私有成员，见 7.x）。

### 2.7 llm.py — LLM 客户端（100 行）

- **实现**：纯 `urllib.request`，零第三方 HTTP 依赖（符合项目「轻量」哲学）。
- `LLMOptions` dataclass：`base_url`（默认 OpenAI v1）、`model`（gpt-4o-mini）、`timeout=30.0`、`temperature=0.3`、`max_tokens=100`、`api_key`、`extra_headers`。
- API key 优先级：`options.api_key` 显式值 > 环境变量 `SHIBEI_LLM_API_KEY`；两者皆无 → `ValueError`（带明确提示）。
- `chat()`：POST `{base}/chat/completions`，payload 支持 `**extra` 覆盖默认参数；错误处理：
  - `HTTPError`：读 body 记日志后**重新抛出**；
  - `URLError`：记日志后重新抛出；
  - `JSONDecodeError`：转 `ValueError`；
  - 响应缺 `choices[0].message.content`：转 `ValueError`。
- **无重试、无退避、无流式**：单次 `urlopen(timeout=timeout)`（见 7.x 改进建议）。

### 2.8 summarizer.py — chunk 摘要（80 行）

- `ChunkSummarizer(cfg)`：`cfg.enabled` 为 False 时不构造 client，`summarize()` 直接返回 `""`。
- 输入按 `max_content_length`（默认 1000 字符）截断；prompt 模板 `{content}` + `{max_length}` 可自定义。
- 输出清理：strip 引号、折叠换行；结果 ∈ {`""`, `"无"`, `"None"`, `"null"`} 视为空。
- 任何异常 → warning + 返回 `""`（降级不阻断导入）。测试覆盖 disabled/empty/成功/截断/失败/参数透传 6 个场景。

### 2.9 支撑模块 tokenizer.py（168 行，顺带）

- `jieba_available()`：模块级缓存检测结果。
- `tokenize_text()`：索引侧分词——jieba 精确分词（过滤噪音 token）或 bigram+单字降级。
- `tokenize_query()`：查询侧——jieba 时输出 `"整段" OR "词1" OR "词2" OR "bigram..."` 的 FTS5 OR 表达式；降级为短语+bigram。
- `is_noise_token()`：`^[#\-*=|>_+\s\d]+$` 匹配纯格式符号/纯数字。
- 是 bm25/memory/dreamer/expander 共用的分词底座，质量较好。

### 2.10 检索链路整合（searcher.py，顺带确认）

`Searcher.search()` 完整管线（searcher.py L33-94 BM25Engine）：
```
规则改写(rewriter) → [可选] LLM 改写(llm_rewriter) → tokenize_query
→ [可选] 同义词/fuzzy 扩展(expander) → tokenize_query(扩展后)
→ BM25Index.search(RRF 融合 content_tokens + summary_tokens)
→ 附 memories(记忆召回) + related(可选)
```
与 CONFIG.md「rewriter 在 expander 之前执行」一致。`register_engine` 保留策略模式扩展点，当前仅注册 `"bm25"`。

---

## 3. Profile 多场景配置机制

文件：`src/shibei/profile.py`（77 行）+ `cli.py` 的 `profile` 子命令。

### 3.1 存储
- Profile 文件统一存放 `~/.shibei/profiles/<name>.yaml`（`PROFILES_DIR` 模块级常量）。
- API：`list_profiles()`（glob `*.yaml` 取 stem 排序）、`get_profile_path(name)`、`profile_exists(name)`、`create_profile(name, template=None)`、`delete_profile(name)`、`resolve_config_path(config=None, profile=None)`。

### 3.2 创建
- 同名已存在 → `FileExistsError`；template 指定但不存在 → `FileNotFoundError`。
- 无 template 时优先复制**当前工作目录**下的 `config.example.yaml`（L44 `Path("config.example.yaml")`，**相对路径依赖 CWD**，见 7.x）；文件不存在则写入一段内联最小配置（storage + sources + 兜底 tagging）。

### 3.3 解析优先级（L65-76）
```
--config 显式路径  >  --profile 名称  >  默认 "config.yaml"
```
- `--profile` 指定但 profile 不存在 → `FileNotFoundError` 并提示先 `shibei profile create`。
- CLI 侧：`--profile/-p` 全局参数（cli.py L504），`shibei profile list/create/delete/show` 子命令齐全。

### 3.4 与 Config 的关系
`load_config(path)` 只接受文件路径；Profile 机制只是「配置文件路径解析」的薄封装——切换 profile 即切换一份完整 YAML（每份 profile 是独立知识库配置，可指向不同 storage.path 与 sources），实现「面试/工作/日记多知识库」场景。**无合并/继承机制**，各 profile 完全独立。

---

## 4. 工程配置：依赖、打包、安装

### 4.1 pyproject.toml（37 行）
| 项 | 值 |
|---|---|
| 构建后端 | setuptools>=68 + wheel（`setuptools.build_meta`） |
| 包名/版本 | shibei 0.1.0（Alpha） |
| **requires-python** | **>=3.9** |
| 运行依赖 | `pyyaml>=6.0`、`jieba>=0.42` |
| 可选依赖 | dev: pytest>=7 / pytest-asyncio / mypy>=1.0；docx: python-docx；xlsx: openpyxl；jieba（重复）；all |
| 入口 | `shibei = "shibei.cli:main"` |
| 打包 | `[tool.setuptools.packages.find] where=["src"]`（src 布局） |

注意：`jieba` 同时出现在必装依赖与 `jieba`/`all` extra 中（冗余但无害）；无 `[project.urls]`/`authors`/`license` 文件（license 为 `{text="MIT"}` 文本声明）。**无 pytest 配置节**（无 `[tool.pytest.ini_options]`），测试依赖 `PYTHONPATH=src` 环境变量导入 src 布局。

### 4.2 setup.sh（40 行）
`set -euo pipefail`；`SHIBEI_PYTHON` 可覆盖解释器（默认 python3）；`pip install -e .`（quiet）；无 config.yaml 时复制 `config.example.yaml`；`mkdir -p ~/.shibei/db`。安装后提示 import / search / serve 三步。与 PRD「一键安装」对应，**没有 `shibei init` 命令的实现痕迹**（PRD 9.1 写了 `shibei init`，但 cli.py 未 grep 到 init 子命令——文档与实现的偏差，见 7.x）。

### 4.3 配置加载（config.py，218 行）
- 全部 dataclass：StorageConfig / ChunkingConfig / TagRule / PathTagRule / TaggingConfig / ExpanderConfig / RewriterConfig / RelatedConfig / LLMConfig / Config，默认值与 CONFIG.md 一致。
- `load_config()`：YAML 解析后逐节覆盖；环境变量 `SHIBEI_CONFIG`（配置路径）、`SHIBEI_SOURCES`（逗号分隔覆盖 sources）。
- LLM API key **刻意不进 YAML**，统一 `SHIBEI_LLM_API_KEY`（符合 PRD 6.2.1 的隐私设计）。
- 忽略目录清单 `DEFAULT_IGNORE_DIRS` 覆盖 .git/node_modules/.venv 等 17 项。
- 一个小坑：`load_config` 中 `StorageConfig(**raw["storage"])` 等为 `**` 展开，若 YAML 含未知键会 `TypeError` 崩（无宽容过滤，见 7.x）。

---

## 5. 测试覆盖分析

### 5.1 覆盖面（好）
- **全部核心模块都有直接测试**：tokenizer、bm25、chunker、dedup、manifest、importer、parsers、tagger（经 test_basic）、dreamer、consolidator、related、expander、rewriter、llm_rewriter、llm、summarizer、memory、searcher、context、cli、server、profile、clean、services、list_sources。
- 测试总量 166 个 / 26 个文件 / 约 3186 行，src 约 5385 行，规模配比合理。
- 关键行为路径覆盖到位：版本链（supersedes）、RRF 摘要召回、降级路径（LLM 失败 → 原文查询）、housekeeping 的「只发现不自动执行」契约、consolidate apply 的库写入与计数。

### 5.2 隔离性（优）
- 全部使用 `tempfile.TemporaryDirectory()` 或 pytest `tmp_path`，**无任何测试写入真实 `~/.shibei` 或仓库 data/**；
- 网络零依赖：`test_llm.py` 用 `monkeypatch.setattr("urllib.request.urlopen", ...)` 注入假响应；`test_summarizer`/`test_llm_rewriter` 用 MagicMock 假 client；
- `test_profile.py` 用 `monkeypatch.setattr("shibei.profile.PROFILES_DIR", tmp_path)` 重定向（隔离正确）；
- 环境变量用 `monkeypatch.setenv/delenv` 管理，互不污染。
- 0.85s 全绿也侧面证明无真实 IO/网络。

### 5.3 缺口（可改进）
1. **tagger.py 无独立测试文件**：仅 test_basic 中 2 个用例间接覆盖，path_rules 的 glob 后缀匹配、规则顺序、兜底规则边界无系统测试。
2. **无 dreamer 采样测试**：`load_chunks_from_bm25` 的采样分支（>max_chunks）无任何测试——实测该分支存在系统性偏差（见 7.x），测试盲区直接放过了它。
3. **consolidate apply 的数据保真无测试**：现有测试文本都 <500 字符，掩盖了「合并后内容被截断到 500 字符」的数据丢失 bug（见 7.x）。
4. **related 覆盖偏薄**（3 个测试）：关键词共现单独场景、链接归一化边界（绝对路径/锚点/查询串）、tag LIKE 转义（tag 含 `%`/`_`）均未覆盖。
5. **无性能/规模测试**：PRD 非功能要求「1 万 chunk 搜索 <1s、10 万 chunk 级别」，无基准测试；`_build_similarity_pairs` 的 O(n²) 在 500 chunk 上限下尚可，超出即退化。
6. **llm.py 边界未测**：timeout 透传、extra_headers、非 200 响应体解析、空 choices 之外的畸形结构。
7. **无端到端集成测试**：没有「真实 config.yaml + 真实目录导入 → 搜索 → dream → consolidate」的完整链路测试（test_cli 是进程级冒烟，未连真实库）。
8. 少量测试实现方式偏 hack：`test_llm_rewriter.py` 直接 monkeypatch `LLMQueryRewriter.__init__`（L66-75、L102-109），脆弱且难读，建议改为依赖注入假 client（类本身已支持 `client=` 参数）。

### 5.4 测试质量小结
断言大多针对行为与返回结构而非内部细节，命名清晰（`test_*` + 中文 docstring），失败信息可读；个别测试断言偏弱（如 `test_consolidate_clusters` 只断言 `cluster_count >= 1` 而未校验簇内容），但整体达到「守住核心契约」的水准。**166/166 通过且 0.85s，说明当前是健康基线**，主要风险在覆盖盲区而非回归。

---

## 6. 文档与路线图（PRD P1/P2）

### 6.1 PRD 文档状况
`docs/PRD.md`（v1.4，594 行）结构完整：定位/竞品/规格/架构/数据模型/接口/路线图/指标/风险。CONFIG.md（114 行）与 QUICKSTART.md（76 行）与实现一致度较高。

### 6.2 路线图标定（PRD 第 10 节）
**P0（已完成）**：文档导入、分块引擎、BM25 双列搜索、答前上下文、对话记忆（版本链）、MCP Server。
**P1（已完成）**：查询扩展（同义词+模糊）、查询改写（规则+LLM）、轻量关联、Profile、Dreaming、去重压缩、一键安装。
**P2**：
- 已完成：一句话摘要 + 双索引 RRF 融合、`shibei_context`、查询改写。
- 规格锁定待开发：**轻量向量混合**（10.3，onnxruntime/bge-small-zh 或 sentence-transformers，`chunk_embeddings` 表，三路 RRF，失败降级 BM25）。
- 待开发：Web 页面连接器、Notion/Obsidian 连接器、HTTP API server。
**v0.4.x 未完成项**：导入进度条与并行解析、更完善中文停用词表、记忆过期清理策略可配置化、补齐 CLI/MCP server 单元测试。

### 6.3 文档与实现的偏差（见 7.x）
- 10.1 写「MCP Server（8 个工具）」，6.1 与实测为 **9 个**（context/search/import/list_sources/dream/remember/update_memory/consolidate/apply_consolidation）；
- 10.2 把「补齐 CLI/MCP server 单元测试」标为未完成 `[ ]`，但 `tests/test_cli.py`（6 个）与 `tests/test_server.py`（7 个，9 工具参数化）已存在；
- PRD 9.1 列出 `shibei init` 命令，cli.py 无对应实现（grep 未命中）。

---

## 7. 代码质量、潜在 bug 与改进建议（按文件、行号）

### 7.1 consolidator/dreamer：`consolidate --apply` 会静默截断合并内容（**实锤，数据丢失**）
- 位置：`dreamer.py:133`（`"text": content[:500]`）→ `consolidator.py:93-98`（启发式摘要取 `c.get("text")`）→ `consolidator.py:195-203`（写入 summary 作为新 chunk content）。
- 实证：800 字符的原文 chunk 经 `consolidate(apply=True)` 后，库中合并 chunk 的 `content` 长度 = **500**，500 字符之后的内容永久丢失。
- 影响：MCP `shibei_consolidate`（apply=true）与 `shibei_apply_consolidation`（summary 为空走启发式）都会命中。
- 建议：`load_chunks` 应携带完整 `content`（或至少把 `text` 截断只用于关键词/相似度，摘要改用完整文本）；apply 前对截断做告警或拒绝。

### 7.2 dreamer：采样逻辑系统性偏差（**实锤**）
- 位置：`dreamer.py:115-120`。
- 实证 1：`total=600, max_chunks=500` → `step=1` → `rowid % 1 == 0` 恒真 → `LIMIT 500` 取**前 500 行**（d0..d499），并非「均匀采样」；
- 实证 2：`total=1000, max_chunks=500` → `step=2` → 只取**偶数 rowid**（500 条全偶数，奇数 0 条），系统性丢弃一半数据；若 rowid 有空洞（删除过），采样比例更不可控。
- 影响：dream 洞察与 consolidate 候选基于有偏样本，结论可能失真。
- 建议：改用 `ORDER BY rowid LIMIT max_chunks OFFSET 随机`，或按 `rowid >= random_start` 游标采样；并补采样测试。

### 7.3 bm25/memory：score 归一化方向反了且被压平（**实锤**）
- 位置：`bm25.py:199` 与 `memory.py:269`：`score = round(1.0 / (1.0 + abs(rank)) if rank else 1.0, 3)`。
- FTS5 的 `rank` 是 **bm25 负值**（越负越相关），`abs(rank)` 越大说明匹配越好，于是**最优匹配的 score 反而最小**。
- 实证（60 文档语料）：top1 rank=-2.598 → score 0.161；次优 rank=-2.595 → score 0.162——最优者分数更低；小语料（3 文档）时 rank≈-1.7e-6，所有结果 score 全部四舍五入成 **1.0**，score 字段完全失去区分度。
- 影响：任何依赖 `score` 排序/展示/阈值的地方（CLI 输出、MCP 返回、外部 Agent 判断）都会被误导（排序本身由 `ORDER BY rank`/RRF 保证正确，仅 score 数值失真）。
- 建议：改 `1.0 / (1.0 - rank)`（rank 为负时越大越好）或直接暴露 `-rank`，并补「分数单调性」断言测试。

### 7.4 dreamer：孤立文档判定 O(refs × chunks) 且按 basename 比较
- 位置：`dreamer.py:240-252`：`for ref in cross_refs: for c in chunks:` 双重循环，且 `Path(c["source"]).name == ref["doc_a"]` 只比文件名——两个不同目录下的同名文件会被误判为「已连接」。
- 建议：用 source 全路径建集合一次 O(n) 完成；交叉引用本身（L160-189）内部已是 O(n²)，500 chunk 上限内可接受，但应注明复杂度。

### 7.5 expander：`_lookup_synonyms` 死代码 / 注释与实现不符
- 位置：`expander.py:165-175`：docstring 与注释宣称「尝试查找包含该词的 key 的同义词（查询词是 key 子串时）」并引用 L173 的注释，但 L175 直接 `return []`，该分支永远不会返回任何东西。
- 影响：无害但误导维护者；若本意是「子串匹配」，应实现（遍历 key `in` word）；否则删掉死代码与注释。

### 7.6 related：「Jaccard」注释与公式不符 + LIKE 转义
- 位置：`related.py:172-173`：注释写「Jaccard」，公式实为 `sum(common)/max(sum(A),sum(B))`（min-overlap 风格，与 2.3.2 一致但命名误导）。
- 另：`related.py:79` `tags LIKE ?` 用 `f"%{tag}%"`——若 tag 本身含 `%`/`_` 会被当作通配符（应转义或改用 `instr()`）；`f"{source_dir}%"`（L97）同理，且 source_dir 含 SQL 特殊字符时是 f-string 拼接（好在路径值由程序构造，风险低）。

### 7.7 consolidator：`apply_consolidation` 与 `consolidate()` 输出的 `chunks` 字段类型不兼容
- 位置：`consolidator.py:252-256`（`apply_consolidation` 期望 `cluster["chunks"]` 为**字符串列表**）vs `consolidator.py:147-154`（`consolidate()` 输出 `chunks` 为 **dict 列表** `{_rowid, source, text}`）。
- 影响：Agent 若把 `shibei_consolidate` 的 clusters 原样回传给 `shibei_apply_consolidation` 且不带 summary，启发式摘要会把 dict repr 拼进正文。
- 建议：统一 schema 并在 MCP 工具描述中明确「chunks 为字符串列表」。

### 7.8 consolidator：删除/插入非事务性
- 位置：`consolidator.py:208-219 / 283-300`：`delete_by_rowids` 与 `add_batch` 是两个独立连接调用，中间崩溃会留下「旧 chunk 已删、新 chunk 未插」的中间态。
- 建议：在 BM25Index 增加一个 `replace_by_rowids(delete_ids, new_items)` 原子方法（同一连接同一事务）。

### 7.9 profile：`create_profile` 依赖 CWD 找 `config.example.yaml`
- 位置：`profile.py:44`：`example = Path("config.example.yaml")`——从其他目录运行 `shibei profile create` 时找不到模板，静默落入内联最小配置分支（功能可用但丢失完整模板）。
- 建议：用 `Path(__file__).resolve().parent.parent.parent / "config.example.yaml"` 或把模板打进包数据。

### 7.10 config：YAML 未知键会导致 `TypeError` 崩溃
- 位置：`config.py:158, 176, 187, 212, 216`：`XxxConfig(**raw["xxx"])` 直接展开；用户 YAML 多写一个键（如 `llm: {timeout: 30, api_key: xxx}`）即 `TypeError: unexpected keyword argument`，且无友好报错。
- 建议：用 `dataclasses.fields()` 过滤未知键，或捕获 TypeError 转配置错误提示。

### 7.11 llm.py：无重试/退避
- 位置：`llm.py:79-92`：单次 `urlopen`，`HTTPError/URLError` 直接抛出；无指数退避重试、无 429/5xx 感知。
- 建议：对 429/5xx 做 1-2 次退避重试（LLM 摘要/改写在导入/搜索链路中，重试收益明显）；超时与重试次数做成 LLMOptions 字段。

### 7.12 llm_rewriter：跨类访问私有成员 + assert 依赖
- 位置：`llm_rewriter.py:58`（`self._client._api_key()`）与 `:89`（`assert self._client is not None`，`python -O` 下 assert 被移除后类型收窄失效）。
- 建议：给 LLMClient 加公开的 `api_key_available()` / `is_configured()`；用显式 `if` 替代 assert。

### 7.13 rewriter：顺序敏感 + 级联替换
- 位置：`rewriter.py:53-56`：规则 `讲讲→介绍` 与 `介绍→讲解` 同时存在时，"讲讲" 最终变成 "讲解"；且替换后继续匹配后续规则（级联）。
- 影响：规则配置不当会链式改写，难以排查（`applied` 列表能反映但无顺序说明）。建议文档化「按声明顺序单遍替换」或改为一次扫描多规则同时匹配。

### 7.14 dreamer：无 jieba 时全大写英文词（AI/LLM/FTS5）被排除出关键词
- 位置：`dreamer.py:91`：`not w.isupper()` 过滤了所有全大写词，而技术文档中 AI/LLM/API 恰是最重要的关键词；jieba 分支无此过滤（行为不一致）。
- 建议：改为过滤「纯数字/网址」噪音，保留全大写缩写，或与 STOP_WORDS 中的噪音表对齐。

### 7.15 文档-实现漂移（PRD/CONFIG）
- PRD 10.1「MCP Server（8 个工具）」应为 9；10.2「补齐 CLI/MCP server 单元测试」实已完成；9.1 `shibei init` 无实现；6.2.5 与 10.2 中「摘要召回扩展 P2 已完成」与 6.1 表格状态基本一致，但 P1/P2 状态行与 10.x 勾选项有重复表述。
- CONFIG.md rewriter 段注明「默认由宿主 Agent LLM 完成改写」，与 searcher 实现一致（`use_llm` 仅 CLI 场景）。

### 7.16 其它小项
- `dreamer.py:116`：`step = total // max_chunks` 在 `total < 2*max_chunks` 时必为 1（见 7.2）；且 f-string 拼 SQL 虽参数均为 int 安全，但风格上建议参数化。
- `dreamer.py:46-51`：停用词表有重复项（「可」「以」「不」「是」「大」「家」等），可去重整理。
- `consolidator.py:194`：`new_source = f"__consolidated__/{timestamp}/{idx}#{Path(source_name).name}"` 中 `source_name` 未做路径安全处理（源文件名含 `/` 时 Path().name 已兜底，OK）。
- `related.py:91`：`LIMIT 5000` 与 `:97` `LIMIT 1000` 为硬编码，建议进配置。
- `bm25.py:201`：`text: content[:300]` 截断返回文本——搜索展示层面可接受，但若上游依赖完整 content 需留意。

---

## 8. 值得注意的问题清单（按优先级）

### 🔴 高优先级（可能造成数据丢失或结果错误）
1. **`consolidate --apply` / `apply_consolidation` 合并内容被截断到 500 字符**（dreamer.py:133 → consolidator.py:89-98/195-203），超过部分永久丢失；实测 800 字符原文 → 库中仅剩 500 字符。测试盲区（现有用例文本过短）未发现。
2. **BM25 score 归一化方向反了**（bm25.py:199、memory.py:269）：最优匹配得分反而最低；小语料下所有结果 score 塌缩为 1.0，score 字段失去意义。排序正确，但对外暴露的分数具有误导性。
3. **dreamer 采样有偏**（dreamer.py:115-120）：`total<2*max_chunks` 时取前 N 行（非均匀）；`step>=2` 时只取固定奇偶 rowid，系统性丢一半；影响 dream 洞察与 consolidate 候选的代表性。

### 🟡 中优先级（兼容性 / 鲁棒性）
4. **`apply_consolidation` 与 `consolidate` 输出的 `chunks` 字段类型不一致**（dict 列表 vs 字符串列表），回传复用会拼出 dict 文本（consolidator.py:252-256 vs 147-154）。
5. **删除/插入非事务**，中途失败留下半完成状态（consolidator.py:208-219/283-300）。
6. **`create_profile` 用 CWD 相对路径找 `config.example.yaml`**（profile.py:44），非项目根目录运行时模板静默丢失。
7. **config.py 未知 YAML 键直接 TypeError**（config.py:158 等 `**` 展开），配置错误无友好提示。
8. **LLM 客户端无重试/退避**（llm.py），429/5xx 或瞬时网络错误直接失败（有降级兜底，但召回质量受损）。
9. **llm_rewriter 访问私有成员 + 依赖 assert**（llm_rewriter.py:58/89），`-O` 模式与类型收窄隐患。
10. **tag LIKE 通配符未转义**（related.py:79/97），tag 含 `%`/`_` 时召回失真。

### 🟢 低优先级（整洁度 / 文档）
11. expander.py:165-175 死代码（注释声称子串匹配，实现恒返回 []）。
12. related.py:173 注释「Jaccard」与公式不符；related 硬编码 LIMIT 5000/1000。
13. dreamer.py:240-252 孤立文档判定 O(refs×chunks) 且按 basename 匹配（跨目录同名误判）。
14. dreamer.py:91 无 jieba 时全大写缩写（AI/LLM）被排除出关键词；与 jieba 分支行为不一致。
15. rewriter 规则级联替换、顺序敏感（rewriter.py:53-56），建议文档化或单遍匹配。
16. 文档漂移：PRD 工具数 8 vs 9、`shibei init` 无实现、「补齐 CLI/MCP 测试」已实际完成但仍标 `[ ]`。
17. 停用词表重复项（dreamer.py:46-51）；jieba 同时列入必装依赖与 extra（pyproject.toml:23/33）冗余。
18. test_llm_rewriter.py monkeypatch `__init__` 的实现方式脆弱（L66-75/102-109），建议改用依赖注入。

### ✅ 做得好的地方
- 零 LLM 依赖的搜索/整理主链路 + 全面降级设计，契约清晰（Shibei 只产候选、宿主 Agent 决策）；
- 测试隔离性优秀：全 tmp 目录、无网络、0.85s 全绿；
- 中文分词双路径（jieba/bigram）与 Markdown 噪音过滤、RRF 双列融合、版本链记忆等实现干净；
- 工程极简（urllib + sqlite 标准库 + 两个依赖），符合「轻量」定位。

---

*报告完 — 基于代码与实证（2026-08 分析，Python 3.12.13 / jieba 可用 / pytest 166 通过）。*
