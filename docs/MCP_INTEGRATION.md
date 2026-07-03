# MCP 集成指南

Shibei 通过 MCP (Model Context Protocol) stdio JSON-RPC 暴露工具，任何 MCP 兼容的 AI Agent 都能接入。

## 暴露的工具

| 工具名 | 参数 | 说明 |
|--------|------|------|
| `shibei_search` | `query` (必填), `limit`, `tag` | BM25 搜索 |
| `shibei_import` | `dirs` (可选), `full` (可选) | 导入/重建知识库 |
| `shibei_list_sources` | 无 | 列出已入库文件 |
| `shibei_dream` | `housekeeping` | 知识库 dreaming — 输出洞察与整理候选 |
| `shibei_remember` | `fact` (必填), `category`, `tags` | 写入 Agent 对话记忆 |
| `shibei_update_memory` | `memory_id` (必填), `fact` (必填), `tags` | 更新/压缩已有记忆 |
| `shibei_consolidate` | `threshold`, `min_cluster_size`, `max_chunks`, `apply` | 发现相似 chunk 聚类；`apply=true` 直接启发式合并 |
| `shibei_apply_consolidation` | `clusters` | 根据外部 Agent 提供的摘要直接合并相似 chunk（零 LLM 依赖） |

## 接入 Hermes

编辑 `~/.hermes/config.yaml`：

```yaml
mcp_servers:
  shibei:
    enabled: true
    command: python3
    args:
      - -m
      - shibei.server
    env:
      SHIBEI_CONFIG: /Users/you/shibei/config.yaml
```

重载 Hermes（或重启）即可。

## 接入 Claude Desktop

编辑 Claude Desktop 配置（`~/Library/Application Support/Claude/claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "shibei": {
      "command": "python3",
      "args": ["-m", "shibei.server"],
      "env": {
        "SHIBEI_CONFIG": "/Users/you/shibei/config.yaml"
      }
    }
  }
}
```

## 接入 Cursor

Cursor 支持 MCP via `.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "shibei": {
      "command": "python3",
      "args": ["-m", "shibei.server"],
      "env": {
        "SHIBEI_CONFIG": "/Users/you/shibei/config.yaml"
      }
    }
  }
}
```

## 调试

```bash
# 开启 stderr 日志
shibei serve --debug

# 或设置环境变量
SHIBEI_MCP_DEBUG=1 shibei serve
```

## 工作原理

```
Agent (Claude/Hermes/Cursor)
  │
  │  MCP JSON-RPC over stdio
  │
  ▼
shibei.server
  │
  ├─ shibei_search(query)      → BM25 搜索 → 返回排序结果
  ├─ shibei_import()           → 扫描 MD → 分块 → 增量导入 → 存入
  ├─ shibei_list_sources()     → 列出已入库文件
  ├─ shibei_remember(fact)     → 写入长期记忆
  ├─ shibei_update_memory(...) → 更新/压缩已有记忆
  ├─ shibei_consolidate()         → 发现相似 chunk 聚类（零 LLM 依赖）
├─ shibei_apply_consolidation() → 根据外部摘要直接合并相似 chunk
└─ shibei_dream()               → 洞察 + 整理候选（零 LLM 依赖）
```

## Dreaming 工作流

`shibei_dream` 以 **skill** 形式工作，本身不调用 LLM，只输出建议：

1. **清理过期记忆**：自动删除已到期的记忆。
2. **识别可压缩记忆候选**：返回 `compressible_candidates`，每条包含 `id`、`fact`、`created_at`。Agent 可用自己的 LLM 判断哪些是短期记忆，然后调用 `shibei_update_memory` 压缩。
3. **识别相似 chunk 候选**：返回 `consolidation.clusters`，每个 cluster 包含重叠的 chunk 原文和 `_rowid`。Agent 可用自己的 LLM 生成合并摘要，然后调用 `shibei_apply_consolidation` 写回知识库。

这种设计让 Shibei 保持零 LLM 依赖，同时让挂载它的 Agent 完全掌控整理决策。
