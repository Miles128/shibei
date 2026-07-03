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

标签推断支持两种规则，按顺序匹配：

1. **路径规则（推荐）**：按目录结构打标签，零维护、最稳定。
2. **regex 规则（兼容）**：检查文件路径 + 内容前 500 字。

```yaml
tagging:
  path_rules:
    - pattern: "面试/HSBC/*.md"   # 支持 source-root-relative glob
      tags: "interview,hsbc"
    - pattern: "面试/*.md"
      tags: "interview"
  rules:
    - pattern: "(AI|人工智能|大模型)"
      tag: ai
    - pattern: ".*"              # 兜底规则
      tag: personal-note
```

路径规则 `pattern` 为 glob，会匹配路径的任意后缀（如 `面试/HSBC/*.md` 可匹配 `/notes/面试/HSBC/prep.md` 中的 `面试/HSBC/prep.md`）。`tags` 为逗号分隔的多个标签。

regex 规则的 `pattern` 是 Python 正则（大小写不敏感）。

## CLI 参数

```bash
shibei import                  # 增量导入（跳过未变更文件）
shibei import --full           # 全量重建（忽略 manifest 缓存）
shibei import --dirs ~/a,~/b   # 临时指定扫描目录
shibei search "关键词" --limit 10 --tag ai
shibei clean                   # 清理源文件已删除的 orphan chunks
shibei clean --dry-run         # 只报告不删除
shibei consolidate --apply     # 发现相似 chunk 并直接合并
```

## 环境变量汇总

| 变量 | 覆盖项 |
|------|--------|
| `SHIBEI_CONFIG` | 配置文件路径 |
| `SHIBEI_SOURCES` | sources（逗号分隔） |
| `SHIBEI_MCP_DEBUG` | MCP server 调试模式 |
