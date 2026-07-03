# Agent Guide — Shibei 开发指南

> 本文件为 AI Agent 提供项目开发上下文。Agent 在修改本项目前应先阅读此文件。

## MCP 使用规则（必读）

当 Shibei 作为 MCP server 挂载到当前 Agent 时，Agent 应遵循以下规则主动调用 Shibei 工具：

### 必须调用 `shibei_search` 的场景

当用户消息涉及以下内容时，Agent **必须**先调用 `shibei_search`，再基于搜索结果回答：

1. **用户的个人经历/做过的事**："我之前做过..."、"我负责过..."、"我们项目里..."、"我开发过..."
2. **用户的个人情况/偏好/习惯/目标**："我喜欢..."、"我习惯..."、"我计划..."、"我的目标是..."
3. **用户自己的知识库内容**："我笔记里有..."、"我好像写过..."、"我整理过..."
4. **需要个人历史信息才能回答的问题**：例如基于用户过往项目给出建议、推荐适合用户的方案等。

### 必须调用 `shibei_remember` 的场景

当用户首次披露以下信息时，Agent **应主动调用** `shibei_remember` 写入长期记忆：

1. 个人偏好、习惯、目标
2. 项目背景、工作经历、技术栈
3. 关键决策、重要结论
4. 任何后续对话可能复用的新事实

### 工具组合顺序

- 先 `shibei_search` 召回已有信息
- 回答中确认/补充后，如有新事实再用 `shibei_remember` 写入
- 不确定是否已存在时，先搜索，避免重复写入

## 项目定位

Shibei（十倍）是面向 AI Agent 的中文语义知识库，定位为 Agent 的"外部大脑"。核心能力：

- **文档导入**：扫描目录 → 分块 → 去重 → 存入 BM25 索引
- **全文搜索**：基于 SQLite FTS5 的 BM25 搜索引擎
- **对话记忆**：Agent 对话事实的持久化存储与版本链
- **Dreaming**：知识库自主思考，发现跨文档隐藏连接，并以 skill 形式输出整理建议（可压缩记忆候选、相似 chunk 候选），由外部 Agent 用 LLM 判断后执行
- **MCP 协议**：7 个 MCP 工具供 AI Agent 直接调用：search、import、list_sources、remember、update_memory、consolidate/apply_consolidation、dream

## 技术栈

| 层 | 技术 |
|---|---|
| 存储 | SQLite FTS5（BM25）|
| 分词 | jieba（优先）+ bigram 降级 |
| LLM | OpenAI-compatible API（urllib，零第三方 HTTP 依赖）|
| 协议 | MCP (Model Context Protocol) JSON-RPC over stdio |
| Python | >=3.9 |

## 项目结构

```
src/shibei/
├── __init__.py          # 包入口
├── __main__.py          # python -m shibei 入口
├── cli.py               # CLI 命令（import/search/dream/remember/mcp/...）
├── config.py            # YAML 配置加载 + 环境变量覆盖
├── services.py          # Shibei 服务层（统一 API 入口）
├── importer.py          # 文档导入器（扫描→分块→去重→存储）
├── searcher.py          # 搜索引擎（BM25）
├── bm25.py              # BM25 索引（SQLite FTS5）
├── memory.py            # 对话记忆存储（AbstractMemoryStore + SQLite 实现）
├── dreamer.py           # Dreaming 模块（知识库自主思考 + 自动整理）
├── consolidator.py      # 相似 chunk 聚类合并
├── chunker.py           # 文本分块（parent-child 模式）
├── tokenizer.py         # 中文分词公共模块（jieba/bigram）
├── parsers.py           # 文件解析器（插件化注册机制）
├── tagger.py            # 文件标签推断
├── llm.py               # LLM 客户端（OpenAI-compatible）
├── manifest.py          # 导入清单（文件 hash 追踪）
├── dedup.py             # chunk 去重（content hash）
├── profile.py           # 多知识库 Profile 管理
└── server.py            # MCP Server（JSON-RPC over stdio）

tests/                   # 测试用例
docs/                    # PRD、竞品对比、配置、快速开始、MCP 集成
```

## 开发规范

### 代码风格

- **类型注解**：所有公开函数必须有参数和返回值类型注解
- **路径**：使用 `pathlib.Path` 而非字符串拼接
- **日志**：核心库使用 `logging.getLogger(__name__)`，CLI 层可用 `print()`
- **异常**：禁止 bare `except Exception: pass`，必须捕获具体异常类型并记录日志
- **中文**：代码注释和文档使用中文，变量名和函数名使用英文

### 架构原则

1. **零 LLM 依赖**：核心功能（导入、搜索、记忆）不依赖 LLM API，LLM 仅作可选增强
2. **轻量优先**：仅使用 SQLite FTS5，不依赖向量数据库或本地 embedding 模型
3. **插件化**：文件解析器通过 `register_parser()` 注册，支持自定义扩展
4. **抽象层**：`AbstractMemoryStore` 定义记忆存储接口，支持未来切换到 PostgreSQL
5. **公共模块**：分词逻辑统一在 `tokenizer.py`，避免重复代码

### 安全规范

- **API Key**：从环境变量 `SHIBEI_LLM_API_KEY` 读取，不写入配置文件
- **config.yaml**：已加入 `.gitignore`，不提交到 Git
- **SQLite**：所有连接设置 `timeout=30.0`，防止并发锁死
- **输入验证**：`fact` 不能为空，`search` 查询有长度限制

### 测试规范

- 测试文件放在 `tests/` 目录，命名 `test_*.py`
- 运行测试：`python -m pytest -q`
- 新增功能必须附带测试

### 配置管理

- 配置文件：`config.yaml`（不提交），模板：`config.example.yaml`
- 环境变量覆盖：`SHIBEI_CONFIG`、`SHIBEI_SOURCES`、`SHIBEI_LLM_API_KEY`

## 常见任务

### 添加新文件格式解析器

```python
from shibei.parsers import register_parser

def _parse_pdf(path):
    # 实现解析逻辑
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

继承 `AbstractMemoryStore`，实现 `add`/`search`/`list`/`delete` 方法。
