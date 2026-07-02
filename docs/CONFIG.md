# 配置参考

`config.yaml` 完整配置项说明。

## storage

数据存储配置。

```yaml
storage:
  path: ~/.shibei/db              # SQLite 数据路径（自动创建）
```

Shibei 使用 SQLite + FTS5 实现 BM25 全文搜索，无需外部数据库或向量服务。

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
  overlap: 0                     # 相邻 chunk 重叠字符数
  parent_window: 0               # parent-child 模式（0=禁用）
  sentence_boundaries: "。！？!?\n"  # 句子边界字符
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

## summarize

导入时为每个 chunk 生成一句话摘要，增强召回。

```yaml
summarize:
  enabled: true                  # 默认开启
  max_length: 100                # 摘要最大字符数
  model: ""                      # 为空则使用 llm.model
```

## llm

LLM 增强（可选，默认关闭）。开启后支持查询改写、事实提取、 dreaming 洞察增强、记忆生命周期判断、相似 chunk 合并摘要。

```yaml
llm:
  enabled: false
  provider: openai-compatible
  model: deepseek-chat
  base_url: https://api.deepseek.com/v1
  api_key: ""                    # 必填
  temperature: 0.3
  max_tokens: 1024
  timeout: 30.0
```

环境变量覆盖：`SHIBEI_LLM_API_KEY`

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
| `SHIBEI_SOURCES` | sources（逗号分隔） |
| `SHIBEI_LLM_API_KEY` | llm.api_key |
| `SHIBEI_MCP_DEBUG` | MCP server 调试模式 |
