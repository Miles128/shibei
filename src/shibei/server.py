"""MCP JSON-RPC stdio server — 任何 MCP 兼容 Agent 可接入。

暴露五个工具：
  - shibei_search      : 语义搜索
  - shibei_import      : 导入文档
  - shibei_list_sources: 列出已入库文件
  - shibei_dream       : 知识库洞察
  - shibei_remember    : 写入 Agent 对话记忆

接入方式（以 Hermes config.yaml 为例）：
  mcp_servers:
    shibei:
      enabled: true
      command: python3
      args:
        - -m
        - shibei.server
"""

import json
import sys
import os

from .config import load_config
from .services import Shibei


def _init_brain():
    config_path = os.environ.get("SHIBEI_CONFIG", "config.yaml")
    return Shibei(config_path)


def _handle_search(brain, args):
    return brain.search(
        query=args.get("query", ""),
        limit=args.get("limit", 5),
        tag=args.get("tag"),
    )


def _handle_import(brain, args):
    dirs = args.get("dirs")
    full = args.get("full", False)
    file_pattern = args.get("file_pattern")
    return brain.import_all(dirs, full=full, file_pattern=file_pattern)


def _handle_list_sources(brain, args):
    return brain.list_sources(
        limit=args.get("limit", 0),
        offset=args.get("offset", 0),
    )


def _handle_dream(brain, args):
    return brain.dream(
        format=args.get("format", "brief"),
        persist=args.get("persist", True),
        housekeeping=args.get("housekeeping"),
    )


def _handle_consolidate(brain, args):
    return brain.consolidate(
        threshold=args.get("threshold", 0.6),
        min_cluster_size=args.get("min_cluster_size", 2),
        max_chunks=args.get("max_chunks", 500),
        apply=args.get("apply", False),
    )


def _handle_remember(brain, args):
    return brain.remember(
        fact=args.get("fact", ""),
        category=args.get("category", "user"),
        source=args.get("source", ""),
        tags=args.get("tags", ""),
        expires_at=args.get("expires_at"),
    )


TOOLS = {
    "shibei_search": {
        "handler": _handle_search,
        "description": "语义搜索个人知识库。当用户提到以下任一情况时，必须优先调用本工具：\n1. 用户做过的事、经历、工作成果、项目背景（如'我之前做过...'、'我负责过...'、'我们项目里...'）\n2. 用户的个人情况、偏好、习惯、目标（如'我喜欢...'、'我计划...'、'我的习惯是...'）\n3. 用户询问自己之前整理/记录/收藏过的内容（如'我笔记里有...'、'我好像写过...'）\n4. 需要调用个人历史信息才能准确回答的问题。搜索 query 应提取用户问题中的核心实体和关键词，必要时去掉人称词。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "limit": {"type": "integer", "default": 5, "description": "返回条数上限"},
                "tag": {"type": "string", "description": "标签过滤（如 interview/ai/finance）"},
            },
            "required": ["query"],
        },
    },
    "shibei_import": {
        "handler": _handle_import,
        "description": "从源目录导入文件到知识库（增量，跳过未变更文件）。支持文件名 glob 过滤",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dirs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要扫描的目录列表（可选，默认用 config 中的 sources）",
                },
                "full": {
                    "type": "boolean",
                    "default": False,
                    "description": "全量重建（忽略增量缓存，重新导入所有文件）",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "文件名 glob 过滤（如 'README*' 只导入 README 文件，'*.md' 匹配所有 md），不填则不过滤",
                },
            },
        },
    },
    "shibei_list_sources": {
        "handler": _handle_list_sources,
        "description": "列出已入库的文件（支持分页 + 聚合摘要：文件数、chunk数、标签分布）。适合快速了解知识库规模，避免大量文件列表撑满上下文。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "default": 0,
                    "description": "每页返回文件数（0=不分页，返回全部；建议首次调用用 0 看摘要，后续用 20-50 翻页）",
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "description": "分页偏移",
                },
            },
        },
    },
    "shibei_dream": {
        "handler": _handle_dream,
        "description": "知识库 dreaming — 发现隐藏连接、跨文档关联、主题模式和洞察，并自动执行整理：清理过期记忆、压缩旧短期记忆、合并相似 chunk。默认精简输出（stats+insights+housekeeping统计），需要完整 cross_references 时用 full 格式。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["brief", "full"],
                    "default": "brief",
                    "description": "brief=精简（推荐 MCP，只返回 stats+insights+housekeeping统计，省 token）；full=完整（返回 cross_references + patterns + 详细 clusters）",
                },
                "persist": {
                    "type": "boolean",
                    "default": True,
                    "description": "是否持久化结果，用于后续 diff 对比",
                },
                "housekeeping": {
                    "type": ["boolean", "object"],
                    "default": True,
                    "description": "是否执行自动整理。false=禁用；true=使用默认配置；object=自定义配置（如 {\"compress_max_age_days\": 30, \"consolidate_threshold\": 0.6}）",
                },
            },
        },
    },
    "shibei_consolidate": {
        "handler": _handle_consolidate,
        "description": "整理知识库：发现内容高度重叠的 chunk，聚类后生成精炼摘要。可选 apply 模式，直接删除原重叠 chunks 并把合并摘要写回知识库。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "number",
                    "default": 0.6,
                    "description": "Overlap 相似度阈值（0~1），越高要求越严格",
                },
                "min_cluster_size": {
                    "type": "integer",
                    "default": 2,
                    "description": "最小组大小",
                },
                "max_chunks": {
                    "type": "integer",
                    "default": 500,
                    "description": "最大扫描 chunk 数",
                },
                "apply": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否将合并摘要写回知识库并删除原重叠 chunks（谨慎使用）",
                },
            },
        },
    },
    "shibei_remember": {
        "handler": _handle_remember,
        "description": "将对话或交互中的重要事实写入 Shibei 长期记忆。Agent 必须在以下场景主动调用：\n1. 用户提到自己的个人情况、偏好、习惯、目标（如'我喜欢...'、'我习惯...'、'我计划...'）\n2. 用户描述自己做过的项目、工作成果、经历（如'我开发过...'、'我负责过...'、'我们之前...'）\n3. 用户明确的关键决策、重要结论、约定（如'我决定...'、'请记住...'）\n4. 任何你认为后续回答用户问题时可能用得上、且不在知识库中的事实。\n写入的记忆会在后续 shibei_search 中自动召回。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "要记忆的事实，建议用简洁的第三人称陈述句",
                },
                "category": {
                    "type": "string",
                    "enum": ["user", "agent", "project", "preference"],
                    "default": "user",
                    "description": "记忆分类",
                },
                "source": {
                    "type": "string",
                    "description": "来源会话ID或工具调用ID，便于追溯",
                },
                "tags": {
                    "type": "string",
                    "description": "逗号分隔标签，如 '面试,Redis'",
                },
                "expires_at": {
                    "type": "string",
                    "description": "过期时间 ISO 8601（可选）",
                },
            },
            "required": ["fact"],
        },
    },
}


def main():
    brain = _init_brain()

    for line in sys.stdin:
        try:
            req = json.loads(line.strip())
        except json.JSONDecodeError:
            continue

        method = req.get("method", "")
        req_id = req.get("id")

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "shibei", "version": "0.1.0"},
                },
            }
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            tools_list = [
                {
                    "name": name,
                    "description": info["description"],
                    "inputSchema": info["inputSchema"],
                }
                for name, info in TOOLS.items()
            ]
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": tools_list},
            }
        elif method == "tools/call":
            params = req.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            if tool_name not in TOOLS:
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Tool not found: {tool_name}",
                    },
                }
            else:
                try:
                    result = TOOLS[tool_name]["handler"](brain, arguments)
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(
                                        result, ensure_ascii=False, indent=2
                                    ),
                                }
                            ]
                        },
                    }
                except Exception as e:
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": f"Error: {e}"}]
                        },
                        "isError": True,
                    }
        else:
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown method: {method}"},
            }

        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
