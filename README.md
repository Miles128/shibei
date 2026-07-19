# 拾贝（Shibei）

**中文轻量知识库 + Agent 长期记忆 —— 给 AI Agent 用的外部大脑。**

> 扫描 Markdown/Word/Excel/TXT → 中文语义分块 → BM25 全文检索 + 近义词/LLM 改写 → Agent 答前强制召回

## 30 秒上手

```bash
./setup.sh                      # 安装依赖
vim config.yaml                 # 改 sources 为你的文档目录
shibei import                   # 增量导入
shibei search "关键词"           # 搜索
shibei serve                    # 启动 MCP server
```

## 三层接口

| 接口 | 命令 | 谁用 |
|------|------|------|
| CLI | `shibei context/search/import/list/remember` | 人 |
| MCP | `shibei serve` | AI Agent |
| Python | `from shibei import Shibei` | 开发者 |

## 核心特性

### 多格式文档导入

监控指定文件夹，自动解析并导入：

| 格式 | 说明 | 依赖 |
|------|------|------|
| `.md` | Markdown | 内置 |
| `.txt` | 纯文本 | 内置 |
| `.docx` | Word 文档 | python-docx |
| `.xlsx` | Excel 表格 | openpyxl |
| `.csv` | CSV 数据 | 内置 |

```yaml
# config.yaml
extensions:
  - .md
  - .txt
  - .docx
  - .xlsx
  - .csv
```

### BM25 全文搜索

基于 SQLite FTS5，零外部依赖、速度快、可解释。

```yaml
# config.yaml
storage:
  path: ~/.shibei/db
```

### 自动摘要（可选 LLM）

导入时为每个 chunk 自动生成一句话中文摘要，摘要内容会建立独立的 FTS5 索引，搜索时与正文 RRF 融合召回：

```bash
export SHIBEI_LLM_API_KEY="sk-..."
shibei import
```

```yaml
llm:
  enabled: true
  base_url: "https://api.openai.com/v1"
  model: "gpt-4o-mini"
  max_content_length: 1000   # 输入 chunk 最大字符数
  max_summary_length: 50     # 期望摘要最大字数
```

- API key 仅通过 `SHIBEI_LLM_API_KEY` 环境变量读取，不写入配置文件
- 摘要生成失败时自动降级为空字符串，不会阻断导入
- 默认关闭，不影响零 LLM 依赖的搜索体验

### 答前上下文（多宿主强制召回）

Agent 不应靠「想起来再搜」。挂载 MCP 后，非纯指令类用户消息应先调 `shibei_context`：

```bash
shibei context "讲讲我面试里的 Redis"
```

返回文档命中、相关记忆，以及 `suggest_remember`（是否建议写入记忆）。契约详见 [docs/MCP_INTEGRATION.md](docs/MCP_INTEGRATION.md) 与 [agent.md](agent.md)。

### 查询改写与扩展

搜索管线：`规则改写 → LLM 关键词改写（可选）→ 同义词扩展 → BM25`。

```yaml
rewriter:
  enabled: true
  use_llm: true            # 需 SHIBEI_LLM_API_KEY；失败自动降级
  max_terms: 8
expander:
  enabled: true
  synonyms_path: ./data/synonyms.yaml
  fuzzy: false
```

```bash
shibei search "大模型" --expand          # 同义词扩展
shibei search "讲讲 Redis" --llm-rewrite # LLM 改写（需 API key）
```

同义词表示例（`data/synonyms.yaml`）：

```yaml
AI: [人工智能, 大模型, 机器学习]
金融: [银行, 风控, finance]
面试: [面经, interview, 求职]
```

### Agent 长期记忆

通过 `shibei_remember`（MCP）或 `shibei remember`（CLI）把对话中的关键事实写入长期记忆。记忆支持：

- BM25 全文召回
- 版本链：新事实自动替代语义相似旧事实，历史可追溯
- 与文档库融合搜索：一次搜索同时召回文档 chunk 和相关记忆

```bash
shibei remember "用户偏好 Python 而不是 Go" --category preference --tags tech
shibei memories list
shibei search "后端技术选型"   # 同时召回文档和记忆
```

### 轻量关联

不依赖图数据库，自动发现文档之间的关联：

- **标签共现**：共享标签的文档互相推荐
- **Markdown 链接**：解析 `[[...]]` 和 `[...](path)` 内链
- **关键词共现**：基于 BM25 高频词计算文档相似度

搜索结果中自动附带 `related` 字段，Agent 可见“还有哪些相关文档”。

### 增量导入

通过文件 hash 追踪变更，跳过未修改的文档。支持配置文件中的 `sources`，也支持命令行直接指定路径：

```bash
shibei import                           # 使用 config.yaml 中的 sources
shibei import ~/notes ~/articles        # 临时指定导入路径
shibei import --dirs ~/notes,~/articles # 逗号分隔写法（与位置参数二选一）
shibei import --full                    # 全量重建：忽略缓存，重新导入所有
```

### Profile 多场景切换

```bash
shibei profile create interview    # 面试知识库
shibei profile create notes        # 个人笔记
shibei profile list                # 查看所有

shibei --profile interview search "银行风控"   # 搜面试库
shibei --profile notes     search "日记"       # 搜笔记库
```

每个 profile 对应 `~/.shibei/profiles/<name>.yaml`，独立的数据源、标签规则。

### 自动标签推断

基于正则规则，扫描文件路径 + 内容前 500 字自动打标签：

```yaml
tagging:
  rules:
    - pattern: "(面试|复盘)"
      tag: interview
    - pattern: "(AI|大模型)"
      tag: ai
    - pattern: ".*"            # 兜底
      tag: personal-note
```

搜索时支持标签过滤：`shibei search "风控" --tag finance`

### Dreaming — 知识库洞察与整理建议

扫描所有文档，发现隐藏连接和洞察，并以 **skill** 形式输出整理建议（零 LLM 依赖）：

```bash
shibei dream
```

输出：
- **跨文档连接**：哪些文档通过共享关键词关联
- **主题模式**：每个标签下的高频主题
- **洞察**：全局高频主题、孤立文档等
- **整理建议**：
  - 清理过期记忆（自动执行）
  - 识别可压缩的旧记忆候选（由 Agent 用 LLM 判断后执行）
  - 识别相似的 chunk 候选（由 Agent 用 LLM 生成摘要后执行）

## MCP 接入（给 Agent）

任何支持 MCP 的 Agent（Claude Desktop、Cursor、Trae、Hermes）都能接：

```yaml
mcp_servers:
  shibei:
    enabled: true
    command: python3
    args:
      - -m
      - shibei.server
    env:
      SHIBEI_CONFIG: /path/to/your/config.yaml
```

Agent 获得 9 个工具：
- `shibei_context` — **答前上下文包**（多宿主强制召回入口）
- `shibei_search` — 语义搜索（文档 + 记忆 + 相关推荐）
- `shibei_import` — 导入文档
- `shibei_list_sources` — 列出已入库文件
- `shibei_remember` — 写入长期记忆
- `shibei_update_memory` — 更新/压缩已有记忆
- `shibei_consolidate` — 发现相似 chunk 聚类；设置 `apply=true` 可直接合并（零 LLM 依赖）
- `shibei_apply_consolidation` — 根据外部 Agent 提供的摘要合并相似 chunk
- `shibei_dream` — 生成跨文档洞察与整理建议（skill 形式）

## 配置

```yaml
storage:
  path: ~/.shibei/db

sources:
  - ~/Documents/notes
  - ~/Documents/articles

extensions:
  - .md
  - .txt
  - .docx
  - .xlsx
  - .csv

chunking:
  max_chars: 800
  split_pattern: "\n(?=## )"  # 按 ## 标题分块

tagging:
  path_rules:
    - pattern: "面试/HSBC/*.md"
      tags: "interview,hsbc"
  rules:
    - pattern: "(AI|大模型)"
      tag: ai

expander:
  enabled: true
  synonyms_path: ./data/synonyms.yaml
  fuzzy: false
```

完整配置说明见 [docs/CONFIG.md](docs/CONFIG.md)。

### 环境变量

| 变量 | 覆盖项 |
|------|--------|
| `SHIBEI_CONFIG` | 配置文件路径 |
| `SHIBEI_SOURCES` | sources（逗号分隔） |
| `SHIBEI_LLM_API_KEY` | LLM API key（仅用于摘要生成） |
| `SHIBEI_MCP_DEBUG` | MCP server 调试模式 |

## 架构

```
CLI (shibei)
  └── Shibei (服务层)
        ├── Context ──→ 答前抽词 + 搜索管线 → context pack
        ├── Importer ──→ BM25 (FTS5)
        ├── Searcher ──→ 规则/LLM 改写 → 扩展 → BM25 + 相关推荐
        ├── Expander ──→ 同义词表 + 模糊匹配
        ├── Summarizer ──→ LLM 一句话摘要（可选）
        ├── MemoryStore ──→ SQLite (BM25 召回) + 版本链
        ├── Manifest ──→ 文件 hash 追踪 (增量导入)
        ├── Profile ──→ 多场景配置切换
        ├── Dreamer ──→ 跨文档洞察 + 整理建议（skill 形式）
        └── Server ──→ MCP JSON-RPC (Agent 接入)
```

## 测试

```bash
PYTHONPATH=src pytest tests/ -v
```

测试覆盖：配置加载、标签推断、分块逻辑、BM25 索引、记忆召回、轻量关联、增量追踪、Profile 管理、Dreaming 洞察与整理建议。

## 依赖

- Python ≥ 3.9
- pyyaml
- python-docx（可选，docx 支持）
- openpyxl（可选，xlsx 支持）
- jieba（可选，中文分词增强）

## License

MIT
