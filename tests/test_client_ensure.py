"""ShibeiClient 与 sidecar ensure（spawn / 复用 / 死进程重启）。"""

import os
import signal
import sys
import time
from pathlib import Path

import yaml

from shibei.sidecar import run_file_for, start_server, stop_sidecar, write_run_file


def _write_config(tmp_path: Path) -> Path:
    notes = tmp_path / "notes"
    notes.mkdir()
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.dump(
            {
                "storage": {"path": str(tmp_path / "db")},
                "sources": [str(notes)],
                "extensions": [".md"],
            }
        ),
        encoding="utf-8",
    )
    return cfg_path


def test_client_uses_existing_sidecar(tmp_path):
    from shibei.client import ShibeiClient

    cfg_path = _write_config(tmp_path)
    httpd, info = start_server(str(cfg_path))
    write_run_file(str(cfg_path), info)
    try:
        client = ShibeiClient(str(cfg_path))
        row = client.remember("用户住在上海", category="user")
        assert "上海" in row["fact"]
        pack = client.context("我住哪", query="上海 住")
        texts = [m.get("text", "") for m in pack.get("memories", [])]
        assert any("上海" in t for t in texts)
    finally:
        httpd.shutdown()
        httpd.server_close()
        stop_sidecar(str(cfg_path))


def test_ensure_spawns_and_reuses_pid(tmp_path):
    from shibei.client import ShibeiClient, ensure_sidecar

    cfg_path = str(_write_config(tmp_path))
    try:
        info1 = ensure_sidecar(cfg_path)
        pid1 = info1["pid"]
        assert pid1 != os.getpid()
        client = ShibeiClient(cfg_path)
        client.remember("偏好 Rust")
        info2 = ensure_sidecar(cfg_path)
        assert info2["pid"] == pid1
    finally:
        stop_sidecar(cfg_path)


def test_ensure_restarts_dead_process(tmp_path):
    from shibei.client import ensure_sidecar

    cfg_path = str(_write_config(tmp_path))
    try:
        info = ensure_sidecar(cfg_path)
        os.kill(info["pid"], signal.SIGKILL)
        time.sleep(0.1)
        info2 = ensure_sidecar(cfg_path)
        assert info2["pid"] != info["pid"]
        run = run_file_for(cfg_path)
        assert run.exists()
    finally:
        stop_sidecar(cfg_path)


def test_cli_remember_visible_via_client(tmp_path, monkeypatch):
    from shibei.cli import main
    from shibei.client import ShibeiClient

    cfg_path = str(_write_config(tmp_path))
    try:
        monkeypatch.setattr(
            sys,
            "argv",
            ["shibei", "--config", cfg_path, "remember", "用户喜欢喝茶"],
        )
        main()
        client = ShibeiClient(cfg_path)
        rows = client.list_memories()
        assert any("喝茶" in r.get("fact", "") for r in rows)
    finally:
        stop_sidecar(cfg_path)
