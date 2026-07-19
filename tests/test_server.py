"""MCP stdio server 测试 — mock stdin/stdout 覆盖 JSON-RPC 处理"""

import json
import sys
from io import StringIO

import pytest

from shibei import server


class FakeBrain:
    """Mock Shibei 服务，记录工具调用参数并返回固定结果。"""

    calls: list[tuple[str, dict]] = []

    def search(self, query: str = "", limit: int = 5, tag: str | None = None):
        self.calls.append(("search", {"query": query, "limit": limit, "tag": tag}))
        return {"results": []}

    def context(
        self,
        message: str = "",
        limit: int = 5,
        tag: str | None = None,
        write_hints: bool = True,
    ):
        self.calls.append(
            (
                "context",
                {
                    "message": message,
                    "limit": limit,
                    "tag": tag,
                    "write_hints": write_hints,
                },
            )
        )
        return {
            "message": message,
            "query_used": message,
            "total": 0,
            "results": [],
            "memories": [],
            "related": [],
            "suggest_remember": {"should": False, "fact": "", "reason": ""},
        }

    def import_all(
        self,
        dirs: list[str] | None = None,
        full: bool = False,
        file_pattern: str | None = None,
        progress=None,
    ):
        self.calls.append(
            ("import_all", {"dirs": dirs, "full": full, "file_pattern": file_pattern})
        )
        return {"files": 0, "chunks": 0}

    def list_sources(self, limit: int = 0, offset: int = 0):
        self.calls.append(("list_sources", {"limit": limit, "offset": offset}))
        return {"count": 0, "sources": []}

    def dream(
        self,
        housekeeping: dict | None = None,
        format: str = "brief",
        persist: bool = True,
    ):
        self.calls.append(
            ("dream", {"housekeeping": housekeeping, "format": format, "persist": persist})
        )
        return {"status": "ok"}

    def consolidate(
        self,
        threshold: float = 0.6,
        min_cluster_size: int = 2,
        max_chunks: int = 500,
        apply: bool = False,
    ):
        self.calls.append(
            (
                "consolidate",
                {
                    "threshold": threshold,
                    "min_cluster_size": min_cluster_size,
                    "max_chunks": max_chunks,
                    "apply": apply,
                },
            )
        )
        return {"clusters": []}

    def apply_consolidation(self, clusters: list[dict]):
        self.calls.append(("apply_consolidation", {"clusters": clusters}))
        return {"applied": 0}

    def remember(
        self,
        fact: str = "",
        category: str = "user",
        source: str = "",
        tags: str = "",
        expires_at: str | None = None,
    ):
        self.calls.append(
            (
                "remember",
                {
                    "fact": fact,
                    "category": category,
                    "source": source,
                    "tags": tags,
                    "expires_at": expires_at,
                },
            )
        )
        return {"id": "m1"}

    def update_memory(
        self,
        memory_id: str = "",
        fact: str = "",
        tags: str | None = None,
    ):
        self.calls.append(
            ("update_memory", {"memory_id": memory_id, "fact": fact, "tags": tags})
        )
        return True


class FailingBrain(FakeBrain):
    """用于验证 handler 异常时返回 isError=True 的 brain。"""

    def search(self, query: str = "", limit: int = 5, tag: str | None = None):
        raise ValueError("search boom")


@pytest.fixture
def server_env(monkeypatch):
    """将 server 的 stdin/stdout 与 brain 替换为 mock。"""
    monkeypatch.setattr(server, "_init_brain", FakeBrain)
    FakeBrain.calls = []


def _run(stdin_lines: list[str]) -> list[dict]:
    """向 server.main 送入请求并解析返回的 JSON-RPC 响应。"""
    sys.stdin = StringIO("\n".join(stdin_lines) + "\n")
    server.main()
    raw = sys.stdout.getvalue()
    return [json.loads(line) for line in raw.strip().split("\n") if line.strip()]


def test_server_initialize(server_env, monkeypatch):
    """initialize 返回协议版本与 serverInfo。"""
    stdout = StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    responses = _run(
        [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})]
    )

    assert len(responses) == 1
    resp = responses[0]
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert resp["result"]["serverInfo"]["name"] == "shibei"


def test_server_tools_list(server_env, monkeypatch):
    """tools/list 返回全部工具定义（含 shibei_context）。"""
    stdout = StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    responses = _run([json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})])

    assert len(responses) == 1
    tools = responses[0]["result"]["tools"]
    assert len(tools) == 9
    assert {t["name"] for t in tools} == set(server.TOOLS.keys())
    assert "shibei_context" in {t["name"] for t in tools}


@pytest.mark.parametrize(
    "name,arguments,expected,expected_call",
    [
        (
            "shibei_context",
            {"message": "我喜欢 Python", "limit": 3, "write_hints": True},
            {
                "message": "我喜欢 Python",
                "query_used": "我喜欢 Python",
                "total": 0,
                "results": [],
                "memories": [],
                "related": [],
                "suggest_remember": {"should": False, "fact": "", "reason": ""},
            },
            (
                "context",
                {
                    "message": "我喜欢 Python",
                    "limit": 3,
                    "tag": None,
                    "write_hints": True,
                },
            ),
        ),
        (
            "shibei_search",
            {"query": "ai", "limit": 3},
            {"results": []},
            ("search", {"query": "ai", "limit": 3, "tag": None}),
        ),
        (
            "shibei_import",
            {"dirs": ["/notes"], "full": True, "file_pattern": "*.md"},
            {"files": 0, "chunks": 0},
            (
                "import_all",
                {"dirs": ["/notes"], "full": True, "file_pattern": "*.md"},
            ),
        ),
        (
            "shibei_list_sources",
            {"limit": 10, "offset": 5},
            {"count": 0, "sources": []},
            ("list_sources", {"limit": 10, "offset": 5}),
        ),
        (
            "shibei_dream",
            {"format": "full", "persist": False},
            {"status": "ok"},
            ("dream", {"housekeeping": None, "format": "full", "persist": False}),
        ),
        (
            "shibei_consolidate",
            {"threshold": 0.5, "min_cluster_size": 3, "max_chunks": 100, "apply": True},
            {"clusters": []},
            (
                "consolidate",
                {
                    "threshold": 0.5,
                    "min_cluster_size": 3,
                    "max_chunks": 100,
                    "apply": True,
                },
            ),
        ),
        (
            "shibei_apply_consolidation",
            {"clusters": [{"rowids": [1, 2], "summary": "合并"}]},
            {"applied": 0},
            (
                "apply_consolidation",
                {"clusters": [{"rowids": [1, 2], "summary": "合并"}]},
            ),
        ),
        (
            "shibei_remember",
            {
                "fact": "我喜欢 Python",
                "category": "preference",
                "source": "s1",
                "tags": "py",
            },
            {"id": "m1"},
            (
                "remember",
                {
                    "fact": "我喜欢 Python",
                    "category": "preference",
                    "source": "s1",
                    "tags": "py",
                    "expires_at": None,
                },
            ),
        ),
        (
            "shibei_update_memory",
            {"memory_id": "m1", "fact": "压缩后", "tags": "py"},
            True,
            ("update_memory", {"memory_id": "m1", "fact": "压缩后", "tags": "py"}),
        ),
    ],
)
def test_server_tools_call(
    server_env, monkeypatch, name, arguments, expected, expected_call
):
    """tools/call 正确分发全部 tool handler。"""
    stdout = StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    req = {
        "jsonrpc": "2.0",
        "id": 42,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    responses = _run([json.dumps(req)])

    assert len(responses) == 1
    resp = responses[0]
    assert resp["id"] == 42
    assert "error" not in resp
    text = resp["result"]["content"][0]["text"]
    assert json.loads(text) == expected
    assert FakeBrain.calls == [expected_call]


def test_server_unknown_tool_returns_method_not_found(server_env, monkeypatch):
    """未知 tool 返回 -32601。"""
    stdout = StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    req = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "unknown_tool", "arguments": {}},
    }
    responses = _run([json.dumps(req)])

    assert len(responses) == 1
    assert responses[0]["id"] == 7
    assert responses[0]["error"]["code"] == -32601
    assert "Tool not found" in responses[0]["error"]["message"]


def test_server_unknown_method_returns_method_not_found(server_env, monkeypatch):
    """未知 JSON-RPC 方法返回 -32601。"""
    stdout = StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    req = {"jsonrpc": "2.0", "id": 8, "method": "foo/bar"}
    responses = _run([json.dumps(req)])

    assert len(responses) == 1
    assert responses[0]["id"] == 8
    assert responses[0]["error"]["code"] == -32601
    assert "Unknown method" in responses[0]["error"]["message"]


def test_server_handler_exception_returns_is_error(monkeypatch):
    """tool handler 抛异常时返回 isError=True。"""
    monkeypatch.setattr(server, "_init_brain", FailingBrain)
    stdout = StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    req = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {"name": "shibei_search", "arguments": {"query": "x"}},
    }
    responses = _run([json.dumps(req)])

    assert len(responses) == 1
    resp = responses[0]
    assert resp["id"] == 9
    assert resp.get("isError") is True
    text = resp["result"]["content"][0]["text"]
    assert "Error: search boom" in text


def test_server_initialized_notification_silenced(server_env, monkeypatch):
    """notifications/initialized 不返回响应。"""
    stdout = StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    responses = _run(
        [json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})]
    )
    assert responses == []
