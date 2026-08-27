"""拾贝 sidecar — loopback HTTP，内核的唯一对外进程。"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import load_config
from .services import Shibei

VERSION = "0.1.0"
WRITE_PATHS = {
    "/v1/remember",
    "/v1/memories/update",
    "/v1/memories/forget",
    "/v1/memories/cleanup",
    "/v1/import",
    "/v1/dream",
    "/v1/consolidate",
    "/v1/consolidate/apply",
    "/v1/clean",
    "/v1/auto-remember",
}


def run_dir_for(config_path: str) -> Path:
    cfg = load_config(config_path)
    parent = Path(cfg.storage.path).expanduser().parent
    digest = hashlib.sha1(str(Path(config_path).resolve()).encode()).hexdigest()[:8]
    return parent / "run" / digest


def run_file_for(config_path: str) -> Path:
    return run_dir_for(config_path) / "sidecar.json"


class SidecarState:
    def __init__(self, config_path: str, token: str):
        self.config_path = str(Path(config_path).resolve())
        self.token = token
        self.brain = Shibei(self.config_path)
        self.write_lock = threading.Lock()
        self.started_at = datetime.now(timezone.utc).isoformat()


def _unauthorized() -> tuple[int, dict]:
    return 401, {"error": {"code": "unauthorized", "message": "invalid token"}}


def _invalid(message: str) -> tuple[int, dict]:
    return 400, {"error": {"code": "invalid_request", "message": message}}


def _not_found(message: str) -> tuple[int, dict]:
    return 404, {"error": {"code": "not_found", "message": message}}


def _dispatch(state: SidecarState, method: str, path: str, qs: dict, body: dict) -> tuple[int, Any]:
    brain = state.brain
    if method == "GET" and path == "/v1/health":
        return 200, {
            "ok": True,
            "version": VERSION,
            "pid": os.getpid(),
            "config_path": state.config_path,
            "db_path": str(brain.config.db_path_expanded),
            "started_at": state.started_at,
        }
    if method == "POST" and path == "/v1/context":
        return 200, brain.context(
            message=body.get("message", ""),
            limit=int(body.get("limit", 5)),
            tag=body.get("tag"),
            write_hints=body.get("write_hints", True),
            query=body.get("query"),
        )
    if method == "POST" and path == "/v1/search":
        include = body.get("include_memories", True)
        return 200, brain.search(
            query=body.get("query", ""),
            limit=int(body.get("limit", 5)),
            tag=body.get("tag"),
            include_memories=include,
            rewrite=body.get("rewrite"),
            llm_rewrite=body.get("llm_rewrite"),
            expand=body.get("expand"),
            fuzzy=body.get("fuzzy"),
            synonyms=body.get("synonyms"),
        )
    if method == "POST" and path == "/v1/remember":
        return 200, brain.remember(
            fact=body.get("fact", ""),
            category=body.get("category", "user"),
            source=body.get("source", ""),
            tags=body.get("tags", ""),
            expires_at=body.get("expires_at"),
            source_message_ids=body.get("source_message_ids", ""),
        )
    if method == "POST" and path == "/v1/memories/update":
        ok = brain.update_memory(
            memory_id=body.get("memory_id", ""),
            fact=body.get("fact", ""),
            tags=body.get("tags"),
        )
        if not ok:
            return _not_found("memory not found")
        return 200, True
    if method == "POST" and path == "/v1/memories/forget":
        ok = brain.forget_memory(body.get("memory_id", ""))
        if not ok:
            return _not_found("memory not found")
        return 200, True
    if method == "GET" and path == "/v1/memories":
        limit = int(qs.get("limit", 100))
        category = qs.get("category") or None
        return 200, brain.list_memories(limit=limit, category=category)
    if method == "POST" and path == "/v1/memories/search":
        return 200, brain.search_memories(
            query=body.get("query", ""),
            limit=int(body.get("limit", 5)),
            category=body.get("category"),
        )
    if method == "POST" and path == "/v1/memories/cleanup":
        return 200, brain.cleanup_memories()
    if method == "GET" and path == "/v1/persona":
        return 200, brain.persona(
            per_category=int(qs.get("per_category", 5)),
            max_facts=int(qs.get("max_facts", 20)),
        )
    if method == "POST" and path == "/v1/import":
        return 200, brain.import_all(
            dirs=body.get("dirs"),
            full=bool(body.get("full", False)),
            file_pattern=body.get("file_pattern"),
        )
    if method == "GET" and path == "/v1/sources":
        return 200, brain.list_sources(
            limit=int(qs.get("limit", 0)),
            offset=int(qs.get("offset", 0)),
        )
    if method == "POST" and path == "/v1/dream":
        return 200, brain.dream(
            housekeeping=body.get("housekeeping"),
            format=body.get("format", "brief"),
            persist=body.get("persist", True),
        )
    if method == "POST" and path == "/v1/consolidate":
        return 200, brain.consolidate(
            threshold=float(body.get("threshold", 0.6)),
            min_cluster_size=int(body.get("min_cluster_size", 2)),
            max_chunks=int(body.get("max_chunks", 500)),
            apply=bool(body.get("apply", False)),
        )
    if method == "POST" and path == "/v1/consolidate/apply":
        return 200, brain.apply_consolidation(clusters=body.get("clusters", []))
    if method == "POST" and path == "/v1/clean":
        return 200, brain.clean_orphans(
            dirs=body.get("dirs"),
            dry_run=bool(body.get("dry_run", False)),
        )
    if method == "POST" and path == "/v1/auto-remember":
        return 200, brain.auto_remember(body.get("message", ""))
    return 404, {"error": {"code": "not_found", "message": f"unknown {method} {path}"}}


def _make_handler(state: SidecarState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _token_ok(self) -> bool:
            header = self.headers.get("Authorization", "")
            return header == f"Bearer {state.token}"

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            if not raw:
                return {}
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
            return data

        def _send(self, status: int, payload: Any):
            blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def _handle(self, method: str):
            if not self._token_ok():
                self._send(*_unauthorized())
                return
            parsed = urlparse(self.path)
            path = parsed.path
            qs = {k: (v[0] if v else "") for k, v in parse_qs(parsed.query).items()}
            try:
                body = self._read_body() if method in ("POST", "PUT") else {}
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(*_invalid(str(exc)))
                return
            try:
                if path in WRITE_PATHS:
                    with state.write_lock:
                        status, payload = _dispatch(state, method, path, qs, body)
                else:
                    status, payload = _dispatch(state, method, path, qs, body)
            except ValueError as exc:
                self._send(*_invalid(str(exc)))
                return
            except Exception as exc:  # noqa: BLE001
                self._send(500, {"error": {"code": "internal", "message": str(exc)}})
                return
            self._send(status, payload)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

    return Handler


def start_server(
    config_path: str,
    host: str = "127.0.0.1",
    port: int = 0,
    token: str | None = None,
) -> tuple[ThreadingHTTPServer, dict]:
    """启动 sidecar（后台线程）。调用方负责 shutdown。"""
    state = SidecarState(config_path, token or secrets.token_hex(32))
    httpd = ThreadingHTTPServer((host, port), _make_handler(state))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    bound_host, bound_port = httpd.server_address[:2]
    info = {
        "host": bound_host,
        "port": bound_port,
        "token": state.token,
        "base_url": f"http://{bound_host}:{bound_port}",
        "config_path": state.config_path,
        "pid": os.getpid(),
        "started_at": state.started_at,
        "version": VERSION,
        "thread": thread,
    }
    return httpd, info


def write_run_file(config_path: str, info: dict) -> Path:
    path = run_file_for(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": info["pid"],
        "host": info["host"],
        "port": info["port"],
        "token": info["token"],
        "config_path": info["config_path"],
        "started_at": info.get("started_at", ""),
        "version": info.get("version", VERSION),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def read_run_file(config_path: str) -> dict | None:
    path = run_file_for(config_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "port" not in data or "token" not in data:
        return None
    data["base_url"] = f"http://{data.get('host', '127.0.0.1')}:{data['port']}"
    return data


def stop_sidecar(config_path: str) -> None:
    import signal

    info = read_run_file(config_path)
    path = run_file_for(config_path)
    if info:
        pid = int(info.get("pid") or 0)
        if pid and pid != os.getpid():
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _daemonize() -> None:
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    os.umask(0)


def main(argv: list[str] | None = None) -> None:
    import argparse
    import traceback

    parser = argparse.ArgumentParser(prog="shibei-sidecar")
    parser.add_argument(
        "--config",
        default=os.environ.get("SHIBEI_CONFIG", "config.yaml"),
    )
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args(argv)
    config_path = str(Path(args.config).expanduser().resolve())
    if args.stop:
        stop_sidecar(config_path)
        return
    if args.daemon:
        _daemonize()
        log_path = run_dir_for(config_path)
        log_path.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path / "sidecar.log", "a", encoding="utf-8")
        os.dup2(log_file.fileno(), 1)
        os.dup2(log_file.fileno(), 2)
        with open(os.devnull, "rb") as devnull:
            os.dup2(devnull.fileno(), 0)
    httpd = None
    try:
        httpd, info = start_server(config_path)
        write_run_file(config_path, info)
        info["thread"].join()
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()
        raise
    finally:
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass
        try:
            run_file_for(config_path).unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
