"""CLI 入口测试 — 参数解析与命令分发"""

import sys
from pathlib import Path

import pytest

from shibei import cli
from shibei.config import load_config


class FakeBrain:
    """Mock Shibei 服务，记录调用参数并返回固定结果。"""

    calls: list[tuple[str, dict]] = []

    def __init__(self, config_path: str):
        self.config = load_config(config_path)

    def search(self, query: str, limit: int = 5, tag: str | None = None):
        self.calls.append(("search", {"query": query, "limit": limit, "tag": tag}))
        return {
            "results": [
                {
                    "rank": 1,
                    "source": "note.md",
                    "score": 1.23,
                    "tags": "ai",
                    "text": "result text",
                }
            ],
            "memories": [
                {"category": "user", "text": "memory text", "score": 0.8}
            ],
            "expansion": {
                "expansions": {"关键词": ["同义词"]},
                "fuzzy": {},
            },
        }

    def context(
        self,
        message: str,
        limit: int = 5,
        tag: str | None = None,
        write_hints: bool = True,
        query: str | None = None,
    ):
        self.calls.append(
            (
                "context",
                {
                    "message": message,
                    "limit": limit,
                    "tag": tag,
                    "write_hints": write_hints,
                    "query": query,
                },
            )
        )
        pack = {
            "message": message,
            "query_used": "Redis 面试",
            "total": 1,
            "results": [
                {
                    "rank": 1,
                    "source": "redis.md",
                    "score": 1.0,
                    "tags": "interview",
                    "text": "Redis 缓存",
                }
            ],
            "memories": [],
            "related": [],
        }
        if write_hints:
            pack["suggest_remember"] = {
                "should": True,
                "fact": message,
                "reason": "first_person_disclosure",
            }
        return pack

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
        if progress is not None:
            progress(1, 1)
        return {"files": 2, "chunks": 3, "skipped": 0, "removed": 0, "deduped": 0}

    def remember(
        self,
        fact: str,
        category: str = "user",
        source: str = "",
        tags: str = "",
        expires_at: str | None = None,
        source_message_ids: str = "",
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
                    "source_message_ids": source_message_ids,
                },
            )
        )
        return {"id": "abc123456789", "fact": fact, "supersedes": None}

    def dream(
        self,
        housekeeping: dict | None = None,
        format: str = "brief",
        persist: bool = True,
    ):
        self.calls.append(
            (
                "dream",
                {"housekeeping": housekeeping, "format": format, "persist": persist},
            )
        )
        return {
            "status": "ok",
            "stats": {
                "unique_docs": 2,
                "total_chunks": 3,
                "cross_references": 1,
                "topics_with_connections": 1,
            },
            "insights": ["insight"],
            "cross_references": [
                {
                    "doc_a": "a.md",
                    "doc_b": "b.md",
                    "shared_keywords": ["k"],
                    "strength": 1,
                }
            ],
            "patterns": {"ai": ["k"]},
            "housekeeping": {"enabled": False},
        }


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch):
    """提供临时配置文件，并隔离 Shibei 实例。"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"storage:\n  path: {tmp_path / 'db'}\n", encoding="utf-8")

    monkeypatch.setattr(cli, "Shibei", FakeBrain)
    monkeypatch.setattr(
        cli, "resolve_config_path", lambda config, profile: str(cfg)
    )
    FakeBrain.calls = []
    return cfg


def test_cli_search(config_path, monkeypatch, capsys):
    """search 子命令正确解析参数并分发。"""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "shibei",
            "--config",
            str(config_path),
            "search",
            "关键词",
            "--limit",
            "10",
            "--tag",
            "ai",
            "--expand",
        ],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "[1] note.md" in out
    assert "相关记忆" in out
    assert FakeBrain.calls == [
        ("search", {"query": "关键词", "limit": 10, "tag": "ai"})
    ]


def test_cli_context(config_path, monkeypatch, capsys):
    """context 子命令正确解析消息并分发。"""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "shibei",
            "--config",
            str(config_path),
            "context",
            "我喜欢用 Python",
            "--limit",
            "3",
        ],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "query_used:" in out
    assert "suggest_remember" in out
    assert FakeBrain.calls == [
        (
            "context",
            {
                "message": "我喜欢用 Python",
                "limit": 3,
                "tag": None,
                "write_hints": True,
                "query": None,
            },
        )
    ]


def test_cli_import_with_paths(config_path, monkeypatch, capsys):
    """import 子命令正确解析位置参数、--full 与 --file-pattern。"""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "shibei",
            "--config",
            str(config_path),
            "import",
            "/notes",
            "/articles",
            "--full",
            "--file-pattern",
            "*.md",
        ],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "Imported: 2 files" in out
    assert FakeBrain.calls == [
        (
            "import_all",
            {"dirs": ["/notes", "/articles"], "full": True, "file_pattern": "*.md"},
        )
    ]


def test_cli_remember(config_path, monkeypatch, capsys):
    """remember 子命令正确解析事实与可选参数。"""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "shibei",
            "--config",
            str(config_path),
            "remember",
            "我喜欢用 Python",
            "--category",
            "preference",
            "--source",
            "session-1",
            "--tags",
            "python,backend",
        ],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "已记住" in out
    assert "我喜欢用 Python" in out
    assert FakeBrain.calls == [
        (
            "remember",
            {
                "fact": "我喜欢用 Python",
                "category": "preference",
                "source": "session-1",
                "tags": "python,backend",
                "expires_at": None,
                "source_message_ids": "",
            },
        )
    ]


def test_cli_dream(config_path, monkeypatch, capsys):
    """dream 子命令正确解析 format、--no-housekeeping 与 --no-persist。"""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "shibei",
            "--config",
            str(config_path),
            "dream",
            "--format",
            "full",
            "--no-housekeeping",
            "--no-persist",
        ],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "Dream 完成" in out
    assert FakeBrain.calls == [
        (
            "dream",
            {"housekeeping": False, "format": "full", "persist": False},
        )
    ]


def test_cli_profile_lifecycle(tmp_path, monkeypatch, capsys):
    """profile 子命令 create / list / show / delete 生命周期。"""
    profiles_dir = tmp_path / "profiles"
    monkeypatch.setattr("shibei.profile.PROFILES_DIR", profiles_dir)

    # create
    monkeypatch.setattr(sys, "argv", ["shibei", "profile", "create", "dev"])
    cli.main()
    assert "Created profile 'dev'" in capsys.readouterr().out

    # list
    monkeypatch.setattr(sys, "argv", ["shibei", "profile", "list"])
    cli.main()
    out = capsys.readouterr().out
    assert "Profiles (1)" in out
    assert "dev" in out

    # show
    monkeypatch.setattr(sys, "argv", ["shibei", "profile", "show", "dev"])
    cli.main()
    out = capsys.readouterr().out
    assert "Profile: dev" in out
    assert "storage:" in out

    # delete
    monkeypatch.setattr(sys, "argv", ["shibei", "profile", "delete", "dev"])
    cli.main()
    assert "Deleted profile 'dev'" in capsys.readouterr().out
