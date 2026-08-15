# 拾贝（Shibei）Agent 接入层源码分析报告

> 分析对象：`src/shibei/server.py`、`context.py`、`services.py`、`cli.py`、`__init__.py`、`__main__.py`，
> 以及支撑调用链的 `searcher.py`、`memory.py`、`bm25.py`、`dreamer.py`、`consolidator.py`、`importer.py`、
> `config.py`、`tokenizer.py`、`llm.py`、`llm_rewriter.py`、`profile.py`、`related.py`；
> 契约文档 `agent.md`、`docs/MCP_INTEGRATION.md`；测试 `tests/test_server.py` 等。
>
> 所有行号以当前工作区代码为准，供复核。

---

## 目录

1. [架构总览](#1-架构总览)
2. [MCP server（server.py）](#2-mcp-server)
   - 2.1 运行模型与 JSON-RPC 分发
   - 2.2 九个工具逐个分析
3. [shibei_context 完整请求流](#3-shibei_context-完整请求流)
4. [CLI 全部子命令](#4-cli-全部子命令)
5. [服务层 Shibei 类](#5-服务层-shibei-类)
6. [错误处理、超时与安全](#6-错误处理超时与安全)
7. [多宿主接入契约](#7-多宿主接入契约)
8. [模块耦合度与代码质量](#8-模块耦合度与代码质量)
9. [值得注意的问题清单](#9-值得注意的问题清单)

---

## 1. 架构总览

拾贝的「Agent 接入层」由三层组成，三层之间通过 `Shibei` 服务类解耦：

```
┌─ Agent（Claude / Cursor / Trae / Hermes）
│    MCP JSON-RPC over stdio（python3 -m shibei.server）
▼
server.py（协议层）      ← 9 个 _handle_* 薄封装 + 手写 JSON-RPC 分发循环
▼
services.py（服务层）    ← Shibei 类，统一入口，按需惰性 import 各组件
▼
context.py / searcher.py / importer.py / memory.py /
dreamer.py / consolidator.py / bm25.py / tokenizer.py …（领域层）
```

- 协议层 `server.py`（457 行）不依赖任何 MCP SDK，纯手写 stdio + JSON-RPC 2.0 循环。
- 服务层 `services.py`（166 行）是唯一的对外门面，CLI 与 MCP 都经由它。
- 领域层模块全部通过**函数内 import** 被服务层引用，避免了模块级循环导入。
- 契约文档（`agent.md`、`docs/MCP_INTEGRATION.md`）定义了「多宿主强制召回」规则，但**服务端没有任何宿主识别代码**——多宿主是纯文档/提示词层面的契约（见第 7 节）。

---

## 2. MCP server

### 2.1 运行模型与 JSON-RPC 分发（server.py:367-453）

`main()`（367 行）是唯一入口：

1. 启动即 `_init_brain()`（32-34 行）：`Shibei(os.environ.get("SHIBEI_CONFIG", "config.yaml"))`，只加载配置，不打开数据库（DB 在首次工具调用时才由各组件惰性初始化）。
2. `for line in sys.stdin`（370 行）逐行读取，`json.loads` 解析（372 行），按 `method` 分发：
   - `initialize`（379-388 行）：返回 `protocolVersion: "2024-11-05"`、`capabilities: {tools: {}}`、`serverInfo {name: shibei, version: 0.1.0}`。**不协商客户端声明的协议版本**。
   - `notifications/initialized`（389-390 行）：静默 `continue`（正确——通知不应有响应）。
   - `tools/list`（391-404 行）：把模块级 `TOOLS` 表（110-364 行）展开为 `[{name, description, inputSchema}]`。
   - `tools/call`（405-444 行）：查表分发到 `TOOLS[name]["handler"](brain, arguments)`；成功则把返回值 `json.dumps(result, ensure_ascii=False, indent=2)` 包进 `result.content[0].text`（426-434 行）；异常则返回 `result.isError=True` + `"Error: {e}"` 文本（436-444 行）。
   - 其他方法（445-450 行）：返回 JSON-RPC 错误 `-32601 Method not found`。
3. 每行响应 `sys.stdout.write(...) + "\n"` 并 `flush()`（452-453 行）。

**协议层特点与缺口**（详见第 9 节问题清单）：

- JSON 解析失败（372-374 行）静默 `continue`，不返回 `-32700 Parse error`，等待响应的客户端会挂起。
- 顶层 JSON 若为数组（JSON-RPC 2.0 合法 batch）或非 dict，376 行 `req.get` 抛 `AttributeError`，**整个 server 崩溃**。
- 未实现 MCP 规范要求的 `ping`（会落入 else 分支返回 -32601）；任何带 `id: null` 的通知（如 `notifications/cancelled`）都会被回一个响应，违反 JSON-RPC 通知语义。
- 工具执行错误用 `result.isError`（436-444 行），未知工具用 `error` 字段（411-418 行），两种错误形态不一致。
- `--debug`（`cli.py:21` 设置 `SHIBEI_MCP_DEBUG=1`）是一个**只写不读**的环境变量，全项目无 `logging.basicConfig`，调试开关是死的（见问题清单 #4）。

### 2.2 九个工具逐个分析

统一说明：所有工具经 `tools/call` 调用，成功响应的结构为
`{"content": [{"type": "text", "text": "<result 的 JSON>"}]}`（server.py:422-435）。

#### ① shibei_context —— 答前上下文包（强制召回入口）

- **JSON-RPC 调用名**：`tools/call`，`name: "shibei_context"`（TOOLS 表 111-153 行）。
- **参数**（inputSchema，122-152 行）：`message`（必填）、`query`（可选，宿主 LLM 扩写后的检索词）、`limit`（默认 5）、`tag`、`write_hints`（默认 True）。
- **handler**：`_handle_context`（45-52 行）→ `brain.context(message, limit, tag, write_hints, query)`（services.py:35-55）→ `build_context`（context.py:110-176）。
- **返回结构**：`{message, query_used, total, results[], memories[], related[], rewrite?, llm_rewrite?, expansion?, suggest_remember?}`（context.py:158-174）。
- **内部调用链**：
  `build_context`（context.py:110）→ 查询词解析（140 行：`query or extract_query_terms(message) or message`）
  → `Searcher(cfg).search(query, limit, tag, include_memories=True)`（142-144 行；searcher.py:114-137）
  → `BM25Engine.search`（searcher.py:33-94）：规则改写（41-47）→ 可选 LLM 改写（49-62）→ `tokenize_query`（64）→ 可选同义词/模糊扩展（66-78）→ `BM25Index.search`（80-81；bm25.py:133-151）→ 正文/摘要双列检索（149-150）→ RRF 融合（151；bm25.py:208-243）
  → 并行记忆召回 `Searcher._search_memories`（130-131；139-156）→ `MemoryStore.search`（memory.py:218-229）
  → 可选 related（133-136；related.py）
  → 文本截断（context.py:147-156：text 240 字 / parent_text 400 字）
  → 组装 pack（158-165）→ `suggest_remember_from_message`（173-174；83-107）。
- **备注**：`suggest_remember` 只在 `write_hints=True` 时返回；服务端只给「建议」，从不自动写库（agent.md:44）。

#### ② shibei_search —— 语义搜索

- **调用名**：`shibei_search`（154-170 行）。
- **参数**：`query`（必填）、`limit`（默认 5）、`tag`。
- **handler**：`_handle_search`（37-42 行）→ `brain.search`（services.py:23-33）→ `Searcher.search` → `BM25Engine.search`（调用链同 ① 的检索部分，不含记忆的截断）。
- **返回结构**：`{query, query_used, total, results[], rewrite?, llm_rewrite?, expansion?, memories[], related[]}`（searcher.py:82-94 + 130-137）。
- **备注**：与 context 的区别是**不做抽词、不返回 suggest_remember**；工具描述明确「日常答前召回请用 shibei_context」（158-160 行）。

#### ③ shibei_import —— 导入文档

- **调用名**：`shibei_import`（171-193 行）。
- **参数**：`dirs`（可选目录列表，默认 config.sources）、`full`（默认 False 增量）、`file_pattern`（glob 过滤，可选）。
- **handler**：`_handle_import`（55-59 行）→ `brain.import_all`（services.py:57-63）→ `Importer.run`（importer.py:38-209）。
- **返回结构**：`{files, chunks, skipped, removed, deduped, status, warnings?}`（importer.py:199-209）。
- **内部调用链**：目录扫描 `rglob` + ignore_dirs 过滤（59-71）→ `Manifest.classify` 增量判定（81）→ `ThreadPoolExecutor` 并行 `parse_file`（93-107）→ `Tagger.infer` 打标签（121）→ `chunk_text` 分块（123-131，含 parent-child 分支 132-149）→ `ChunkSummarizer.summarize`（154-158，LLM 失败降级空串）→ `ChunkHashStore` + `dedup_chunks` 精确去重（161-183）→ `_import_bm25`（186-187；211-223：先 `remove_sources` 旧文件、再 `add_batch`）→ manifest / hash_store 持久化（189-197）。
- **备注**：`dirs` 传 None 时 `dirs or self.cfg.sources`（54 行）；不存在的目录只记 warning 不报错（70-71 行）。

#### ④ shibei_list_sources —— 列出已入库文件

- **调用名**：`shibei_list_sources`（194-212 行）。
- **参数**：`limit`（默认 0=不分页返回全部）、`offset`。
- **handler**：`_handle_list_sources`（62-66 行）→ `brain.list_sources`（services.py:65-67）→ `importer.list_sources`（importer.py:275-324）。
- **返回结构**：`{count, sources[], summary: {total_chunks, total_files, by_tag}, has_more}`（importer.py:315-324）。
- **内部调用链**：`BM25Index.list_sources_with_meta`（295 行；bm25.py:258-268）→ `count()`（298 行）→ 内存聚合 by_tag（300-305）→ 内存分页 `all_meta[offset:] / [:limit]`（307-310）。
- **备注**：分页是**取全量后在内存切片**，非 SQL 分页；库大时 list_sources_with_meta 的 GROUP_CONCAT 有开销（见问题清单 #27）。

#### ⑤ shibei_dream —— 知识库洞察 + 整理候选（零 LLM）

- **调用名**：`shibei_dream`（213-237 行）。
- **参数**：`housekeeping`（`false`=禁用 / `true`=默认配置 / `object`=自定义，默认 true）、`format`（brief/full，默认 brief）、`persist`（默认 true）。
- **handler**：`_handle_dream`（69-74 行）→ `brain.dream`（services.py:77-89）→ `dreamer.dream`（dreamer.py:337-395）。
- **返回结构**：`{status, timestamp, format, stats: {total_chunks, unique_docs, cross_references, topics_with_connections}, insights[], housekeeping: {enabled, memory: {...}, consolidation: {...}}, cross_references?, patterns?}`（dreamer.py:372-390；`full` 格式才带后两个）。
- **内部调用链**：`load_chunks`（358 行；106-136，采样 + 关键词抽取）→ `find_cross_references`（363；160-189）→ `find_patterns`（364；192-209）→ `generate_insights`（365；212-254）→ `_run_housekeeping`（367；257-320：`MemoryStore.cleanup_expired` + `find_compressible` 记忆整理 281-298；`consolidator.consolidate` 相似 chunk 候选 301-318）→ `_persist_dream_result` 写 `db_path/dream_result.json`（392-393；323-334）。
- **备注**：`housekeeping=True` 时 `_run_housekeeping` 的 `isinstance(True, dict)` 为假，走默认 `DEFAULT_HOUSEKEEPING`（31-38 行）——语义正确。`brief` 格式**不裁剪** housekeeping 里的 clusters 详情，响应可能很大（见问题清单 #30）。

#### ⑥ shibei_remember —— 写入对话记忆

- **调用名**：`shibei_remember`（301-341 行）。
- **参数**：`fact`（必填）、`category`（user/agent/project/preference，默认 user）、`source`（可选会话 ID）、`tags`（逗号分隔）、`expires_at`（ISO 8601 可选）。
- **handler**：`_handle_remember`（92-99 行）→ `brain.remember`（services.py:97-113）→ `MemoryStore.add`（memory.py:121-185）。
- **返回结构**：`get(memory_id)` 整行：`{id, fact, category, source, tags, created_at, expires_at, supersedes, superseded_by, rowid}`（memory.py:185 + 284-292）。
- **内部调用链**：空 fact 抛 `ValueError`（132-134）→ source 缺省 `"agent"` 并入 tags（140-143）→ `_find_most_similar`（146-152；187-204：BM25 召回 + Jaccard 重叠度，score ≥ 0.7 建立 supersedes 版本链）→ INSERT + FTS 同步写（154-183）→ 若 supersedes 则回写旧记忆 `superseded_by`（178-182）。
- **备注**：`tags` 会被强制并入 `source` 并排序（141-143），用户传的标签顺序不保证。

#### ⑦ shibei_update_memory —— 更新/压缩已有记忆

- **调用名**：`shibei_update_memory`（342-363 行）。
- **参数**：`memory_id`（必填）、`fact`（必填）、`tags`（可选，不传保持原标签）。
- **handler**：`_handle_update_memory`（102-107 行）→ `brain.update_memory`（services.py:137-144）→ `MemoryStore.update`（memory.py:421-450）。
- **返回结构**：纯 bool（`True` 成功 / `False` 未找到），`json.dumps` 后是 `true`/`false`。
- **内部调用链**：`_rowid_by_id`（423；206-211）→ `get` 取旧行（427）→ 事务内：删 FTS 旧索引（436）→ UPDATE 主表（437-444）→ 重建 FTS（445-448）。
- **备注**：记忆 ID 支持**前 8 位前缀匹配**（CLI 提示，cli.py:579），但 `update` 本身是精确匹配（423 行），前缀匹配只存在于 `delete`（见 2.2 及问题清单 #13）。

#### ⑧ shibei_consolidate —— 相似 chunk 聚类（可选自动合并）

- **调用名**：`shibei_consolidate`（238-266 行）。
- **参数**：`threshold`（默认 0.6）、`min_cluster_size`（默认 2）、`max_chunks`（默认 500）、`apply`（默认 False）。
- **handler**：`_handle_consolidate`（77-83 行）→ `brain.consolidate`（services.py:146-161）→ `consolidator.consolidate`（consolidator.py:101-171）。
- **返回结构**：`{status, total_chunks, cluster_count, threshold, clusters: [{size, sources, summary, chunks: [{_rowid, source, text}]}], applied?}`（160-169 行；`applied` 仅 apply=True 时存在）。
- **内部调用链**：`load_chunks`（126；dreamer.py:106-136）→ `_build_similarity_pairs` 两两 Overlap（130；41-56）→ `_cluster_by_edges` 传递闭包（131；59-86）→ 每组 `_heuristic_summary`（141；89-98）→ apply=True 时 `_apply_clusters`（168-169；174-219：`delete_by_rowids` + `add_batch`）。
- **备注**：O(n²) 两两比较（见问题清单 #17）；传递闭包会把「链式相似」合并成一个簇（#18）；`summary` 是启发式全文拼接，候选响应体积大（#30）。

#### ⑨ shibei_apply_consolidation —— 按外部摘要直接合并

- **调用名**：`shibei_apply_consolidation`（267-300 行）。
- **参数**：`clusters`（必填，元素 `{rowids: [int], summary: str, sources?: [str]}`，`rowids` 来自 dream/consolidate 返回的 `cluster.chunks._rowid`）。
- **handler**：`_handle_apply_consolidation`（86-89 行）→ `brain.apply_consolidation`（services.py:163-166）→ `consolidator.apply_consolidation`（consolidator.py:222-301）。
- **返回结构**：`{applied, removed, inserted, invalid_clusters, error?}`（275-292 行）。
- **内部调用链**：逐 cluster 校验 rowids 为 int（246-249）→ summary 为空时用 `cluster.chunks` 启发式拼接（252-256）→ 汇总待删 rowids 与待插摘要（262-273）→ `BM25Index.delete_by_rowids` + `add_batch`（283-292；bm25.py:294-310, 103-131）。
- **备注**：这是**破坏性写入**且完全信任外部 Agent 的 rowids/summary——不校验 rowid 归属、不限制 summary 长度；delete 与 insert 不在同一事务（见问题清单 #19）。

---

## 3. shibei_context 完整请求流

以 MCP `tools/call {name: "shibei_context", arguments: {message, query?, limit?, tag?, write_hints?}}` 为例：

```
收到 message（本轮用户消息原文）
  │
  ▼ server.py:45-52  _handle_context
  │   brain.context(message, limit, tag, write_hints, query)
  ▼ services.py:35-55  Shibei.context
  │   build_context(cfg, message, limit, tag, write_hints, query)
  ▼ context.py:110  build_context
  │
  ├─ 138 行  message = (message or "").strip()
  ├─ 140 行  检索词解析：query(Agent 扩写词) 优先
  │          → 否则 extract_query_terms(message)（服务端规则抽词）
  │          → 否则退回 message 原文
  │
  ├─ 142-144 行  Searcher(cfg).search(query, limit, tag, include_memories=True)
  │   │
  │   ├─ searcher.py:121-128  取 engine（config.storage.search_engine，未知则回退 bm25）
  │   ├─ searcher.py:33-94    BM25Engine.search
  │   │   ├─ 41-47   规则改写 rewriter（cfg.rewriter.enabled）
  │   │   ├─ 49-62   可选 LLM 改写 llm_rewriter（cfg.rewriter.use_llm，需 SHIBEI_LLM_API_KEY）
  │   │   ├─ 64      tokenize_query(effective_query)
  │   │   ├─ 66-78   可选同义词/模糊扩展 expander
  │   │   └─ 80-81   BM25Index.search
  │   │       ├─ bm25.py:148      tokenized = 传入 tokenized_query
  │   │       ├─ bm25.py:149-150  正文列 + 摘要列各查 limit*3
  │   │       ├─ bm25.py:161      MATCH '{field}: {tokenized}'（tag 存在时追加 c.tags LIKE）
  │   │       └─ bm25.py:151+208  RRF 融合，score = 1/(1+|rank|)
  │   ├─ searcher.py:130-131  记忆召回（include_memories=True）
  │   │   └─ searcher.py:139-156  MemoryStore.search（memory.py:218-229，FTS MATCH + 排除 superseded）
  │   └─ searcher.py:133-136  related（仅 cfg.related.enabled；默认关）
  │
  ├─ 147-156 行  截断：results[i].text ≤ 240 字、parent_text ≤ 400 字
  ├─ 158-165 行  组装 pack：{message, query_used, total, results, memories, related}
  ├─ 166-171 行  透传 rewrite / llm_rewrite / expansion（若存在）
  └─ 173-174 行  write_hints=True → pack["suggest_remember"] = suggest_remember_from_message(message)
                 │
                 ▼ context.py:83-107  suggest_remember_from_message
                 ├─ 89-97   _FIRST_PERSON_RE（"我|本人"…40 字内出现 喜欢/习惯/计划/决定/负责/做过/开发过/偏好/目标是）
                 │         → should=True, fact=原句（>200 字截断），reason="first_person_disclosure"
                 ├─ 100-105 "我"开头且 4≤len≤120 且无问号 → should=True, reason="short_first_person"
                 └─ 否则 should=False
  │
  ▼ server.py:422-435  结果 json.dumps 包进 content[0].text 返回
```

要点：

- **抽词降级链**（context.py:36-80）：去语气短语（42-43）→ jieba 精确分词过滤停用词（46-64，英文 token 用 `_ALNUM_RE` 补充）→ 异常或未装 jieba 时正则 `[\u4e00-\u9fff]{2,}` + 英文/下划线 token（68-80），最多 12 个词。
- **Agent 侧扩写优先**：`query` 参数 > 服务端规则抽词 > 原文（140 行），这是「用宿主 LLM 改写、不依赖服务端 LLM」契约的落点（agent.md:23-31）。
- **记忆与文档并行召回**：同一 `Searcher.search` 内文档走 BM25Engine、记忆走 MemoryStore，结果合并进一个 pack。
- **写侧提示不自动落库**：suggest_remember 只是提示，写库动作由 Agent 调用 `shibei_remember` 完成（agent.md:33-44）。

---

## 4. CLI 全部子命令

入口 `cli.main()`（495-705 行），全局参数 `--config` / `--profile`（500-506 行），统一先 `resolve_config_path`（652-657 行；profile.py:65-76，优先级 `--config > --profile > config.yaml`）。

| 子命令 | 用法（cli.py 行号） | 实现要点 |
|---|---|---|
| `init` | 438-492 / 参数 511-516 | 检查 Python ≥3.9 → `pip install -e .[extras]`（subprocess，455-463）→ 复制 `config.example.yaml`（473-479）→ 建 `~/.shibei/db`（482-484）。**数据目录硬编码**，与配置无关 |
| `serve` | 17-24 / 519-520 | 把 `resolved_config` 通过环境变量 `SHIBEI_CONFIG` 传给 `server.main()`（23 行）；`--debug` 只设置从未被读取的 `SHIBEI_MCP_DEBUG` |
| `context` | 27-54 / 523-530 | 打印 query_used/hits/结果/记忆/suggest_remember；`--query` 透传给服务端 |
| `search` | 57-112 / 533-547 | **直接改 `brain.config`** 临时启用 rewriter/expander（61-75 行）；打印改写/扩展详情与结果 |
| `import` | 199-238 / 550-554 | 位置参数或 `--dirs`（逗号分隔）二选一指定目录；tqdm 可选进度条（211-229）；汇总 files/chunks/skipped/removed/deduped |
| `list` | 241-246 / 557 | `brain.list_sources()` 打印 count + 每行一个 source |
| `clean` | 249-268 / 560-565 | `clean_orphans`（importer.py:226-272）：扫描现存文件，对比 manifest，删除 DB 中已消失的 source；`--dry-run` 只报告 |
| `remember` | 115-128 / 568-575 | 写记忆，打印 id 前 8 位 + 替代的旧记忆（`result['supersedes']`） |
| `update-memory` | 131-142 / 578-581 | 更新记忆；返回 False 时打印「未找到」 |
| `memories list` | 145-161 / 587-591 | 列出（默认 20 条），标注 `[已替代]`/`[已压缩]` |
| `memories search` | 164-180 / 593-598 | 记忆搜索 |
| `memories cleanup` | 183-187 / 600 | `cleanup_expired` 删除过期记忆 |
| `memories forget` | 190-196 / 602-603 | 删除记忆（支持前 8 位前缀匹配） |
| `consolidate` | 328-372 / 606-616 | 聚类候选；`--apply` 直接合并；`--output` 写 JSON 报告 |
| `apply-consolidation` | 375-396 / 619-621 | 读 JSON 文件（`clusters` 列表或完整 consolidate 输出，382 行兼容两种）→ 应用合并 |
| `dream` | 271-325 / 624-632 | 洞察 + 整理候选；`--no-housekeeping` 禁用整理；`--format full` 打印跨文档连接与标签模式；`--persist`/`--no-persist` 控制持久化 |
| `profile list/create/delete/show` | 399-435 / 635-648 | profile 增删查，基于 `~/.shibei/profiles/<name>.yaml` |

分发逻辑（659-705 行）：`init` 不需要 resolved_config（664-666 行）；`profile`、`memories` 用 dict 查表分发（669-687 行）；其余核心命令在 694-705 行的 dict 查表。

**CLI 侧问题**：

- `dream` 的 `--persist` 参数（629-630 行）`action="store_true", default=True` 是死参数（永远 True），实际生效的是 `--no-persist`（631-632 行）。
- `cmd_search`（61-75 行）直接改共享 config 对象——CLI 单进程单次运行无碍，但若未来做成交互式会话会污染后续调用。
- `cmd_init`（482 行）数据目录硬编码 `~/.shibei/db`，与 `config.yaml` 里 `storage.path` 可能不一致。
- `cmd_apply_consolidation`（380 行）读文件后 `sys.exit(1)` 处理格式错误，函数内退出而非异常，可测性差。

---

## 5. 服务层 Shibei 类

`services.py` 的 `Shibei`（11 行）是唯一门面：

- **构造**（20-21 行）：`self.config = load_config(config_path)`，不做任何 DB/组件初始化。
- **组件组装方式：全部函数内惰性 import**，按需实例化：
  - `search`（23-33 行）→ 每次 `Searcher(self.config)`。
  - `context`（35-55 行）→ 每次 `build_context(self.config, ...)`（context.py 内部再建 Searcher）。
  - `import_all`（57-63 行）→ 每次 `Importer(self.config)`。
  - `list_sources` / `clean_orphans`（65-67 / 69-75 行）→ importer 模块级函数。
  - `dream`（77-89 行）→ `dreamer.dream(self.config.db_path_expanded, ...)`。
  - `consolidate` / `apply_consolidation`（146-161 / 163-166 行）→ `consolidator.consolidate/apply_consolidation(db_path, ...)`。
  - 记忆系列统一走 `_memory_store()`（93-95 行）：**每次调用新建 `MemoryStore(db_path)`**（内部 `mkdir` + `_init_db` 的 CREATE TABLE IF NOT EXISTS，memory.py:78-87）——`remember/search_memories/list_memories/forget_memory/count_memories/cleanup_memories/update_memory` 全部如此。
- **返回值契约**：search/context/import_all/list_sources/dream/consolidate/apply_consolidation/remember 返回 dict；`update_memory`/`forget_memory` 返回 bool；`search_memories/list_memories` 返回 list；`count_memories` 返回 int；`cleanup_memories` 返回删除条数。
- **类型注解**：混用字符串注解 `'str | None'`（27、39、41 行等，3.9 兼容）与真实注解 `list[dict]`（116、121 行），风格不统一（见问题清单 #28）。

**服务层评价**：门面干净、依赖方向正确（只依赖 config，向领域层单向），但「每次调用重建组件 + 重复 DDL」的惰性策略在 MCP 高频调用下有明显开销；且没有实例缓存/连接复用。

---

## 6. 错误处理、超时与安全

### 6.1 错误处理

| 层 | 机制 | 位置 |
|---|---|---|
| MCP 工具异常 | `except Exception` 捕获 → `result.isError=True` + `Error: {e}` 文本 | server.py:420-444 |
| MCP 未知方法/工具 | JSON-RPC `-32601` | server.py:410-418, 445-450 |
| JSON 解析失败 | **静默 continue（无响应）** | server.py:372-374 |
| 顶层非 dict JSON | **未捕获，崩溃整个 server** | server.py:376 |
| 领域层 | 广泛 try/except + `logger.warning` 降级（如 dream 的 housekeeping，dreamer.py:296-298, 316-318；LLM 改写失败降级原查询，llm_rewriter.py:102-105；LLM 摘要失败降级空串，importer.py:154-158） | — |
| CLI | 顶层无全局 try/except，靠 argparse 与 `sys.exit(1)`（如 resolve_config_path 失败，cli.py:653-657） | — |

### 6.2 超时

- SQLite：`sqlite3.connect(..., timeout=30.0)` 贯穿 memory.py（90、154、207、259…）与 bm25.py（33、98、111、162…）。
- LLM HTTP：`LLMConfig.timeout` 默认 30s（config.py:92），`urllib.request.urlopen(timeout=...)`（llm.py:81）。
- **MCP 循环本身无任何超时/并发控制**：`dream`/`consolidate` 这类 O(n²) 重操作在 stdio 同步循环中执行，期间阻塞所有后续工具调用，也不发 progress 通知——宿主侧通常有自己的工具超时，可能出现「宿主以为失败、服务端还在跑」的错位（见问题清单 #31）。

### 6.3 安全

- **API key**：设计正确——`LLMConfig` 无 api_key 字段（config.py:82-98 注释明确「统一使用环境变量 SHIBEI_LLM_API_KEY」）；`llm.LLMClient._api_key()`（llm.py:36-41）只从环境变量取；`llm_rewriter` 只在环境变量存在时构建 client（llm_rewriter.py:42-51）。`available()` 通过 `self._client._api_key()` 访问私有方法（58 行），耦合较丑但无害。
- **SQL 注入**：所有 SQL 值均参数化（`?` 占位，如 memory.py:155-167、bm25.py:119-130），表名/列名是常量。主要残留面：
  - FTS5 MATCH 表达式由 `tokenize_query` 拼接（tokenizer.py:80-110）：中文 token 加双引号，**英文 token 不加引号**（104-106 行），且 token 为空时**回退返回原始文本**（107、168 行）→ 用户查询中的 `OR/AND/NEAR/NOT`、引号等 FTS 语法可进入 MATCH 表达式，造成语法错误或语义被篡改（非数据泄露，但属于注入面，见问题清单 #7）。
  - `memory_id` 前缀匹配用 `LIKE ?`（memory.py:325-327、343-345），id 含 `%`/`_` 时是通配符，可能误删多条。
  - tag 过滤 `c.tags LIKE ?`（bm25.py:173）`%{tag}%` 子串匹配：`ai` 会命中 `daily`；tag 含 `%`/`_` 时同理。
- **路径处理**：统一 `Path(...).expanduser()`（config.py:133-134、importer.py:60、247）；`rglob` 扫描受 `ignore_dirs` 过滤（importer.py:26-27、56）。未处理符号链接环/越权（本机工具场景可接受）。
- **凭证与隐私**：MCP 错误回传 `Error: {e}`（server.py:441）可能包含绝对路径/内部信息；工具输出会经宿主上下文传递，无脱敏。
- **破坏性工具无确认**：`shibei_consolidate(apply=true)` 与 `shibei_apply_consolidation` 直接删行写库（consolidator.py:174-219、283-292），无事务回滚、无二次确认；完全信任外部 Agent 的 rowids/summary。
- **`expires_at`** 不解析不校验（memory.py:131-183 原样存储），清理用字符串比较 `expires_at <= now_str`（memory.py:371-373）——naive 时间/时区不一致会错判（问题清单 #14）。

---

## 7. 多宿主接入契约

**实现方式：纯文档 + 提示词契约，服务端零宿主代码。**

1. **协议层**（server.py 顶部注释 14-21 行）：`command: python3 / args: [-m, shibei.server]`，任何 MCP 兼容宿主用同一 stdio JSON-RPC 接入；server 不探测、不区分宿主。
2. **契约正文**：`agent.md` 第 5-65 行 + `docs/MCP_INTEGRATION.md` 第 5-18 行，给出「多宿主强制召回契约」粘贴模板（Cursor Rule / Hermes system / Trae skill / Claude 项目说明通用）：
   - 非纯指令消息 → 宿主自己 LLM 抽 5-8 个关键词 → 先调 `shibei_context(message, query=关键词)` 再回答；
   - 不依赖服务端 LLM / `SHIBEI_LLM_API_KEY`；
   - `suggest_remember.should=true` 或新事实 → 调 `shibei_remember`；
   - 明确要求「搜一下」才用 `shibei_search`。
3. **契约的代码落点**：
   - `shibei_context` 的 `query` 参数优先于服务端抽词（context.py:140）——让宿主 LLM 的扩写真正生效；
   - 工具 description 内嵌使用规则（server.py:113-121、303-311），宿主在 tools/list 阶段即可读到；
   - `suggest_remember` 只建议不落库（context.py:173-174），写库权交给宿主（agent.md:44）。
4. **宿主配置示例**：Hermes `~/.hermes/config.yaml`（MCP_INTEGRATION.md:40-56）、Claude Desktop `claude_desktop_config.json`（58-74）、Cursor `.cursor/mcp.json`（76-92），均通过 `env.SHIBEI_CONFIG` 指定配置。

**评价**：契约设计（服务端零 LLM、宿主出词、宿主决策写库）清晰且自洽，文档与工具描述双层承载。但「强制召回」本质上是**提示词层面的软约束**——server 无法验证宿主是否先调 context 再回答；`shibei_search` 与 `shibei_context` 的能力重叠也依赖宿主自觉分流。另外 `agent.md` 声称「9 个工具」（76 行）与代码一致（tests/test_server.py:183 断言 9 个）。

---

## 8. 模块耦合度与代码质量

### 8.1 依赖方向

```
server.py ──► services.py ──► config.py
                    │
                    ├─► context.py ──► searcher.py ──► bm25.py / tokenizer.py
                    │                    │              └─(lazy) memory.py / related.py
                    ├─► importer.py ──► tagger / manifest / parsers / dedup / chunker / summarizer
                    ├─► memory.py ──► tokenizer.py
                    ├─► dreamer.py ──(lazy)► memory.py / consolidator.py
                    ├─► consolidator.py ──► dreamer.py(load_chunks) / bm25.py
                    └─► (llm.py / llm_rewriter.py / expander.py / rewriter.py)
```

- 服务层与 CLI/MCP 之间解耦良好；领域层内部无循环导入（dreamer→consolidator 为函数内 import，consolidator→dreamer 为模块顶部 import，方向相反但运行期安全）。
- **主要异味**：`consolidator.py:23` 顶部 `from .dreamer import load_chunks` —— 聚类模块依赖「做梦」模块的加载函数，职责错位；`dreamer` 与 `consolidator` 在 housekeeping 里互相引用（dreamer.py:301-309 函数内 import consolidate），把 `load_chunks`/`extract_keywords` 抽到公共模块（如 `loader.py`）更合理。
- 重复的「新建连接 + DDL」模式：`MemoryStore.__init__`（memory.py:78-87）、`BM25Index.__init__`（bm25.py:28-80）每次实例化都跑 CREATE TABLE IF NOT EXISTS / PRAGMA / 潜在 ALTER，且这两个类在每次搜索/记忆操作中被反复实例化（searcher.py:127、142；services.py:93-95）。

### 8.2 代码质量观察

- **优点**：手写 MCP 协议层短小清晰；9 个 handler 是纯薄封装；参数默认值在 handler 兜底（如 `args.get("limit", 5)`）；领域层异常降级策略（jieba/tqdm/LLM/解析器全部优雅降级）贯彻「核心路径零 LLM、零可选依赖」；测试覆盖协议契约（tests/test_server.py 9 工具分发、isError、-32601 全覆盖）。
- **可维护性问题**：
  - `TOOLS` 表（server.py:110-364）同时承担描述、inputSchema、handler，且 inputSchema 的 `default` 与 handler 内 `args.get(..., default)` **两处维护同一默认值**（如 `limit` 在 138 行与 48 行），容易漂移。
  - 无参数 schema 运行时校验：`limit` 传字符串、`housekeeping` 传任意类型等都不会被拒绝，错误直到领域层才暴露。
  - `cli.py` 的 `main()` 分发用三个 dict 查表 + 若干 if（659-705 行），可读性尚可但子命令回调签名全靠约定。
  - 字符串类型注解 `'str | None'` 与真实注解混用（services.py:27-41、137-144），若升级到 3.10+ 可统一为 `X | None` + `from __future__ import annotations`。
  - 文档漂移：MCP_INTEGRATION.md:26 的 `shibei_import` 只列 `dirs/full`，代码另有 `file_pattern`（server.py:187-190）；`shibei_remember` 文档（29 行）缺 `source`/`expires_at`（代码 326-337 行有）。

---

## 9. 值得注意的问题清单

按严重度排序（P1=可能崩溃/协议违规/数据损坏，P2=正确性/健壮性缺陷，P3=性能/风格/文档）。

| # | 严重度 | 位置 | 问题 | 建议 |
|---|---|---|---|---|
| 1 | P1 | server.py:372-374 | JSON 解析失败静默 `continue`，不返回 `-32700`，客户端将挂起等待 | 捕获后回 `{"jsonrpc":"2.0","id":null,"error":{"code":-32700,...}}` |
| 2 | P1 | server.py:376 | 顶层 JSON 为数组（JSON-RPC batch）或非 dict 时 `req.get` 抛 AttributeError，**整个 MCP server 崩溃** | 校验 `isinstance(req, dict)`，batch 至少回错误；为整个循环加兜底 try |
| 3 | P1 | server.py:445-450 | 未实现 MCP 规范要求的 `ping`（返回 -32601）；所有带 `id:null` 的通知都会被回响应，违反 JSON-RPC 通知语义 | 增加 `ping` 分支返回 `{"result":{}}`；判断 `id is None` 时跳过响应 |
| 4 | P1 | cli.py:21；全项目 | `SHIBEI_MCP_DEBUG` 只写不读、无 `logging.basicConfig`；`shibei serve --debug`（文档 MCP_INTEGRATION.md:96-104）**完全无效** | 在 server.main 入口按 env 配置 logging 级别，或移除该开关 |
| 5 | P1 | consolidator.py:174-219、222-301 | `apply=true` / `apply_consolidation` 先 `delete_by_rowids` 再 `add_batch`，**不在同一事务**；中途异常则 DB 处于半删状态，无法回滚 | 用单连接事务包裹 delete+insert（bm25 需提供事务化批量接口） |
| 6 | P1 | consolidator.py:222-301 | `apply_consolidation` 完全信任外部 Agent 的 `rowids`：不校验归属/存在性/是否重复删除；破坏性写入无确认 | 校验 rowid 存在且来源一致、限制 summary 长度、返回预览 |
| 7 | P2 | tokenizer.py:80-110 | `tokenize_query` 英文 token 不加引号（104-106、145-167 行），token 为空时回退返回**原始文本**（107、168 行）→ FTS5 表达式可被 `OR/AND/NEAR/引号` 等污染，产生语法错误或错误匹配 | 所有 token 统一 `"..."` 包裹；空结果返回空串而非原文 |
| 8 | P2 | bm25.py:148-161；tokenizer.py:86-87 | 空/纯空白查询 → tokenized="" → `MATCH 'content_tokens: '` → OperationalError；`shibei_search("")` 报错而非返回空结果；与 `MemoryStore.search` 的空查询保护（memory.py:226-229）不一致 | `BM25Engine.search`/`Searcher.search` 增加空查询短路 |
| 9 | P2 | bm25.py:173 | tag 过滤 `c.tags LIKE '%tag%'` 子串匹配：`ai` 命中 `daily`；tag 含 `%`/`_` 变通配符 | 精确匹配（拆分 tags 列比对或用 FTS 的 tags 列） |
| 10 | P2 | server.py:436-444 vs 411-418 | 工具异常走 `result.isError`，未知工具走 `error` 字段，两种错误形态不一致；异常文本 `Error: {e}` 未脱敏（可能含路径） | 统一错误形态；截断/脱敏异常消息 |
| 11 | P2 | memory.py:325-327、343-345 | 删除/清理用 `LIKE '{id}%'`，memory_id 含 `%`/`_` 时通配符误匹配多条 | 精确匹配 + 显式前缀参数（`id_prefix`） |
| 12 | P2 | memory.py:360-383、131-137 | `expires_at` 不校验/不解析，清理用 ISO 字符串比较；naive 时间或不同时区会错判过期 | 入库时 `datetime.fromisoformat` 校验并统一为 UTC |
| 13 | P2 | services.py:93-95、memory.py:78-87 | `_memory_store()` 每次调用新建 MemoryStore（mkdir + CREATE TABLE IF NOT EXISTS + commit），搜索/列记忆高频重复 DDL | 服务层缓存 store 实例；`_init_db` 加模块级标志 |
| 14 | P2 | bm25.py:28-80、33-166 | `BM25Index` 每次实例化跑 PRAGMA/ALTER 检查；每次 search 开 2 个连接（正文+摘要列），记忆再加 1 个——连接/DDL 抖动 | 单连接复用或连接池；schema 检查只做一次 |
| 15 | P2 | context.py:147-156 | 只截断 `results` 的 text/parent_text，`memories` 不截断；大记忆可撑爆 context pack | 对 memories 同样截断 |
| 16 | P2 | cli.py:629-630 | `dream --persist` 参数 `action="store_true", default=True` 是死参数（永远 True） | 删除该参数或改为 `store_true, default=False` 配 `--no-persist` |
| 17 | P2 | consolidator.py:41-56 | 相似度两两比较 O(n²)（500 chunks ≈ 12.5 万对），`max_chunks` 调大即卡死 | 引入 minhash/倒排预筛或限制 n |
| 18 | P2 | consolidator.py:59-86 | 传递闭包聚类把链式相似（a~b、b~c 但 a≁c）合并为一簇，阈值语义失真 | 改连通分量 + 簇内最小密度校验 |
| 19 | P2 | dreamer.py:113-120 | 采样 `rowid % step = 0` 是确定性有偏采样（按导入顺序），非均匀随机；dream/consolidate 结论有偏差 | 用 `ORDER BY random() LIMIT` 或先 shuffle |
| 20 | P2 | dreamer.py:133 | `load_chunks` 截断 content 到 500 字符，关键词抽取与聚类基于截断文本 | 提高截断或支持全量模式 |
| 21 | P2 | config.py:151-153 | 配置文件不存在时静默使用全默认值；`shibei --config /nonexistent search` 不报错 | 显式路径不存在时给出 warning/error |
| 22 | P2 | server.py:379-388 | `initialize` 不协商协议版本（恒返 2024-11-05），忽略客户端声明 | 按客户端 protocolVersion 协商（取交集） |
| 23 | P3 | consolidator.py:23 | 模块顶部 `from .dreamer import load_chunks`：聚类依赖「做梦」模块的加载函数，职责错位（dreamer 又函数内 import consolidate） | 抽公共 `loader.py` |
| 24 | P3 | cli.py:61-75 | `cmd_search` 直接改 `brain.config` 副作用式覆盖 | 改为传参/临时对象 |
| 25 | P3 | cli.py:482 | `cmd_init` 数据目录硬编码 `~/.shibei/db`，与配置 `storage.path` 可能不一致 | 从模板配置读取或提示用户 |
| 26 | P3 | services.py:27-41、137-144 | 字符串注解 `'str | None'` 与真实注解 `list[dict]` 混用 | 统一 `from __future__ import annotations` |
| 27 | P3 | importer.py:307-310 | `list_sources` 分页是内存切片（先全量 GROUP_CONCAT） | SQL 层分页 |
| 28 | P3 | server.py:110-364 | inputSchema 的 default 与 handler 内 `args.get(..., 默认)` 两处维护同一默认值，易漂移；无运行时 schema 校验 | 从 inputSchema 生成默认值解析器 |
| 29 | P3 | docs/MCP_INTEGRATION.md:26-32 | 文档与代码漂移：`shibei_import` 缺 `file_pattern`、`shibei_remember` 缺 `source`/`expires_at` | 同步文档 |
| 30 | P3 | dreamer.py:372-390、consolidator.py:143-155 | `brief` 格式不裁剪 housekeeping 的 clusters 全文摘要；`consolidate` 候选响应含整段拼接 summary——MCP 响应体积可能巨大 | brief 只返回簇元数据 |
| 31 | P3 | server.py:367-453 | MCP 循环无超时/进度：dream/consolidate O(n²) 阻塞后续工具，无 progress 通知 | 长任务异步化或先限制 max_chunks |
| 32 | P3 | memory.py:140-143 | `remember` 强制把 source 并入 tags 并排序，用户标签顺序/语义被改写 | 分开存储或文档说明 |

### 已确认的正面实践（避免误伤）

- 全库 SQL 值均参数化，表名常量；API key 仅环境变量（llm.py:36-41）；`config.yaml` 不入库（agent.md:134）。
- 核心路径零 LLM 依赖；jieba/tqdm/LLM/文件解析全部优雅降级（tokenizer.py:32-41、cli.py:211-214、llm_rewriter.py:102-105、importer.py:93-100）。
- 惰性 import 有效避免了模块级循环依赖；`Shibei` 门面职责单一。
- 协议契约有测试背书：tests/test_server.py 覆盖 initialize / tools/list(9 个) / 全工具分发 / 未知工具 / 异常 isError / 通知静默。
- 增量导入（manifest）+ 精确去重（chunk hash）+ 版本链（supersedes）设计成熟，`clean_orphans` 有 dry-run。

---

*报告结束。所有结论基于当前工作区源码；行号随代码演进可能偏移。*
