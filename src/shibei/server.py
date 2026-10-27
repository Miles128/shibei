"""MCP JSON-RPC stdio server — 任何 MCP 兼容 Agent 可接入。

暴露工具：
  - shibei_context           : 答前上下文包（强制召回入口）
  - shibei_search            : 语义搜索
  - shibei_import            : 导入文档
  - shibei_list_sources      : 列出已入库文件
  - shibei_persona           : 轻量画像（按类别聚合的记忆摘要）
  - shibei_dream             : 知识库洞察 + 整理建议（skill 形式，零 LLM 依赖）
  - shibei_remember          : 写入 Agent 对话记忆
  - shibei_update_memory     : 更新/压缩已有记忆
  - shibei_consolidate       : 相似 chunk 聚类
  - shibei_apply_consolidation: 根据外部 Agent 提供的摘要直接合并相似 chunk

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
import uuid
from datetime import datetime
from typing import Any

from .client import ShibeiClient


def _init_brain():
    config_path = os.environ.get("SHIBEI_CONFIG", "config.yaml")
    return ShibeiClient(config_path)


def _handle_search(brain, args):
    return brain.search(
        query=args.get("query", ""),
        limit=args.get("limit", 5),
        tag=args.get("tag"),
    )


def _handle_context(brain, args):
    result = brain.context(
        message=args.get("message", ""),
        limit=args.get("limit", 5),
        tag=args.get("tag"),
        write_hints=args.get("write_hints", True),
        query=args.get("query"),
    )
    # 自动记忆抽取（P1，可选，默认关）：检测到第一人称披露时用 LLM 提取并写入
    cfg = getattr(brain, "config", None)
    am = getattr(cfg, "auto_memory", None) if cfg is not None else None
    if am is not None and am.enabled and (result.get("suggest_remember") or {}).get("should"):
        try:
            written = brain.auto_remember(args.get("message", ""))
            if written:
                result["auto_remembered"] = [
                    {"id": w.get("id"), "fact": w.get("fact")} for w in written
                ]
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning("自动记忆抽取失败: %s", e)
    return result


def _handle_persona(brain, args):
    return brain.persona(
        per_category=args.get("per_category", 5),
        max_facts=args.get("max_facts", 20),
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
        housekeeping=args.get("housekeeping"),
        format=args.get("format", "brief"),
        persist=args.get("persist", True),
    )


def _handle_consolidate(brain, args):
    return brain.consolidate(
        threshold=args.get("threshold", 0.6),
        min_cluster_size=args.get("min_cluster_size", 2),
        max_chunks=args.get("max_chunks", 500),
        apply=args.get("apply", False),
    )


def _handle_apply_consolidation(brain, args):
    return brain.apply_consolidation(
        clusters=args.get("clusters", []),
    )


def _handle_remember(brain, args):
    return brain.remember(
        fact=args.get("fact", ""),
        category=args.get("category", "user"),
        source=args.get("source", ""),
        tags=args.get("tags", ""),
        expires_at=args.get("expires_at"),
        source_message_ids=args.get("source_message_ids", ""),
        memory_type=args.get("memory_type", ""),
        priority=args.get("priority", 0),
        scene_name=args.get("scene_name", ""),
        confidence=args.get("confidence", 0.0),
    )


def _handle_update_memory(brain, args):
    return brain.update_memory(
        memory_id=args.get("memory_id", ""),
        fact=args.get("fact", ""),
        tags=args.get("tags"),
    )


TOOLS: dict[str, dict[str, Any]] = {
    "shibei_context": {
        "handler": _handle_context,
        "description": (
            "答前上下文包（多宿主强制召回入口）。收到非纯指令类用户消息时，"
            "必须先调用本工具再回答。\n"
            "推荐流程：先用你自己的 LLM 从用户原文抽出 5–8 个检索关键词"
            "（含同义/中英，去掉人称语气词），再调用本工具，"
            "传入 message=用户原文、query=关键词。\n"
            "返回文档命中、相关记忆、related，以及 suggest_remember。"
            "不要依赖拾贝服务端 LLM；明确关键词搜索用 shibei_search。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "本轮用户消息原文",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "可选。由你（Agent）用自己的 LLM 扩写后的检索关键词"
                        "（空格分隔）。强烈建议传入以提升召回；不传则服务端规则抽词。"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "default": 5,
                    "description": "返回条数上限",
                },
                "tag": {
                    "type": "string",
                    "description": "标签过滤（如 interview/ai/finance）",
                },
                "write_hints": {
                    "type": "boolean",
                    "default": True,
                    "description": "是否返回 suggest_remember 写侧提示",
                },
            },
            "required": ["message"],
        },
    },
    "shibei_search": {
        "handler": _handle_search,
        "description": (
            "语义搜索个人知识库（用户明确要搜关键词时用）。"
            "query 建议先用你自己的 LLM 改写成 BM25 友好关键词再传入。"
            "一般对话答前召回请优先用 shibei_context。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词（建议 Agent 侧已改写）"},
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
    "shibei_persona": {
        "handler": _handle_persona,
        "description": "轻量画像 — 按类别聚合长期记忆中的高密度事实（偏好/项目/技术栈…），零 LLM 依赖。适合在长时间对话或跨会话开始时快速了解用户，对标 TencentDB 的 L3 Persona。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "per_category": {
                    "type": "integer",
                    "default": 5,
                    "description": "每个类别最多返回几条事实",
                },
                "max_facts": {
                    "type": "integer",
                    "default": 20,
                    "description": "画像总事实数上限",
                },
            },
        },
    },
    "shibei_dream": {
        "handler": _handle_dream,
        "description": "知识库 dreaming — 发现隐藏连接、跨文档关联、主题模式，并执行零 LLM 依赖的整理：清理过期记忆、识别可压缩记忆候选、发现相似 chunk 候选。Shibei 自身不调用 LLM；具体压缩/合并由外部 Agent 判断并调用 shibei_update_memory / shibei_apply_consolidation 执行。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["brief", "full"],
                    "default": "brief",
                    "description": "输出格式。brief=统计+洞察+整理摘要（推荐 MCP 使用）；full=额外返回 cross_references 和 patterns 详情。",
                },
                "persist": {
                    "type": "boolean",
                    "default": True,
                    "description": "是否将结果持久化到 db_path/dream_result.json，供后续 diff。",
                },
                "housekeeping": {
                    "type": ["boolean", "object"],
                    "default": True,
                    "description": "是否执行整理。false=禁用；true=使用默认配置；object=自定义配置（如 {\"compress_max_age_days\": 30, \"consolidate_threshold\": 0.6}）",
                },
            },
        },
    },
    "shibei_consolidate": {
        "handler": _handle_consolidate,
        "description": "整理知识库：发现内容高度重叠的 chunk 聚类。默认只返回候选；设置 apply=true 可直接用启发式摘要合并并写回数据库（零 LLM 依赖）。",
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
                    "description": "true=直接应用启发式合并；false=只返回候选",
                },
            },
        },
    },
    "shibei_apply_consolidation": {
        "handler": _handle_apply_consolidation,
        "description": "根据外部 Agent 提供的 cluster 摘要，直接删除原重叠 chunks 并把合并摘要写回知识库。若 summary 为空，则自动使用启发式拼接摘要（零 LLM 依赖）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "clusters": {
                    "type": "array",
                    "description": "要应用的 cluster 列表，每项包含 rowids（整数列表）、summary（合并摘要）、sources（来源路径列表）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "rowids": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "要合并的 chunk rowid 列表，来自 shibei_dream 或 shibei_consolidate 返回的 cluster.chunks._rowid",
                            },
                            "summary": {
                                "type": "string",
                                "description": "由外部 Agent 生成的合并摘要",
                            },
                            "sources": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "来源文件路径列表（可选）",
                            },
                        },
                        "required": ["rowids", "summary"],
                    },
                },
            },
            "required": ["clusters"],
        },
    },
    "shibei_remember": {
        "handler": _handle_remember,
        "description": (
            "将对话或交互中的重要事实写入拾贝长期记忆。"
            "若 shibei_context 返回 suggest_remember.should=true，应答后主动调用本工具写入。"
            "必须调用场景：\n"
            "1. 用户个人情况/偏好/习惯/目标（'我喜欢...'、'我计划...'）\n"
            "2. 项目/工作经历（'我开发过...'、'我负责过...'）\n"
            "3. 关键决策与约定（'我决定...'、'请记住...'）\n"
            "4. 后续可能复用且库中尚无的新事实。\n"
            "写入后会在后续 shibei_context / shibei_search 中自动召回。"
        ),
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
                "source_message_ids": {
                    "type": "string",
                    "description": "溯源：触发该记忆的原始消息 ID（逗号分隔）。召回时随记忆返回，便于回溯到原始对话。",
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["persona", "episodic", "instruction"],
                    "description": "L1 语义类型：persona=稳定偏好/身份，episodic=发生过的事件/决策，instruction=长期行为规则",
                },
                "priority": {
                    "type": "integer",
                    "description": "0-100；instruction 可用 -1 表示严格死命令",
                },
                "scene_name": {
                    "type": "string",
                    "description": "L2 情境名，如「我在和四海做 dsh 迁移」",
                },
                "confidence": {
                    "type": "number",
                    "description": "0-1 抽取置信度",
                },
                "expires_at": {
                    "type": "string",
                    "description": "过期时间 ISO 8601（可选）",
                },
            },
            "required": ["fact"],
        },
    },
    "shibei_update_memory": {
        "handler": _handle_update_memory,
        "description": "更新一条已存在的 Shibei 记忆内容。通常由 dreaming 建议压缩旧记忆后，Agent 用自己的 LLM 判断并执行压缩。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "要更新的记忆 ID（从 list_memories / dreaming 中获取）",
                },
                "fact": {
                    "type": "string",
                    "description": "更新后的记忆内容",
                },
                "tags": {
                    "type": "string",
                    "description": "更新后的标签（可选，不传则保持原标签）",
                },
            },
            "required": ["memory_id", "fact"],
        },
    },
}


def _compact_result(result: Any, keep: int) -> Any:
    """递归截断结果中的长文本字段，生成紧凑版（用于上下文卸载后的响应）。"""
    if isinstance(result, dict):
        return {k: _compact_result(v, keep) for k, v in result.items()}
    if isinstance(result, list):
        return [_compact_result(v, keep) for v in result]
    if isinstance(result, str) and len(result) > keep:
        return result[:keep] + "…"
    return result


def _maybe_offload(brain, result) -> dict:
    """结果过大时写入 <db>/offload/<id>.json，返回紧凑摘要 + 引用路径。

    对标 TencentDB 的短期上下文卸载：完整证据落盘，上下文只留摘要，
    需要完整内容时宿主可读取该文件（Agent 自带文件访问能力）。
    """
    cfg = getattr(brain, "config", None)
    if cfg is None or not getattr(cfg, "offload", None) or not cfg.offload.enabled:
        return result

    text = json.dumps(result, ensure_ascii=False)
    size = len(text.encode("utf-8"))
    if size <= cfg.offload.max_bytes:
        return result

    offload_dir = cfg.db_path_expanded / "offload"
    offload_dir.mkdir(parents=True, exist_ok=True)
    off_id = f"{datetime.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    path = offload_dir / f"{off_id}.json"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as exc:
        import logging
        logging.getLogger(__name__).warning("上下文卸载写文件失败: %s", exc)
        return result

    compact = _compact_result(result, cfg.offload.keep_summary_text)
    compact["offloaded"] = {
        "path": str(path),
        "bytes": size,
        "note": "完整结果已卸载到该文件，上下文仅保留摘要；如需完整内容请读取该文件",
    }
    return compact


def _handle_request(brain, req) -> 'dict | None':
    """处理单个 JSON-RPC 请求。返回响应 dict；通知（无 id）返回 None。"""
    if not isinstance(req, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request: expected object"},
        }

    method = req.get("method", "")
    req_id = req.get("id")

    # JSON-RPC 2.0：无 id 视为通知，不应返回响应
    if req_id is None:
        return None

    if method == "ping":
        # MCP 必需方法：保活探测
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "shibei", "version": "0.1.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        tools_list = [
            {
                "name": name,
                "description": info["description"],
                "inputSchema": info["inputSchema"],
            }
            for name, info in TOOLS.items()
        ]
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": tools_list},
        }
    if method == "tools/call":
        params = req.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name not in TOOLS:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Tool not found: {tool_name}",
                },
            }
        try:
            result = TOOLS[tool_name]["handler"](brain, arguments)
            # 上下文卸载：结果过大时落盘 + 返回紧凑摘要（仅对结果型工具生效）
            result = _maybe_offload(brain, result)
            return {
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
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {e}"}]
                },
                "isError": True,
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def _write_response(resp: dict):
    sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    brain = _init_brain()

    if os.environ.get("SHIBEI_MCP_DEBUG"):
        import logging
        logging.basicConfig(level=logging.DEBUG)

    for line in sys.stdin:
        try:
            req = json.loads(line.strip())
        except json.JSONDecodeError as e:
            # 解析失败必须回 -32700，否则客户端会一直挂起等待
            _write_response({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            })
            continue

        if isinstance(req, list):
            # JSON-RPC 2.0 batch：逐个处理，只回有响应的部分
            resps = [
                r for r in (_handle_request(brain, item) for item in req)
                if r is not None
            ]
            if resps:
                sys.stdout.write(json.dumps(resps, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            continue

        resp = _handle_request(brain, req)
        if resp is not None:
            _write_response(resp)


if __name__ == "__main__":
    main()
