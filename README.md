# 🧠 Shibei

**中文语义知识库 + Agent 长期记忆 —— 给 AI Agent 用的外部大脑。**

> 扫描 Markdown/Word/Excel/TXT → 自动摘要 → 中文语义分块 → BM25 + 向量 + 混合检索 → Agent 随时搜索

## 30 秒上手

```bash
./setup.sh                      # 安装依赖 + 下载模型
vim config.yaml                 # 改 sources 为你的文档目录
shibei import                   # 增量导入
shibei search "关键词"           # 语义搜索
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

### 一句话摘要 + 混合召回

导入时自动为每个 chunk 生成一句话中文摘要，同时建立**正文索引**和**摘要索引**。搜索时两边一起召回、RRF 融合，命中率和语义覆盖度显著提升，最终返回给 LLM 的仍是原始正文。

```yaml
# config.yaml（默认已开启）
summarize:
  enabled: true
  max_length: 100
```

### 三引擎搜索

| 引擎 | 优势 | 配置 |
|------|------|------|
| **BM25** | 零依赖、速度快、可解释 | `search_engine: bm25` |
| **Vector** | 语义理解、模糊匹配 | `search_engine: vector` |
| **Hybrid** | RRF 融合 BM25 + 向量 | `search_engine: hybrid` |

```yaml
# config.yaml
chroma:
  search_engine: hybrid   # bm25 | vector | hybrid
```

### Agent 长期记忆

通过 `shibei_remember`（MCP）或 `shibei remember`（CLI）把对话中的关键事实写入长期记忆。记忆支持：

- BM25 / 向量 / Hybrid 三种召回模式（默认 hybrid）
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

每个 profile 对应 `~/.shibei/profiles/<name>.yaml`，独立的数据源、collection、标签规则。

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

### Dreaming — 知识库自主思考

扫描所有文档，发现隐藏连接和洞察：

```bash
shibei dream
```

输出：
- **跨文档连接**：哪些文档通过共享关键词关联
- **主题模式**：每个标签下的高频主题
- **洞察**：全局高频主题、孤立文档等

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

Agent 获得 5 个工具：
- `shibei_search` — 语义搜索（文档 + 记忆 + 相关推荐）
- `shibei_import` — 导入文档
- `shibei_list_sources` — 列出已入库文件
- `shibei_remember` — 写入长期记忆
- `shibei_dream` — 生成跨文档洞察

## 配置

```yaml
chroma:
  path: ~/.shibei/db
  collection: knowledge_base
  search_engine: hybrid

embedding:
  model: moka-ai/m3e-base    # 中文 768d，可换 bge-large-zh-v1.5
  device: cpu                 # cpu | mps (Mac) | cuda
  hf_endpoint: https://hf-mirror.com

sources:
  - ~/Documents/notes
  - ~/Documents/articles

chunking:
  max_chars: 800
  split_pattern: "\n(?=## )"  # 按 ## 标题分块

summarize:
  enabled: true               # 默认开启 chunk 一句话摘要
  max_length: 100             # 摘要最大字符数
  model: ""                   # 为空则使用 embedding.model

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
| `SHIBEI_MODEL` | embedding.model |
| `SHIBEI_SOURCES` | sources（逗号分隔） |
| `HF_ENDPOINT` | embedding.hf_endpoint |
| `SHIBEI_MCP_DEBUG` | MCP server 调试模式 |

## 模型选择

| 模型 | 维度 | 大小 | 适合 |
|------|------|------|------|
| `moka-ai/m3e-base` | 768 | 440MB | 快速启动（默认） |
| `BAAI/bge-large-zh-v1.5` | 1024 | 1.3GB | 精度优先 |
| `infgrad/stella-base-zh-v3-1792d` | 1792 | 400MB | 极限精度 |

换模型：改 `config.yaml` 中 `embedding.model` → `shibei import --full` 重建。

## 架构

```
CLI (shibei)
  └── Shibei (服务层)
        ├── Importer ──→ ChromaDB (向量) + BM25 (FTS5，content + summary 双索引)
        ├── Searcher ──→ 三引擎搜索 (BM25/Vector/Hybrid+RRF) + 相关推荐
        ├── MemoryStore ──→ SQLite (BM25 + 向量 hybrid 召回) + 版本链
        ├── Manifest ──→ 文件 hash 追踪 (增量导入)
        ├── Profile ──→ 多场景配置切换
        └── Server ──→ MCP JSON-RPC (Agent 接入)
```

## 测试

```bash
PYTHONPATH=src pytest tests/ -v
```

98 个测试覆盖：配置加载、标签推断、分块逻辑、BM25 摘要索引、向量记忆召回、轻量关联、增量追踪、Profile 管理。

## 依赖

- Python ≥ 3.9
- ChromaDB
- sentence-transformers
- python-docx（可选，docx 支持）
- openpyxl（可选，xlsx 支持）

## License

MIT
