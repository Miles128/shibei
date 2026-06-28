# MCP 集成指南

Shibei 通过 MCP (Model Context Protocol) stdio JSON-RPC 暴露工具，任何 MCP 兼容的 AI Agent 都能接入。

## 暴露的工具

| 工具名 | 参数 | 说明 |
|--------|------|------|
| `shibei_search` | `query` (必填), `limit`, `tag` | 语义搜索 |
| `shibei_import` | `dirs` (可选), `full` (可选) | 导入/重建知识库 |
| `shibei_list_sources` | 无 | 列出已入库文件 |
| `shibei_dream` | 无 | 知识库 dreaming — 发现隐藏连接与洞察 |

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
  ├─ shibei_search(query)   → BM25/向量/Hybrid 搜索 → 返回排序结果
  ├─ shibei_import()        → 扫描 MD → 分块 → 增量导入 → 存入
  └─ shibei_list_sources()  → 列出已入库文件
```
