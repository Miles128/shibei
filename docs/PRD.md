# 拾贝（Shibei）产品需求文档（PRD）

> 版本：v1.4
> 日期：2026-07-20
> 状态：持续迭代中
> 权威中文名：**拾贝**；英文/包名/命令：Shibei / `shibei`

---

## 1. 文档信息

### 1.1 什么是拾贝

拾贝（Shibei）是一个面向 AI Agent 的**中文轻量知识库 + 长期记忆系统**，定位为 Agent 的“外部大脑”。它将个人本地文档（Markdown、Word、Excel、CSV、TXT）转化为可搜索、可关联、可演化的结构化记忆，并通过 MCP（Model Context Protocol）协议暴露给任何兼容的 AI Agent。

### 1.2 一句话定位

**“中文轻量知识库 + Agent 长期记忆 —— 给 AI Agent 用的个人外部大脑。”**

### 1.3 文档目标

- 明确产品定位、目标用户与核心价值
- 梳理竞品格局与差异化机会
- 定义功能规格、技术架构与路线图
- 为开发提供统一依据

---

## 2. 产品定位

### 2.1 问题定义

AI Agent 正在从“单次会话工具”演化为“长期协作助手”，但面临三个核心问题：

1. **上下文窗口有限**：无法一次性加载全部个人知识
2. **会话隔离**：每次重启都丢失上下文，需要重复交代背景
3. **知识沉淀困难**：个人笔记、文档、思考散落各处，Agent 无法按需调用

现有方案要么太重（AnythingLLM、Dify、MaxKB 等企业平台），要么需要 LLM API（Mem0、Hindsight、Cognee），要么对中文支持弱，要么无法把“文档知识库”和“对话记忆”统一检索。

### 2.2 市场空白

| 市场分层 | 代表产品 | 空白点 |
|---------|---------|--------|
| 企业级知识平台 | Dify、MaxKB、AnythingLLM | 太重、多容器、面向团队 |
| Agent 交互记忆 | Mem0、Hindsight、Letta | 偏向行为记忆，需要 LLM API |
| 知识图谱记忆 | Cognee、Zep/Graphiti | 构建慢、需 LLM、中文弱 |
| 纯向量 RAG | 各类 Chroma 封装 | 缺乏 BM25、MCP、中文优化 |
| 文档库 + 记忆 + MCP | **拾贝** | 唯一同时覆盖三者的纯本地中文方案 |

**拾贝的机会**：在“个人 + 中文 + 轻量 + 本地 + MCP + 文档记忆统一”的交叉点上，做最贴合个人用户的 Agent 外部知识库。

### 2.3 产品愿景

- **短期**：中文个人知识库 MCP Server
- **中期**：个人 AI 记忆中枢（文档库 + 交互记忆 + 跨文档洞察）
- **长期**：每个人的私有 AI 第二大脑

---

## 3. 目标用户与场景

### 3.1 目标用户

| 用户画像 | 特征 | 核心痛点 |
|---------|------|---------|
| **AI 极客开发者** | 使用 Cursor/Claude/Trae 等 Agent IDE | 想让 Agent 记住项目文档、个人笔记 |
| **知识工作者** | 律师、咨询师、研究员、产品经理 | 笔记多、查找慢、想让 Agent 直接基于笔记回答 |
| **终身学习者** | 大量阅读、做笔记、写复盘 | 知识无法被 Agent 调用，无法形成洞察 |
| **中文用户** | 中文文档为主 | 海外方案中文搜索差、配置麻烦 |

### 3.2 核心场景

1. **Agent 问答**：“我去年写的银行风控复盘里提到哪些指标？”
2. **项目上下文**：“根据我的项目笔记，这个 API 应该怎么设计？”
3. **面试准备**：“搜一下我面试知识库里关于 Redis 的问题”
4. **跨文档洞察**：“我最近的笔记里，AI 和金融有哪些隐藏联系？”
5. **长期记忆**：“用户偏好 Python 而不是 Go，后面推荐方案时默认 Python”
6. **增量同步**：每天新增日记/文章后自动入库，Agent 立即能搜

---

## 4. 核心价值主张

### 4.1 价值主张陈述

> 对希望让 AI Agent 长期记住并使用个人知识的中文用户而言，Shibei 是一个**零外部依赖、纯本地、MCP 原生**的轻量知识库 + 长期记忆系统。与 Hy-Memory 相比，它开源可控且支持文档库；与 TencentDB Agent Memory 相比，它原生 MCP 化且更聚焦个人场景；与 agentmemory 相比，它支持中文文档库、BM25 全文召回和轻量关联。Shibei 是目前唯一同时覆盖“文档知识 + 对话记忆 + MCP + 纯本地 + 中文”的方案。

### 4.2 核心差异化

| 维度 | Shibei | 竞品现状 |
|------|--------|---------|
| **中文原生** | BM25 + jieba/bigram，中文摘要生成 | Hy-Memory/TencentDB 中文优化；agentmemory 默认英文 |
| **零 LLM 依赖（搜索阶段）** | 搜索、BM25 召回、Dreaming 基础版无需 LLM API | agentmemory 无需 LLM；Hy-Memory/TencentDB 需要 |
| **纯本地轻量** | `pip install`，SQLite FTS5，秒级启动 | 四者均偏本地，但 Shibei 同时支持文档库+MCP+记忆 |
| **MCP 原生** | stdio MCP server，5 个工具 | Hy-Memory 是插件；TencentDB/agentmemory 无原生 MCP |
| **文档知识库** | 完整支持 Markdown/Word/Excel 导入、分块、搜索 | 三者均不支持通用文档库 |
| **对话记忆** | `shibei_remember` + SQLite + BM25 + 版本链 | Hy-Memory/TencentDB/agentmemory 均支持 |
| **BM25 双列索引** | 正文 + 摘要双列 FTS5 索引，可扩展摘要召回 | 多数竞品无 |
| **轻量关联** | 标签共现、Markdown 链接、关键词共现，无需图数据库 | 多数竞品依赖向量或图谱 |
| **Profile 多场景** | 面试/工作/日记等多知识库切换 | 多数无此概念 |
| **Dreaming 自动整理** | 清理过期记忆、压缩旧短期记忆、合并相似 chunk | 竞品无 |

---

## 5. 竞品分析

### 5.1 主要竞品

| 竞品 | 定位 | 威胁 | Shibei 的机会 |
|------|------|------|--------------|
| **Hy-Memory** | 闭源 Agent 记忆压缩插件 | 腾讯背书、中文原生、版本演化链先进 | 闭源不可控；不支持文档库；仅特定 IDE |
| **TencentDB Agent Memory** | 开源四层记忆架构 | 同路线（SQLite+中文）、Apache 2.0 | 无原生 MCP；知名度低；文档库缺失 |
| **agentmemory** | Coding Agent 记忆工具 | 三流融合检索精度高、零 LLM 依赖 | 仅适合 Coding；默认英文；无文档库 |
| **Shibei** | 中文个人文档知识库 + MCP + 记忆 | — | 唯一同时覆盖文档库 + MCP + 纯本地 + 中文 + 记忆 |

### 5.2 竞品启示

| 竞品 | 应学习 | 应避免 |
|------|--------|--------|
| **Hy-Memory** | supersedes 版本演化链；记忆压缩分层 | 不要闭源；不要绑定特定 IDE |
| **TencentDB Agent Memory** | 四层渐进式架构；中文本地优化 | 不要缺失 MCP 和文档库 |
| **agentmemory** | 零 LLM 依赖设计 | 不要局限于 Coding Agent |

### 5.3 竞争策略

1. **错位竞争**：不做闭源插件，不做通用 Agent 平台，专注“个人中文文档知识库 + 对话记忆”
2. **MCP-first**：竞品均不原生 MCP，这是 Shibei 最大壁垒
3. **文档+记忆统一**：其他产品只做对话/行为记忆，Shibei 能把文档知识和对话记忆融合检索
4. **关联增强召回**：轻量关联提升搜索质量
5. **隐私优势**：纯本地，数据不上云，无 LLM API 也能用
6. **中文优势**：BM25 中文分词 + 中文 Dreaming

---

## 6. 功能规格

### 6.1 功能总览

| 模块 | 功能 | 优先级 | 状态 |
|------|------|--------|------|
| 文档导入 | 多格式解析、增量导入、自动标签 | P0 | 已完成 |
| 分块引擎 | 标题切分、句子边界、overlap、parent-child | P0 | 已完成 |
| 搜索引擎 | BM25 全文搜索，正文+摘要双列 FTS5 索引 | P0 | 已完成 |
| 答前上下文 | `shibei_context` 多宿主强制召回入口 | P0 | 已完成 |
| 查询扩展 | 外部同义词表 + 编辑距离模糊匹配 | P1 | 已完成 |
| 查询改写 | 规则改写 + 可选 LLM 关键词扩写 | P1 | 已完成 |
| 摘要召回扩展 | 可配置 LLM 为每个 chunk 生成一句话中文摘要，RRF 融合正文+摘要 | P2 | 已完成 |
| 轻量关联 | 标签共现、Markdown 链接、关键词共现 | P1 | 已完成 |
| Profile | 多场景配置切换 | P1 | 已完成 |
| MCP Server | 9 个工具：context/search/import/list_sources/dream/remember/update_memory/consolidate/apply_consolidation | P0 | 已完成 |
| 对话记忆 | `shibei_remember` + SQLite + BM25 + 版本链 | P0 | 已完成 |
| Dreaming | 跨文档关联、洞察、自动整理（记忆清理/压缩/相似 chunk 合并） | P1 | 已完成 |
| 去重压缩 | 精确去重、相似 chunk 合并 | P1 | 已完成 |
| 一键安装 | `shibei init` 初始化配置和环境 | P1 | 已完成 |
| 轻量向量混合 | 可选本地 embedding + BM25 RRF（见 10.3） | P2 | 规格锁定 |
| Web/Notion 连接器 | 在线数据源导入 | P2 | 待开发 |
| HTTP API server | 除 MCP 外的 REST 接口 | P2 | 待开发 |

### 6.2 详细功能规格

#### 6.2.1 文档导入

- 支持格式：Markdown、TXT、DOCX、XLSX、CSV
- 增量导入：基于文件 MD5 的 manifest 追踪
- 全量重建：`--full` 参数忽略缓存
- 自动标签：基于正则规则扫描文件路径 + 内容前 500 字
- 自动摘要：可选 LLM 为每个 chunk 生成一句话中文摘要，API key 仅通过 `SHIBEI_LLM_API_KEY` 环境变量读取；摘要失败时降级为空字符串，不阻断导入

#### 6.2.2 分块引擎

- 三级策略：
  1. `split_pattern` 按标题切分（如 `\n(?=## )`）
  2. 句子边界切分（`。！？!?\n`），不切断词
  3. 强制字符切分（超过 `max_chars` 硬切）
- `overlap`：相邻 chunk 重叠 N 字符
- `parent-child`：小 chunk 用于索引，大 chunk（`parent_window` 个相邻 child）用于返回上下文

#### 6.2.3 搜索引擎

- **BM25**：SQLite FTS5，中文 bigram + 可选 jieba
  - content_tokens：原始正文索引
  - summary_tokens：摘要索引（启用 LLM 时索引一句话中文摘要）
  - 搜索时 RRF 融合正文和摘要结果
- 支持标签过滤、limit 控制
- 返回结果附带 `related` 相关推荐

#### 6.2.4 查询扩展

通过外部同义词表提升召回，零向量库依赖：

- **同义词表**：YAML/JSON，`{"大模型": ["LLM", "人工智能", "AI"]}`
- **查询时扩展**：`shibei search "大模型"` 自动扩展为 `大模型 OR LLM OR 人工智能 OR AI`
- **模糊匹配兜底**：开启 `fuzzy=true` 后，同义词表未命中时用编辑距离找相近词
- **配置**：
  ```yaml
  expander:
    enabled: true
    synonyms_path: ./data/synonyms.yaml
    fuzzy: false
  ```

#### 6.2.5 MCP 工具

| 工具名 | 参数 | 返回 |
|--------|------|------|
| `shibei_context` | message, limit, tag, write_hints | context pack（query_used、results、memories、related、suggest_remember） |
| `shibei_search` | query, limit, tag | 排序结果列表（含 parent_text、related） |
| `shibei_import` | dirs（可选）, full（可选） | 导入统计 |
| `shibei_list_sources` | limit, offset | 文件列表 + 聚合摘要 |
| `shibei_dream` | format（可选，brief/full）, persist（可选）, housekeeping（可选） | 洞察报告 + 整理统计 |
| `shibei_remember` | fact, category, source, tags | 写入结果（含 memory_id） |
| `shibei_update_memory` | memory_id, fact, tags | 更新/压缩结果 |
| `shibei_consolidate` | threshold, apply | 相似 chunk 聚类；apply=true 直接合并 |
| `shibei_apply_consolidation` | clusters | 根据外部摘要合并相似 chunk |

**多宿主契约**：非纯指令类用户消息 → 先 `shibei_context` 再回答；`suggest_remember.should=true` 时调用 `shibei_remember`。各宿主只粘贴同一段规则，不绑 IDE Hook。

#### 6.2.6 对话记忆

**目标**：让 Agent 在对话中主动把重要事实写入 Shibei，后续搜索时同时召回文档 chunk 和记忆条目。

**数据模型**：

```sql
CREATE TABLE memories (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,       -- UUID，对外暴露
    fact TEXT NOT NULL,            -- 事实文本
    category TEXT,                 -- user / agent / project / preference
    source TEXT,                   -- 来源会话/工具调用ID
    tags TEXT,                     -- 逗号分隔标签
    created_at TEXT,               -- ISO 8601
    expires_at TEXT,               -- 过期时间（可选）
    supersedes TEXT,               -- 替代的上一版本 memory_id（版本链）
    superseded_by TEXT             -- 被哪个新版本替代
);

CREATE VIRTUAL TABLE memories_fts USING fts5(
    fact, tags, category,
    content='memories', content_rowid='rowid'
);
```

**召回模式**：仅 BM25 FTS5 全文检索。

**版本链逻辑**（借鉴 Hy-Memory）：

1. 写入新 fact 时，先检索相似度最高的现有记忆
2. 使用 BM25 + Jaccard 判断语义相似
3. 如果相似度 > 阈值（默认 0.7，可配置），新记忆标记 `supersedes = 旧记忆.id`
4. 旧记忆不删除，仅标记为 `superseded_by = 新记忆.id`
5. 默认搜索只返回最新版本；`dream` 和审计场景可查看历史链

**与文档库的融合搜索**：

```python
chunk_results = search_chunks(query)
memory_results = search_memories(query)
result = {
    "results": chunk_results,
    "memories": memory_results,
    "related": find_related(chunk_results)
}
```

#### 6.2.7 轻量关联

不引入图数据库，基于已有索引元数据计算文档关联：

- **标签共现**：共享标签越多，关联越强
- **Markdown 链接**：解析 `[[...]]` 和 `[...](path)`，建立文档级链接
- **关键词共现**：基于 BM25 token 分布的 Jaccard 相似度

输出：`related` 字段，包含 `source`、`score`、`reason`。

#### 6.2.8 Dreaming

- 关键词提取：优先 jieba，降级正则 bigram
- 跨文档连接：共享关键词的文档对
- 主题模式：标签下高频主题
- 洞察：全局高频主题、孤立文档
- 输出格式：`format="brief"` 返回统计+洞察+整理摘要；`format="full"` 额外返回 cross_references/patterns 详情
- 持久化：`persist=true` 保存结果到 `db_path/dream_result.json`，供后续 diff
- **自动整理**（零 LLM 依赖）：
  - 清理过期记忆
  - 识别可压缩记忆候选，由外部 Agent 决策是否压缩
  - 基于 Overlap 系数聚类识别相似 chunk 候选；`shibei_consolidate --apply` 可直接启发式合并

### 6.3 非功能需求

| 维度 | 要求 |
|------|------|
| 性能 | 单次搜索 < 1s（本地库 < 1 万 chunk） |
| 隐私 | 纯本地运行，数据不上传 |
| 可扩展性 | 单用户知识库，10 万 chunk 级别 |
| 兼容性 | Python ≥ 3.9，macOS/Linux/Windows |
| 易用性 | 一键安装、配置即 YAML |

---

## 7. 技术架构

### 7.1 架构图

```
┌─────────────────────────────────────────┐
│           MCP Clients                   │
│  (Claude Desktop / Cursor / Trae / ...) │
└─────────────────┬───────────────────────┘
                  │ MCP JSON-RPC over stdio
                  ▼
┌─────────────────────────────────────────┐
│         shibei.server                   │
│  ┌─────────┐ ┌─────────┐ ┌───────────┐ │
│  │ search  │ │ import  │ │ list/dream│ │
│  │ remember│ │         │ │           │ │
│  └────┬────┘ └────┬────┘ └─────┬─────┘ │
└───────┼───────────┼────────────┼───────┘
        │           │            │
        ▼           ▼            ▼
┌─────────────────────────────────────────┐
│           Shibei 服务层                 │
│  ┌─────────┐ ┌─────────┐ ┌───────────┐ │
│  │ Searcher│ │ Importer│ │ Dreamer   │ │
│  │(BM25 +  │ │(summary │ │(关联+整理)│ │
│  │ related)│ │ + dedup)│ │           │ │
│  └────┬────┘ └────┬────┘ └─────┬─────┘ │
│  ┌─────────┐ ┌───────────────────┐     │
│  │MemoryStore│  │ RelatedFinder   │     │
│  │(BM25)   │  │(tags/links/    │     │
│  │+版本链   │  │ keywords)       │     │
│  └────┬────┘ └───────────────────┘     │
└───────┼─────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────┐
│           存储层（SQLite）               │
│  ┌─────────────────┐ ┌─────────────────┐│
│  │ SQLite FTS5     │ │ manifest        ││
│  │ (BM25 全文索引)  │ │ (增量追踪)       ││
│  └─────────────────┘ └─────────────────┘│
│  ┌─────────────────┐ ┌─────────────────┐│
│  │ chunk_hashes    │ │ memories        ││
│  │ (精确去重)       │ │ (对话记忆)       ││
│  └─────────────────┘ └─────────────────┘│
│  ┌─────────────────┐                    │
│  │ memories_fts    │                    │
│  │ (BM25 记忆索引)  │                    │
│  └─────────────────┘                    │
└─────────────────────────────────────────┘
```

### 7.2 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| Config | `src/shibei/config.py` | YAML + 环境变量配置管理 |
| Context | `src/shibei/context.py` | 答前抽词 + context pack |
| Chunker | `src/shibei/chunker.py` | 文档分块、overlap、parent-child |
| Importer | `src/shibei/importer.py` | 扫描、解析、增量导入、去重、摘要生成 |
| BM25 | `src/shibei/bm25.py` | SQLite FTS5 全文索引（正文+摘要双列） |
| Searcher | `src/shibei/searcher.py` | 规则/LLM 改写 → 扩展 → BM25 + 记忆 + related |
| LLMRewriter | `src/shibei/llm_rewriter.py` | LLM 查询关键词扩写 |
| Expander | `src/shibei/expander.py` | 同义词表 + 编辑距离查询扩展 |
| MemoryStore | `src/shibei/memory.py` | 对话记忆存储 + BM25 召回 + 版本链 |
| RelatedFinder | `src/shibei/related.py` | 标签/链接/关键词共现关联发现 |
| Dreamer | `src/shibei/dreamer.py` | 跨文档关联挖掘、自动整理 |
| Consolidator | `src/shibei/consolidator.py` | 相似 chunk 聚类合并 |
| Server | `src/shibei/server.py` | MCP JSON-RPC server |
| CLI | `src/shibei/cli.py` | 命令行接口 |

---

## 8. 数据模型

### 8.1 Chunk 数据

```
chunk_id: str          # 唯一标识
source: str            # 源文件路径
tags: str              # 逗号分隔标签
text: str              # chunk 文本（索引用）
summary: str           # 一句话中文摘要（摘要索引用；LLM 禁用时为空字符串）
parent_text: str        # 父 chunk 文本（返回上下文用）
parent_id: str          # 父 chunk ID
```

### 8.2 BM25 表结构

```sql
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    source TEXT,
    file_path TEXT,
    tags TEXT,
    content TEXT,
    parent_text TEXT,
    parent_id TEXT,
    summary TEXT
);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
    content_tokens,
    summary_tokens,
    content=''
);
```

### 8.3 对话记忆表

见 6.2.5。

---

## 9. 接口设计

### 9.1 CLI

```bash
# 初始化
shibei init [--force]

# 导入（支持位置参数或 --dirs）
shibei import [DIR1] [DIR2] ...
shibei import [--full] [--dirs DIR1,DIR2]

# 答前上下文
shibei context "用户消息原文" [--limit N] [--tag TAG]

# 搜索
shibei search "query" [--tag TAG] [--limit N] [--llm-rewrite]

# 列出来源
shibei list [--limit N] [--offset N]

# 清理源文件已删除的 orphan chunks
shibei clean [--dirs DIRS] [--dry-run]

# Dreaming
shibei dream [--no-housekeeping] [--format brief|full] [--no-persist]

# 合并相似 chunk
shibei consolidate [--apply] [--threshold F] [--output FILE]
shibei apply-consolidation clusters.json

# 对话记忆
shibei remember "用户在面试准备中重点关注 Redis 和 Kafka" --tags 面试,Redis
shibei memories list [--limit N] [--category user]
shibei memories forget MEMORY_ID

# Profile
shibei profile create NAME
shibei profile list
shibei profile switch NAME

# MCP Server
shibei serve [--debug]
```

### 9.2 MCP Tools

详见 6.2.4。

### 9.3 Python SDK

```python
from shibei import Shibei

brain = Shibei("config.yaml")
results = brain.search("银行风控", limit=5, tag="finance")
brain.import_all(dirs=["~/Documents/notes"])
sources = brain.list_sources(limit=20)
insights = brain.dream()

# 对话记忆
brain.remember(
    "用户在面试准备中重点关注 Redis 和 Kafka",
    category="user",
    tags="面试,Redis"
)
memories = brain.search_memories("面试准备")
```

---

## 10. 路线图

### 10.1 已完成（v0.3.x）

- [x] 多格式文档导入（md/txt/docx/xlsx/csv）
- [x] BM25 全文搜索，正文+摘要双列 FTS5 索引
- [x] 增量导入 + 精确去重
- [x] Profile 多场景切换
- [x] MCP Server（8 个工具）
- [x] 对话记忆 + 版本链 + BM25 召回
- [x] 查询扩展：同义词表 + 编辑距离模糊匹配
- [x] 轻量关联（标签共现、Markdown 链接、关键词共现）
- [x] Dreaming 跨文档洞察 + 自动整理（记忆清理/压缩/相似 chunk 合并）
- [x] 中文分词优化（jieba 可选）
- [x] Parent-child chunk + overlap
- [x] `shibei init` 一键安装
- [x] 修复 contentless FTS5 批量删除导致的 database disk image malformed

### 10.2 短期（v0.4.x）

- [x] 一句话摘要 + 正文/摘要双索引 RRF 融合
- [x] 答前上下文 `shibei_context`（多宿主强制召回）
- [x] 查询改写：规则 + 可选 LLM 关键词扩写
- [ ] 导入进度条与并行解析
- [ ] 更完善的中文停用词表
- [ ] 记忆过期自动清理策略可配置
- [ ] 补齐 CLI/MCP server 单元测试

### 10.3 中期（v0.5.x）— 轻量向量混合（规格锁定）

**目标**：在不引入 Chroma/Faiss 服务的前提下，用可选本地 embedding 补齐近义召回，与 BM25 RRF 融合。

| 项 | 规格 |
|----|------|
| 依赖 | 可选 extras：`onnxruntime` + 小中文模型（如 `bge-small-zh-v1.5` ONNX），或 `sentence-transformers` |
| 存储 | 同目录 SQLite：`chunk_embeddings(rowid INTEGER PK, dim INTEGER, vector BLOB)`；与 `chunks.rowid` 对齐 |
| 默认 | `embedding.enabled: false`；开启后才加载模型 |
| 导入 | `shibei import` 增量补嵌；`shibei reindex --embeddings` 全量重建 |
| 搜索 | 向量 TopK + BM25 content/summary TopK → **三路 RRF**（复用现有融合点，外层于 `Searcher`） |
| 记忆 | 一期仅 chunk；记忆向量化二期 |
| 失败降级 | 模型缺失/推理失败 → 仅 BM25，不阻断搜索 |

其它中期项：

- [ ] Web 页面连接器（URL 抓取）
- [ ] Notion / Obsidian 连接器
- [ ] HTTP API server（除 MCP 外）

### 10.4 长期（v1.0+）

- [ ] 个人 AI 记忆中枢：文档库 + 交互记忆 + 日程/邮件等多源数据统一
- [ ] 插件生态：自定义 parser、connector、search strategy

---

## 11. 成功指标

| 指标类型 | 指标 | 目标 |
|---------|------|------|
| 可用性 | 一键安装成功率 | > 90% |
| 性能 | 1 万 chunk 搜索延迟 | < 1s |
| 性能 | 导入速度 | > 10 文件/分钟 |
| 质量 | Top-5 搜索命中率（人工评估） | > 85% |
| 质量 | Dreaming 洞察可用率 | > 70% |
| 生态 | MCP 兼容 Agent 数 | 支持 Claude/Cursor/Trae/Hermes |
| 质量 | 测试覆盖率 | 核心测试持续通过 |

---

## 12. 风险与假设

### 12.1 风险

| 风险 | 影响 | 缓解策略 |
|------|------|---------|
| 技术护城河浅 | 高 | 强化中文优化、Dreaming、轻量关联 |
| 竞品快速跟进 | 中 | 聚焦个人场景，保持轻量 |
| 用户增长慢 | 中 | 做好 MCP 接入文档，录制短视频教程 |
| 大库性能下降 | 中 | 引入批量查询、索引优化、相似 chunk 合并 |

### 12.2 关键假设

1. 个人用户愿意在本地运行一个 Python 包来管理知识库
2. MCP 协议会在 2026-2027 年成为 Agent 集成的标准
3. 中文用户对“无需 LLM API 即可搜索”的纯本地方案有需求
4. 用户主要使用 Markdown/Word/Excel 等本地文档

---

## 13. 附录

### 13.1 相关文档

- [README.md](../README.md)
- [CONFIG.md](CONFIG.md)
- [MCP_INTEGRATION.md](MCP_INTEGRATION.md)

### 13.2 Sources

- [腾讯混元 Hy-Memory：Agent记忆越少反而越聪明](https://www.geekpark.net/news/365698)
- [腾讯 ima 正式解锁 Agent 形态](http://tech.cnr.cn/techgd/20260429/t20260429_527605152.shtml)
- [腾讯网盘：为 Agent 而生](https://36kr.com/p/3858282630822537)
- [行业 Agent 记忆：国内 Agent 记忆方案概览](https://blog.csdn.net/weixin_49657774/article/details/160903064)
- [Agent 六款开源记忆工具大横评](http://m.toutiao.com/group/7652659795845071402/)
- [Coze 3.0 正式发布](https://www.aitop100.cn/coze3.0)
- [Hindsight: Open-Source MCP Memory Server](https://hindsight.vectorize.io/blog/2026/03/04/mcp-agent-memory)
- [Cognee: How Cognee Builds AI Memory](https://www.cognee.ai/blog/fundamentals/how-cognee-builds-ai-memory)
- [AnythingLLM MCP Compatibility](https://docs.useanything.com/mcp-compatibility/overview)
