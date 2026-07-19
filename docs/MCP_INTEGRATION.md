# MCP 集成指南

拾贝（Shibei）通过 MCP (Model Context Protocol) stdio JSON-RPC 暴露工具，任何 MCP 兼容的 AI Agent 都能接入。

## 多宿主强制召回契约

各宿主（Cursor / Hermes / Trae / Claude Desktop）共用同一规则，粘贴到 Rule / system / skill 即可：

```
拾贝（Shibei）MCP 契约：
- 非纯指令类用户消息 → 先调用 shibei_context(message=用户原文)，再回答
- suggest_remember.should=true 或用户披露新事实 → 调用 shibei_remember
- 用户明确要求「搜一下」时用 shibei_search；日常答前召回不用 search 替代 context
```

## 暴露的工具

| 工具名 | 参数 | 说明 |
|--------|------|------|
| `shibei_context` | `message` (必填), `limit`, `tag`, `write_hints` | **答前上下文包**（强制召回入口） |
| `shibei_search` | `query` (必填), `limit`, `tag` | BM25 搜索（明确关键词搜索时用） |
| `shibei_import` | `dirs` (可选), `full` (可选) | 导入/重建知识库 |
| `shibei_list_sources` | `limit`, `offset` | 列出已入库文件 |
| `shibei_dream` | `housekeeping`, `format`, `persist` | Dreaming — 洞察与整理候选 |
| `shibei_remember` | `fact` (必填), `category`, `tags` | 写入 Agent 对话记忆 |
| `shibei_update_memory` | `memory_id` (必填), `fact` (必填), `tags` | 更新/压缩已有记忆 |
| `shibei_consolidate` | `threshold`, `min_cluster_size`, `max_chunks`, `apply` | 相似 chunk 聚类；`apply=true` 直接合并 |
| `shibei_apply_consolidation` | `clusters` | 根据外部摘要合并相似 chunk |

### `shibei_context` 返回字段

- `query_used`：抽取/改写后的检索词
- `results` / `memories` / `related`：文档与记忆命中
- `suggest_remember`：`{should, fact, reason}`，提示是否应写入记忆（服务端不自动写）

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

重载 Hermes（或重启）即可。将上方「多宿主契约」加入 Hermes system prompt。

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

将「多宿主契约」写入 Cursor User Rule 或项目 Rule。

## 调试

```bash
# 开启 stderr 日志
shibei serve --debug

# 或设置环境变量
SHIBEI_MCP_DEBUG=1 shibei serve
```

## 工作原理

```
Agent (Claude/Hermes/Cursor/Trae)
  │
  │  MCP JSON-RPC over stdio
  │
  ▼
shibei.server
  │
  ├─ shibei_context(message)   → 抽词 → 搜索管线 → context pack
  ├─ shibei_search(query)      → BM25（+ 可选 LLM 改写 / 同义词）
  ├─ shibei_import()           → 扫描 → 分块 → 增量导入
  ├─ shibei_list_sources()     → 列出已入库文件
  ├─ shibei_remember(fact)     → 写入长期记忆
  ├─ shibei_update_memory(...) → 更新/压缩已有记忆
  ├─ shibei_consolidate()      → 相似 chunk 聚类
  ├─ shibei_apply_consolidation() → 外部摘要合并
  └─ shibei_dream()            → 洞察 + 整理候选
```

## Dreaming 工作流

`shibei_dream` 以 **skill** 形式工作，本身不调用 LLM，只输出建议：

1. **清理过期记忆**：自动删除已到期的记忆。
2. **识别可压缩记忆候选**：返回 `compressible_candidates`；Agent 判断后调用 `shibei_update_memory`。
3. **识别相似 chunk 候选**：返回 `consolidation.clusters`；Agent 生成摘要后调用 `shibei_apply_consolidation`。

这种设计让拾贝核心路径保持零 LLM 依赖，同时让挂载它的 Agent 完全掌控整理决策。
