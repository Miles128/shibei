# 配置参考

`config.yaml` 完整配置项说明。

## chroma

ChromaDB 存储配置。

```yaml
chroma:
  path: ~/.shibei/db              # 向量数据库路径（自动创建）
  collection: knowledge_base      # collection 名称
  search_engine: hybrid           # bm25 | vector | hybrid
```

`search_engine` 控制搜索模式：
- `bm25` — 纯关键词搜索，零依赖，最快
- `vector` — 纯语义搜索，需 embedding 模型
- `hybrid` — RRF 融合 BM25 + 向量，效果最好

## embedding

嵌入模型配置。

```yaml
embedding:
  model: moka-ai/m3e-base        # 模型名称（sentence-transformers 格式）
  device: cpu                     # cpu | mps (Mac) | cuda
  hf_endpoint: https://hf-mirror.com  # HF 镜像，海外删掉
```

环境变量覆盖：
- `SHIBEI_MODEL` — 覆盖 model
- `HF_ENDPOINT` — 覆盖 hf_endpoint

## sources

要扫描的 Markdown 文件目录（支持 `~`）。

```yaml
sources:
  - ~/Documents/notes
  - ~/Documents/projects/docs
```

环境变量覆盖：`SHIBEI_SOURCES=~/a,~/b`

## chunking

文本分块策略。

```yaml
chunking:
  max_chars: 800                 # 每块最大字符数
  split_pattern: "\n(?=## )"     # 分割正则（按 ## 标题切分）
```

## tagging

标签推断规则。按顺序匹配，检查文件路径 + 内容前 500 字。

```yaml
tagging:
  rules:
    - pattern: "(面试|复盘|interview)"
      tag: interview
    - pattern: "(AI|人工智能|大模型)"
      tag: ai
    - pattern: ".*"              # 兜底规则
      tag: personal-note
```

`pattern` 是 Python 正则（大小写不敏感）。

## CLI 参数

```bash
shibei import                  # 增量导入（跳过未变更文件）
shibei import --full           # 全量重建（忽略 manifest 缓存）
shibei search "关键词" --limit 10 --tag ai
```

## 环境变量汇总

| 变量 | 覆盖项 |
|------|--------|
| `SHIBEI_CONFIG` | 配置文件路径 |
| `SHIBEI_MODEL` | embedding.model |
| `SHIBEI_SOURCES` | sources（逗号分隔） |
| `HF_ENDPOINT` | embedding.hf_endpoint |
| `SHIBEI_MCP_DEBUG` | MCP server 调试模式 |
