# Agent Guide — 拾贝（Shibei）开发指南

> 本文件为 AI Agent 提供项目开发上下文。Agent 在修改本项目前应先阅读此文件。

## MCP 使用规则（必读 · 多宿主契约）

当拾贝作为 MCP server 挂载到任意 Agent（Cursor / Hermes / Trae / Claude Desktop）时，遵循同一套契约，不依赖特定 IDE Hook。

### 必须先调用 `shibei_context` 的场景

收到**非纯指令类**用户消息时，Agent **必须先**召回再回答：

1. 个人经历 / 做过的事：「我之前做过…」「我负责过…」「我们项目里…」
2. 个人情况 / 偏好 / 习惯 / 目标：「我喜欢…」「我计划…」「我的目标是…」
3. 知识库内容：「我笔记里有…」「我好像写过…」
4. 需要个人历史才能回答的问题
5. 其他非琐碎对话（解释概念、给建议、闲聊中带「我」等）

**可跳过 `shibei_context` 的纯指令**：`shibei import`、改配置、跑测试、纯代码编辑指令且不涉及个人知识。

`shibei_search` 仅在用户**明确要求关键词搜索**时使用；日常答前召回一律用 `shibei_context`。

### 查询改写：用 Agent 自己的 LLM（默认）

**不要**依赖拾贝服务端 `SHIBEI_LLM_API_KEY`。答前流程：

1. 用你自己的模型从用户原文抽出 **5–8 个**检索关键词（含同义/中英，去掉人称/语气词）
2. 调用 `shibei_context(message=用户原文, query=关键词)`
3. 基于 pack 回答

服务端 `rewriter.use_llm` 仅给纯 CLI / 无人值守；MCP 场景保持关闭。

### 必须调用 `shibei_remember` 的场景

当用户首次披露以下信息，或 `shibei_context` 返回 `suggest_remember.should=true` 时，Agent **应主动调用** `shibei_remember`：

1. 个人偏好、习惯、目标
2. 项目背景、工作经历、技术栈
3. 关键决策、重要结论
4. 后续对话可能复用的新事实
5. 用户以「我」为主语的陈述
6. 用户主动询问的中立/开放性问题：Agent 的回答本身也可记一条交互偏好

**不要**在服务端自动写入；由 Agent 确认后调用 `shibei_remember`。

### 工具组合顺序

1. 用自己的 LLM 扩写检索词
2. `shibei_context(message, query=关键词)` 召回
3. 基于 pack 回答
4. 若有新事实 → `shibei_remember`
5. 用户明确要搜关键词 → `shibei_search(query=已改写关键词)`

### 宿主粘贴模板（各宿主共用）

将以下段落加入 Cursor Rule / Hermes system / Trae skill / Claude 项目说明：

```
拾贝（Shibei）MCP 契约：
- 非纯指令类用户消息 → 先用你自己的 LLM 抽出 5–8 个检索关键词，再调用
  shibei_context(message=用户原文, query=关键词)，然后回答
- 不要依赖拾贝服务端 LLM / SHIBEI_LLM_API_KEY
- suggest_remember.should=true 或用户披露新事实 → 调用 shibei_remember
- 用户明确要求「搜一下」时用 shibei_search；日常答前召回不用 search 替代 context
```

## 项目定位

拾贝（Shibei）是面向 AI Agent 的中文语义知识库，定位为 Agent 的「外部大脑」。核心能力：

- **文档导入**：扫描目录 → 分块 → 去重 → 可选一句话摘要 → BM25 索引
- **全文搜索**：SQLite FTS5 BM25（正文 + 摘要双列，RRF 融合）；可选规则/LLM 查询改写
- **答前上下文**：`shibei_context` 从用户消息抽词并召回文档 + 记忆
- **对话记忆**：持久化 + supersedes 版本链
- **Dreaming**：跨文档洞察与整理候选（零 LLM 依赖）
- **MCP**：9 个工具：context、search、import、list_sources、remember、update_memory、consolidate、apply_consolidation、dream

## 技术栈

| 层 | 技术 |
|---|---|
| 存储 | SQLite FTS5（BM25）|
| 分词 | jieba（优先）+ bigram 降级 |
| LLM | OpenAI-compatible API（urllib，可选：摘要 / 查询改写）|
| 协议 | MCP JSON-RPC over stdio |
| Python | >=3.9 |

## 项目结构

```
src/shibei/
├── __init__.py          # 包入口
├── __main__.py          # python -m shibei 入口
├── cli.py               # CLI
├── config.py            # YAML 配置
├── services.py          # 服务层
├── context.py           # 答前上下文包
├── importer.py          # 导入
├── searcher.py          # 搜索（规则改写 → LLM 改写 → 扩展 → BM25）
├── llm_rewriter.py      # LLM 查询改写
├── rewriter.py          # 规则查询改写
├── expander.py          # 同义词扩展
├── summarizer.py        # chunk 摘要
├── llm.py               # LLM 客户端
├── bm25.py              # FTS5 索引
├── memory.py            # 对话记忆
├── related.py           # 轻量关联
├── dreamer.py           # Dreaming
├── consolidator.py      # 相似 chunk 合并
├── chunker.py / tokenizer.py / parsers.py / tagger.py
├── manifest.py / dedup.py / profile.py
└── server.py            # MCP Server
```

## 开发规范

### 代码风格

- **类型注解**：所有公开函数必须有参数和返回值类型注解
- **路径**：使用 `pathlib.Path`
- **日志**：核心库用 `logging.getLogger(__name__)`，CLI 可用 `print()`
- **异常**：禁止 bare `except Exception: pass`
- **中文**：注释和文档用中文，标识符用英文

### 架构原则

1. **零 LLM 依赖（核心路径）**：导入 / BM25 搜索 / 记忆可不依赖 LLM；LLM 仅作可选增强
2. **轻量优先**：SQLite FTS5；向量混合为 Phase 2 可选
3. **插件化**：`register_parser()` / `register_engine()`
4. **公共模块**：分词统一在 `tokenizer.py`

### 安全规范

- API Key 仅从 `SHIBEI_LLM_API_KEY` 读取
- `config.yaml` 不提交
- SQLite `timeout=30.0`
- `fact` 不能为空

### 测试规范

- `tests/test_*.py`
- 运行：`PYTHONPATH=src pytest tests/ -q` 或 `python -m pytest -q`

### 配置管理

- 模板：`config.example.yaml`
- 环境变量：`SHIBEI_CONFIG`、`SHIBEI_SOURCES`、`SHIBEI_LLM_API_KEY`

## 常见任务

### 添加新文件格式解析器

```python
from shibei.parsers import register_parser

def _parse_pdf(path):
    return text

register_parser(".pdf", _parse_pdf)
```

### 添加新搜索引擎

```python
from shibei.searcher import SearchEngine, register_engine

class MyEngine(SearchEngine):
    def search(self, query, limit, tag=None):
        return {"query": query, "total": 0, "results": []}

register_engine("my_engine", MyEngine)
```

### 添加新记忆存储后端

继承 `AbstractMemoryStore`，实现 `add`/`search`/`list`/`delete`。
