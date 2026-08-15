# 拾贝（Shibei）项目研究报告 — 汇总

> 分析时间：2026-08-16 ｜ 配套三份深度报告：
> - [storage-retrieval-layer-analysis.md](./storage-retrieval-layer-analysis.md) — 核心存储与检索层（703 行）
> - [ANALYSIS_AGENT_LAYER.md](./ANALYSIS_AGENT_LAYER.md) — Agent 接入层（415 行）
> - [../ANALYSIS_REPORT.md](../ANALYSIS_REPORT.md) — 智能/整理层 + 工程配置 + 测试（458 行）

---

## 1. 项目定位

**拾贝（Shibei）** — 给 AI Agent 用的中文轻量知识库 + 长期记忆（「外部大脑」）。
扫描 Markdown/Word/Excel/TXT → 中文语义分块 → SQLite FTS5 BM25 全文检索 + 近义词/LLM 改写 → 通过 MCP 供 Agent 答前强制召回。

- **三层接口**：CLI（`shibei ...`）｜ MCP server（`shibei serve`，9 个工具）｜ Python（`from shibei import Shibei`）
- **技术栈**：Python ≥3.9，运行依赖仅 `pyyaml` + `jieba`；零外部数据库/向量库
- **状态**：0.1.0 Alpha，9 个 commit，单作者（Miles128/四海），git 最后提交 7/20
- **多宿主契约**：Claude / Cursor / Trae / Hermes 通过「提示词契约」接入（`.cursor/rules/shibei-mcp-context.mdc`、`.trae/skills/shibei-dreaming/SKILL.md`）

## 2. 存储架构（记忆存在哪）

全部数据落在 **`~/.shibei/db/`**（`config.yaml` 的 `storage.path`）：

| 文件 | 内容 | 备注 |
|------|------|------|
| **`fts5.db`** | 主库：`chunks`/`chunks_fts`（文档 chunk）+ `memories`/`memories_fts`（对话记忆） | 文档与记忆**共库**，靠 `source`/`tags` 区分 |
| `manifest.json` | 文件级 MD5 增量追踪 | |
| `chunk_hashes.json` | chunk 级 MD5 去重 | |
| `dream_result.json` + `dreams/` | Dreaming 洞察持久化 | 每次 dream 存时间戳 JSON |
| `~/.shibei/profiles/` | Profile 配置（多场景切换） | 当前为空 |

**FTS5 两种模式**（重要设计约束）：
- `chunks_fts` = **contentless**（`content=''`）：**不支持 DELETE**，删除必须整表重建（bm25.py:82-95 `_rebuild_fts_index`）
- `memories_fts` = **external content**（`content='memories'`）：无触发器，直改 memories 表会静默失同步

**记忆版本链**：`supersedes`/`superseded_by` 字段，新事实与旧记忆 Jaccard ≥0.7 时建链，历史可追溯。

## 3. 架构分层

```
CLI / MCP Server (server.py 手写 stdio+JSON-RPC 2.0，零 MCP SDK)
   └── Shibei 门面 (services.py，惰性 import)
         ├── Context     答前上下文包（抽词 + 文档/记忆双路召回）
         ├── Importer    扫描→解析→标签→分块→摘要→去重→索引
         ├── Searcher    规则/LLM 改写 → 同义词扩展 → BM25 双列 RRF
         ├── MemoryStore 对话长期记忆 + 版本链
         ├── Dreamer     跨文档洞察 + 整理建议
         ├── Consolidator 相似 chunk 聚类/合并
         ├── Related     标签共现 / Markdown 链接 / 关键词共现
         └── 基础设施    bm25 / tokenizer / chunker / parsers / tagger / manifest / dedup / profile / llm
```

## 4. 测试状况

```
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
→ 166 passed in 0.85s（0 失败 / 0 跳过，Python 3.12.13）
```

26 个测试文件，隔离性优秀（全 tmp 目录 + monkeypatch）。缺口：dreamer 采样分支、consolidate apply 数据保真、related 关键词共现单独场景、性能基准、端到端集成。

## 5. 重大问题清单（合并三份报告，按优先级）

### 🔴 高（数据丢失 / 崩溃 / 语义颠倒）

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| 1 | **`consolidate --apply` 静默截断合并内容** | dreamer.py:133 `content[:500]` → consolidator.py:89-98/195-203 | 800 字符原文合并后仅剩 500 字符，**数据永久丢失** |
| 2 | **BM25 score 归一化方向反了** | bm25.py:199、memory.py:269 `1/(1+abs(rank))` | FTS5 rank 越负越相关 → 最相关结果得分反而最低；小语料全部塌缩为 1.0。排序正确，仅暴露给 Agent 的 score 语义颠倒 |
| 3 | **dreamer 采样有偏** | dreamer.py:115-120 `rowid % step = 0` | step=2 时只取偶数 rowid，**系统性丢一半数据**；total<2×max_chunks 时取前 N 行 |
| 4 | **MCP server JSON 解析失败静默丢弃** | server.py:372-374 | 不回 -32700 错误，客户端挂起 |
| 5 | **顶层非 dict/batch 请求崩溃整个 server** | server.py:376 | `req.get` 直接异常 |
| 6 | **未实现 MCP 必需 `ping`** | server.py:445-450 | 协议不合规 |
| 7 | **噪音查询崩溃（500）** | tokenizer.py:107/168 → bm25.py MATCH | `tokenize_query("###")` 返回原文 → `fts5: syntax error` |
| 8 | **`--full` 并非全量重建** | importer.py:76-79/162-186 | hash store 未清空 → chunking 配置变更后新旧 chunk 在 bm25 重复；注释与实现矛盾 |
| 9 | **删除+插入不在同一事务** | bm25.py、consolidator.py:174-219/222-301 | 中途异常即半删状态 + 两个 JSON 直写，三份状态不一致 |
| 10 | **`apply_consolidation` 完全信任外部 rowids** | consolidator.py:222-301 | 无归属校验/无确认的破坏性写入 |

### 🟠 中（正确性 / 健壮性）

| # | 问题 | 位置 |
|---|------|------|
| 11 | tag 用 `LIKE '%tag%'` 子串匹配，`ai` 误命中 `daily` | bm25.py:173 |
| 12 | 记忆过期用 ISO 字符串字典序比较，时区偏移即误判 | memory.py:364-375 |
| 13 | supersedes 版本链无限增长，被替代的过期记忆永不清扫 | memory.py |
| 14 | `memory_id LIKE '{id}%'` 通配符误删 | memory.py:325-327/343-345 |
| 15 | memories 不截断，可撑爆 context pack | context.py:147-156 |
| 16 | `--debug`（`SHIBEI_MCP_DEBUG`）只写不读，完全无效 | cli.py:21 |
| 17 | `dream --persist` 是死参数（store_true + default=True） | cli.py:629-630 |
| 18 | 每次调用新建 MemoryStore/BM25Index，重复 DDL/PRAGMA | services.py:93-95、bm25.py:28-80 |
| 19 | 错误回传 `Error: {e}` 未脱敏 | server.py:436-444 |
| 20 | config 不存在/未知键静默降级或 TypeError | config.py:151-153 |
| 21 | 增量导入每文件 ≥3 次全量读取（parse + 去重 hash + manifest 重算） | importer.py |

### 🟡 低（性能 / 风格 / 文档）

- consolidator→dreamer 依赖错位（consolidator.py:23 顶部 import dreamer）
- cli.py:61-75 副作用改 config；cli.py:482 硬编码数据目录；注解风格混用
- dream/consolidate O(n²)，响应体积巨大；MCP 循环无超时/进度
- PRD 文档漂移：工具数 8 vs 9、`shibei init` 无实现、P2「轻量向量混合」规格锁定待开发

## 6. 设计优点（已核实）

- 全库 **SQL 参数化**、API key 仅环境变量、YAML safe_load
- 核心路径**零 LLM 依赖**；jieba/tqdm/LLM/解析器全链路优雅降级
- 增量导入 + 精确去重 + supersedes 版本链
- 手写 MCP 协议层短小清晰，契约有 test_server.py 背书
- 策略/插件注册表（`register_parser`/`register_engine`）、门面+懒加载解环

## 7. 本次清理记录（2026-08-16）

- ✅ **迁移**：旧 `memories.db` 的 22 条记忆（2026-06 项目背景信息）→ 迁入 `fts5.db`（记忆 176→**198**，FTS 索引重建，搜索验证通过）
- ✅ **删除**：`memories.db`（旧独立记忆库）、`chroma.sqlite3`（移除 vector 功能的残留）、`embedding.cpython-312.pyc`（已删模块残留）
- ⏸️ 保留：`fts5.db.backup`（现架构备份）、`shibei_export.md`（7/9 手动导出）、`dreams/`（Dreaming 数据）
- 📌 注意：`/tmp/fts5.db.pre-migration.bak` 是迁移前备份，确认无误后可删

## 8. 建议的后续动作（按性价比排序）

1. **修 score 归一化**：改成 `1/(1+abs(rank))` 的倒数或直接暴露 `-rank`（一行改动，影响所有下游 Agent 的排序判断）
2. **修 consolidate 截断**：启发式摘要前不截断 content，或 apply 时保留原文引用
3. **修 dreamer 采样**：用 `ORDER BY rowid LIMIT offset` 均匀分页替代 `rowid % step`
4. **MCP server 加固**：JSON 解析错误回 -32700、顶层类型校验、实现 ping
5. **事务化写入**：bm25 删/增 + JSON 元数据放同一事务；apply_consolidation 加归属校验
6. **清理未提交改动**：工作区有 8/3 的 12 个文件未提交（新增 RelatedConfig、tokenizer 中英混合切分修复等），建议尽快 commit
