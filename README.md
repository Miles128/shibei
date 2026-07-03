# 🧠 Shibei

**中文轻量知识库 + Agent 长期记忆 —— 给 AI Agent 用的外部大脑。**

> 扫描 Markdown/Word/Excel/TXT → 自动摘要 → 中文语义分块 → BM25 全文检索 → Agent 随时搜索

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
| CLI | `shibei search/import/list/remember` | 人 |
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

### 一句话摘要 + BM25 召回

导入时自动为每个 chunk 生成一句话中文摘要，同时建立**正文索引**和**摘要索引**。搜索时两边一起召回，命中率和语义覆盖度显著提升，最终返回给 LLM 的仍是原始正文。

```yaml
# config.yaml（默认已开启）
summarize:
  enabled: true
  max_length: 100
```

### BM25 全文搜索

基于 SQLite FTS5，零外部依赖、速度快、可解释。

```yaml
# config.yaml
storage:
  path: ~/.shibei/db
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

通过文件 hash 追踪变更，跳过未修改的文档：

```bash
shibei import                # 增量：只导入新增/修改的文件
shibei import --full         # 全量重建：忽略缓存，重新导入所有
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
shibei dream run
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

Agent 获得 7 个工具：
- `shibei_search` — 语义搜索（文档 + 记忆 + 相关推荐）
- `shibei_import` — 导入文档
- `shibei_list_sources` — 列出已入库文件
- `shibei_remember` — 写入长期记忆
- `shibei_update_memory` — 更新/压缩已有记忆
- `shibei_consolidate` / `shibei_apply_consolidation` — 发现/合并相似 chunk（后者支持外部 Agent 提供摘要，零 LLM 依赖）
- `shibei_dream` — 生成跨文档洞察与整理建议（skill 形式）

## 配置

```yaml
storage:
  path: ~/.shibei/db

sources:
  - ~/Documents/notes
  - ~/Documents/articles

chunking:
  max_chars: 800
  split_pattern: "\n(?=## )"  # 按 ## 标题分块

summarize:
  enabled: true               # 默认开启 chunk 一句话摘要
  max_length: 100             # 摘要最大字符数

tagging:
  rules:
    - pattern: "(面试|复盘)"
      tag: interview
    - pattern: "(AI|大模型)"
      tag: ai
```

完整配置说明见 [docs/CONFIG.md](docs/CONFIG.md)。

### 环境变量

| 变量 | 覆盖项 |
|------|--------|
| `SHIBEI_CONFIG` | 配置文件路径 |
| `SHIBEI_SOURCES` | sources（逗号分隔） |
| `SHIBEI_LLM_API_KEY` | llm.api_key |
| `SHIBEI_MCP_DEBUG` | MCP server 调试模式 |

## 架构

```
CLI (shibei)
  └── Shibei (服务层)
        ├── Importer ──→ BM25 (FTS5，content + summary 双索引)
        ├── Searcher ──→ BM25 搜索 + 相关推荐
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

测试覆盖：配置加载、标签推断、分块逻辑、BM25 摘要索引、记忆召回、轻量关联、增量追踪、Profile 管理、Dreaming 洞察与整理建议。

## 依赖

- Python ≥ 3.9
- pyyaml
- python-docx（可选，docx 支持）
- openpyxl（可选，xlsx 支持）
- jieba（可选，中文分词增强）

## License

MIT
