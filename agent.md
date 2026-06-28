# Agent Guide — Shibei 开发指南

> 本文件为 AI Agent 提供项目开发上下文。Agent 在修改本项目前应先阅读此文件。

## 项目定位

Shibei（十倍）是面向 AI Agent 的中文语义知识库，定位为 Agent 的"外部大脑"。核心能力：

- **文档导入**：扫描目录 → 分块 → 去重 → 存入 BM25/向量索引
- **语义搜索**：BM25 / 向量 / 混合（RRF 融合）三种引擎
- **对话记忆**：Agent 对话事实的持久化存储与版本链
- **Dreaming**：知识库自主思考，发现跨文档隐藏连接
- **MCP 协议**：5 个 MCP 工具供 AI Agent 直接调用

## 技术栈

| 层 | 技术 |
|---|---|
| 存储 | SQLite FTS5（BM25）+ ChromaDB（向量）|
| 分词 | jieba（优先）+ bigram 降级 |
| LLM | OpenAI-compatible API（urllib，零第三方 HTTP 依赖）|
| 协议 | MCP (Model Context Protocol) JSON-RPC over stdio |
| Python | >=3.11 |

## 项目结构

```
src/shibei/
├── __init__.py          # 包入口
├── __main__.py          # python -m shibei 入口
├── cli.py               # CLI 命令（import/search/dream/remember/mcp/...）
├── config.py            # YAML 配置加载 + 环境变量覆盖
├── services.py          # Shibei 服务层（统一 API 入口）
├── importer.py          # 文档导入器（扫描→分块→去重→存储）
├── searcher.py          # 搜索引擎（策略模式：BM25/Vector/Hybrid）
├── bm25.py              # BM25 索引（SQLite FTS5）
├── memory.py            # 对话记忆存储（AbstractMemoryStore + SQLite 实现）
├── dreamer.py           # Dreaming 模块（知识库自主思考）
├── chunker.py           # 文本分块（parent-child 模式）
├── tokenizer.py         # 中文分词公共模块（jieba/bigram）
├── parsers.py           # 文件解析器（插件化注册机制）
├── tagger.py            # 文件标签推断
├── embedding.py         # 嵌入模型管理（sentence-transformers）
├── llm.py               # LLM 客户端（OpenAI-compatible）
├── manifest.py          # 导入清单（文件 hash 追踪）
├── dedup.py             # chunk 去重（content hash）
├── profile.py           # 多知识库 Profile 管理
└── server.py            # MCP Server（JSON-RPC over stdio）

tests/                   # 93 个测试用例
docs/                    # PRD、竞品对比、配置、快速开始、MCP 集成
.specify/                # Spec-Kit 配置（spec-driven development）
.claude/skills/          # Claude Agent skills（spec-kit 安装）
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
2. **策略模式**：搜索引擎通过 `SearchEngine` 抽象基类 + `register_engine()` 注册
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
- 当前覆盖率：93 个测试，全部通过
- 新增功能必须附带测试

### 配置管理

- 配置文件：`config.yaml`（不提交），模板：`config.example.yaml`
- 环境变量覆盖：`SHIBEI_CONFIG`、`SHIBEI_MODEL`、`SHIBEI_SOURCES`、`SHIBEI_LLM_API_KEY`、`HF_ENDPOINT`

## Spec-Kit 工作流

本项目已安装 GitHub Spec-Kit，支持 spec-driven development：

1. `/speckit.constitution` — 建立项目原则
2. `/speckit.specify` — 创建规格说明（spec.md）
3. `/speckit.plan` — 创建技术实现计划
4. `/speckit.tasks` — 生成任务列表
5. `/speckit.implement` — 执行实现

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
