"""召回诊断台状态机（无 UI 依赖）测试。"""

from shibei.tui.session import FLAGS, RecallSession

PAYLOAD = {
    "schema_version": 1,
    "mode": "raw",
    "query": "Redis",
    "query_used": "Redis 缓存",
    "total": 1,
    "results": [
        {
            "source": "/redis.md",
            "file_path": "/redis.md",
            "tags": "interview",
            "score": 0.83,
            "score_kind": "bm25",
            "rank": 1,
            "text": "面试重点考察 Redis 缓存与持久化。",
            "parent_text": "parent 上下文",
            "why": {
                "routes": ["content", "summary"],
                "ranks": {"content": 1, "summary": 3},
                "matched_terms": ["Redis", "缓存"],
            },
        }
    ],
    "memories": [
        {"text": "用户偏好 Python", "score": 0.5, "category": "preference"},
    ],
    "trace": {
        "stages": [{"name": "retrieve", "ms": 1.2, "coverage_dropped": 2}],
        "total_ms": 3.4,
        "dropped": {"coverage_gate": 2},
        "degraded": ["vector: 未生效"],
    },
}


class FakeClient:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[dict] = []
        self.chunk_calls: list[dict] = []

    def recall(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return self.payload

    def import_report(self) -> dict:
        return {
            "generated_at": "2026-11-08T10:00:00Z",
            "summary": {"failed": 1, "skipped": 0, "chunks": 1},
            "files": [
                {"path": "/notes/a.md", "status": "ok", "chunks": 1},
                {"path": "/notes/b.md", "status": "failed", "chunks": 0,
                 "error": "解码失败"},
            ],
        }

    def reindex(self, source: str, timeout: float = 300) -> dict:
        self.reindexed = [source]
        return {"files": 1, "chunks": 1}

    def save_eval_sample(self, query: str, expect=None, path=None) -> dict:
        self.eval_saved = {"query": query, "expect": list(expect or [])}
        return {"path": "/tmp/golden.yaml", "queries": 1, "updated": False}

    def list_chunks(
        self,
        limit: int = 200,
        offset: int = 0,
        source: str | None = None,
        tag: str | None = None,
    ) -> dict:
        self.chunk_calls.append(
            {"limit": limit, "offset": offset, "source": source, "tag": tag}
        )
        items = [
            {
                "_rowid": 11,
                "source": "/redis.md",
                "file_path": "/redis.md",
                "tags": "interview",
                "text": "面试重点考察 Redis 缓存与持久化。",
                "parent_text": "",
            },
            {
                "_rowid": 12,
                "source": "/ai.md",
                "file_path": "/ai.md",
                "tags": "ai",
                "text": "人工智能正在改变世界。",
                "parent_text": "",
            },
        ]
        return {
            "total": 2,
            "limit": limit,
            "offset": offset,
            "source": source,
            "tag": tag,
            "items": items[offset: offset + limit],
        }


def test_cycle_flag_is_tri_state():
    session = RecallSession(client=FakeClient(PAYLOAD))
    assert session.state.flags["vector"] is None
    assert session.cycle_flag("vector") is True
    assert session.cycle_flag("vector") is False
    assert session.cycle_flag("vector") is None


def test_run_passes_flags_and_trace():
    client = FakeClient(PAYLOAD)
    session = RecallSession(client=client)
    session.cycle_flag("vector")
    session.cycle_flag("memories")
    session.cycle_flag("memories")

    session.run("Redis")

    call = client.calls[0]
    assert call["query"] == "Redis"
    assert call["trace"] is True
    assert call["full_text"] is True
    assert call["vector"] is True
    assert call["memories"] is False
    assert call["rerank"] is None
    assert session.state.query == "Redis"


def test_run_rejects_empty_query():
    session = RecallSession(client=FakeClient(PAYLOAD))
    try:
        session.run("   ")
    except ValueError as exc:
        assert "不能为空" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_rows_and_memory_rows_projection():
    session = RecallSession(client=FakeClient(PAYLOAD))
    session.run("Redis")
    assert session.rows() == [(
        "1", "0.83", "-", "content+summary", "/redis.md",
        "面试重点考察 Redis 缓存与持久化。",
    )]
    assert session.memory_rows() == [("1", "0.50", "用户偏好 Python")]


def test_detail_shows_routes_ranks_and_matched_terms():
    session = RecallSession(client=FakeClient(PAYLOAD))
    session.run("Redis")
    detail = session.detail()
    assert "/redis.md" in detail
    assert "content#1" in detail
    assert "summary#3" in detail
    assert "Redis 缓存" in detail
    assert "parent 上下文" in detail


def test_summary_and_trace_notes():
    session = RecallSession(client=FakeClient(PAYLOAD))
    session.run("Redis")
    assert "Redis 缓存" in session.summary_line()
    assert "1 条" in session.summary_line()
    notes = session.trace_notes()
    assert any("coverage_gate" in note for note in notes)
    assert any("降级" in note for note in notes)


def test_flag_marks_are_tri_state():
    session = RecallSession(client=FakeClient(PAYLOAD))
    assert session.flag_mark("vector") == "·"
    session.cycle_flag("vector")
    assert session.flag_mark("vector") == "✓"
    session.cycle_flag("vector")
    assert session.flag_mark("vector") == "✗"
    for flag in FLAGS:
        assert session.flag_mark(flag) in {"·", "✓", "✗"}


def test_brain_cycle_and_client_binding():
    client = FakeClient(PAYLOAD)
    session = RecallSession(client=client)
    session.brains = ["default", "work", "life"]
    session.select_brain("work")
    assert session.state.brain == "work"

    assert session.cycle_brain() == "life"
    assert session.cycle_brain() == "default"
    session.run("Redis")
    assert client.calls[-1]["brain"] == "default"


def test_single_brain_cycle_is_noop():
    session = RecallSession(client=FakeClient(PAYLOAD))
    session.brains = ["default"]
    session.state.brain = "default"
    assert session.cycle_brain() == "default"


def test_result_basenames_and_eval_sample():
    client = FakeClient(PAYLOAD)
    session = RecallSession(client=client)
    session.run("Redis")

    assert session.result_basenames() == ["redis.md"]
    info = session.save_eval_sample(["redis.md"])
    assert info["queries"] == 1
    assert client.eval_saved == {"query": "Redis", "expect": ["redis.md"]}

    session.reindex("/redis.md")
    assert client.reindexed == ["/redis.md"]
