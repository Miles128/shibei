"""进程外拾贝客户端 — 所有适配器经 sidecar HTTP 访问内核。"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import load_config
from .sidecar import (
    read_run_file,
    read_shared_run_file,
    run_dir_for,
    stop_sidecar,
)


class SidecarError(RuntimeError):
    """sidecar 不可用或请求失败。"""


def _src_on_path() -> str:
    return str(Path(__file__).resolve().parent.parent)


def _health(info: dict, config_path: str, timeout: float = 0.2) -> bool:
    expected = str(Path(config_path).resolve())
    if info.get("config_path") and info["config_path"] != expected:
        return False
    try:
        status, body = _request(
            info["base_url"],
            info["token"],
            "GET",
            "/v1/health",
            timeout=timeout,
        )
    except SidecarError:
        return False
    return status == 200 and body.get("ok") is True and body.get("config_path") == expected


def _spawn(config_path: str) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = _src_on_path() + os.pathsep + env.get("PYTHONPATH", "")
    env["SHIBEI_CONFIG"] = config_path
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "shibei.sidecar",
            "--config",
            config_path,
            "--daemon",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _shared_info(config_path: str) -> dict | None:
    """若全局多库 daemon 正在运行且包含这个 config，返回它的连接信息。"""
    if os.environ.get("SHIBEI_NO_SHARED"):
        return None
    info = read_shared_run_file()
    if not info:
        return None
    try:
        status, body = _request(
            info["base_url"], info["token"], "GET", "/v1/brains", timeout=1.5
        )
    except SidecarError:
        return None
    if status != 200 or not isinstance(body, dict):
        return None
    target = str(Path(config_path).expanduser().resolve())
    for entry in body.get("brains") or []:
        if str(Path(str(entry.get("config_path", ""))).resolve()) == target:
            return {**info, "brain": entry.get("name"), "brains": body.get("brains")}
    return None


def ensure_sidecar(config_path: str, timeout: float = 20.0) -> dict:
    """复用已有 sidecar，否则拉起 daemon。冷启动含模型/分词初始化，留足 20s。"""
    config_path = str(Path(config_path).expanduser().resolve())
    shared = _shared_info(config_path)
    if shared is not None:
        return shared
    run_dir = run_dir_for(config_path)
    run_dir.mkdir(parents=True, exist_ok=True)
    lock_path = run_dir / "sidecar.lock"
    with open(lock_path, "w", encoding="utf-8") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        info = read_run_file(config_path)
        if info and _health(info, config_path):
            return info
        if info:
            stop_sidecar(config_path)
        _spawn(config_path)
        deadline = time.time() + timeout
        while time.time() < deadline:
            info = read_run_file(config_path)
            if info and _health(info, config_path):
                return info
            time.sleep(0.05)
        raise SidecarError(f"sidecar failed to start for {config_path}")


def _request(
    base_url: str,
    token: str,
    method: str,
    path: str,
    body: dict | None = None,
    timeout: float = 2.0,
) -> tuple[int, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(base_url + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            payload = json.loads(raw) if raw else None
            return resp.status, payload
    except HTTPError as exc:
        err_body = exc.read().decode("utf-8") if exc.fp else ""
        try:
            parsed = json.loads(err_body) if err_body else {}
        except json.JSONDecodeError:
            parsed = {"error": {"message": err_body or str(exc)}}
        message = (
            (parsed.get("error") or {}).get("message")
            if isinstance(parsed, dict)
            else str(exc)
        )
        raise SidecarError(message or str(exc)) from exc
    except URLError as exc:
        raise SidecarError(str(exc.reason or exc)) from exc


class ShibeiClient:
    """与 Shibei 内核同名的进程外 API。"""

    def __init__(
        self,
        config_path: str = "config.yaml",
        token: 'str | None' = None,
    ):
        self.config_path = str(Path(config_path).expanduser().resolve())
        self.config = load_config(config_path)
        info = ensure_sidecar(self.config_path)
        self._base = info["base_url"]
        # 允许下游程序用受限 token 覆盖 daemon 的 bootstrap token
        self._token = token or os.environ.get("SHIBEI_TOKEN") or info["token"]
        self.brain = info.get("brain")

    def _get(self, path: str, query: dict | None = None, timeout: float = 2.0):
        params = dict(query or {})
        if self.brain:
            params.setdefault("brain", self.brain)
        qs = ""
        filtered = {k: v for k, v in params.items() if v is not None}
        if filtered:
            qs = "?" + urlencode(filtered)
        _, payload = _request(
            self._base, self._token, "GET", path + qs, timeout=timeout
        )
        return payload

    def _post(self, path: str, body: dict, timeout: float = 2.0):
        payload_body = dict(body)
        if self.brain:
            payload_body.setdefault("brain", self.brain)
        _, payload = _request(
            self._base, self._token, "POST", path, body=payload_body, timeout=timeout
        )
        return payload

    def brains(self, stats: bool = True) -> dict:
        """列出当前 daemon 可用的 brain。"""
        return self._get("/v1/brains", {"stats": "1" if stats else "0"})

    def register_brain(
        self,
        name: str,
        config_path: 'str | None' = None,
        create: bool = False,
        description: str = "",
    ) -> dict:
        """运行时注册一个库；create=True 时自动生成配置。"""
        body: dict[str, Any] = {"name": name, "description": description}
        if config_path:
            body["config_path"] = config_path
        if create:
            body["create"] = True
        return self._post("/v1/brains", body, timeout=10)

    def remove_brain(self, name: str) -> dict:
        return self._post("/v1/brains/remove", {"name": name}, timeout=10)

    def search(
        self,
        query: str,
        limit: int = 5,
        tag: str | None = None,
        include_memories: bool = True,
        rewrite: bool | None = None,
        llm_rewrite: bool | None = None,
        expand: bool | None = None,
        fuzzy: bool | None = None,
        synonyms: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "query": query,
            "limit": limit,
            "include_memories": include_memories,
        }
        if tag is not None:
            body["tag"] = tag
        if rewrite is not None:
            body["rewrite"] = rewrite
        if llm_rewrite is not None:
            body["llm_rewrite"] = llm_rewrite
        if expand is not None:
            body["expand"] = expand
        if fuzzy is not None:
            body["fuzzy"] = fuzzy
        if synonyms is not None:
            body["synonyms"] = synonyms
        return self._post("/v1/search", body)

    def recall(
        self,
        query: str | None = None,
        message: str = "",
        limit: int = 5,
        tag: str | None = None,
        mode: str = "raw",
        memories: bool = True,
        persona: bool = False,
        write_hints: bool = False,
        trace: bool = False,
        full_text: bool = False,
        mmr: bool = False,
        rewrite: bool | None = None,
        llm_rewrite: bool | None = None,
        expand: bool | None = None,
        fuzzy: bool | None = None,
        synonyms: str | None = None,
        vector: bool | None = None,
        rerank: bool | None = None,
        brain: str | None = None,
        timeout: float = 60.0,
    ) -> dict:
        """统一召回入口（默认超时放大到 60s：rerank/LLM 改写会比普通搜索慢）。

        brain: 指定库名；``"*"`` 表示联邦召回所有库；缺省用客户端当前库。
        """
        body: dict[str, Any] = {
            "query": query,
            "message": message,
            "limit": limit,
            "mode": mode,
            "memories": memories,
            "persona": persona,
            "write_hints": write_hints,
            "trace": trace,
            "full_text": full_text,
            "mmr": mmr,
        }
        if brain is not None:
            body["brain"] = brain
        if tag is not None:
            body["tag"] = tag
        for key, value in (
            ("rewrite", rewrite),
            ("llm_rewrite", llm_rewrite),
            ("expand", expand),
            ("fuzzy", fuzzy),
            ("synonyms", synonyms),
            ("vector", vector),
            ("rerank", rerank),
        ):
            if value is not None:
                body[key] = value
        return self._post("/v1/recall", body, timeout=timeout)

    def context(
        self,
        message: str,
        limit: int = 5,
        tag: str | None = None,
        write_hints: bool = True,
        query: str | None = None,
    ) -> dict:
        return self._post(
            "/v1/context",
            {
                "message": message,
                "limit": limit,
                "tag": tag,
                "write_hints": write_hints,
                "query": query,
            },
        )

    def import_all(
        self,
        dirs: list[str] | None = None,
        full: bool = False,
        file_pattern: str | None = None,
        progress=None,
        timeout: float = 120,
    ) -> dict:
        return self._post(
            "/v1/import",
            {"dirs": dirs, "full": full, "file_pattern": file_pattern},
            timeout=timeout,
        )

    def list_sources(self, limit: int = 0, offset: int = 0) -> dict:
        return self._get("/v1/sources", {"limit": limit, "offset": offset})

    def list_chunks(
        self,
        limit: int = 200,
        offset: int = 0,
        source: str | None = None,
        tag: str | None = None,
    ) -> dict:
        return self._get(
            "/v1/chunks",
            {"limit": limit, "offset": offset, "source": source, "tag": tag},
        )

    def clean_orphans(
        self,
        dirs: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict:
        return self._post("/v1/clean", {"dirs": dirs, "dry_run": dry_run}, timeout=120)

    def dream(
        self,
        housekeeping: dict | None = None,
        format: str = "brief",
        persist: bool = True,
    ) -> dict:
        return self._post(
            "/v1/dream",
            {"housekeeping": housekeeping, "format": format, "persist": persist},
            timeout=120,
        )

    def remember(
        self,
        fact: str,
        category: str = "user",
        source: str = "",
        tags: str = "",
        expires_at: str | None = None,
        source_message_ids: str = "",
    ) -> dict:
        return self._post(
            "/v1/remember",
            {
                "fact": fact,
                "category": category,
                "source": source,
                "tags": tags,
                "expires_at": expires_at,
                "source_message_ids": source_message_ids,
            },
        )

    def persona(self, per_category: int = 5, max_facts: int = 20) -> dict:
        return self._get(
            "/v1/persona",
            {"per_category": per_category, "max_facts": max_facts},
        )

    def auto_remember(self, message: str) -> list[dict]:
        return self._post("/v1/auto-remember", {"message": message}, timeout=30)

    def search_memories(
        self,
        query: str,
        limit: int = 5,
        category: str | None = None,
    ) -> list[dict]:
        return self._post(
            "/v1/memories/search",
            {"query": query, "limit": limit, "category": category},
        )

    def list_memories(
        self,
        limit: int = 100,
        category: str | None = None,
    ) -> list[dict]:
        return self._get("/v1/memories", {"limit": limit, "category": category})

    def forget_memory(self, memory_id: str) -> bool:
        return bool(self._post("/v1/memories/forget", {"memory_id": memory_id}))

    def cleanup_memories(self) -> int:
        return int(self._post("/v1/memories/cleanup", {}))

    def update_memory(
        self,
        memory_id: str,
        fact: str,
        tags: str | None = None,
    ) -> bool:
        return bool(
            self._post(
                "/v1/memories/update",
                {"memory_id": memory_id, "fact": fact, "tags": tags},
            )
        )

    def consolidate(
        self,
        threshold: float = 0.6,
        min_cluster_size: int = 2,
        max_chunks: int = 500,
        apply: bool = False,
    ) -> dict:
        return self._post(
            "/v1/consolidate",
            {
                "threshold": threshold,
                "min_cluster_size": min_cluster_size,
                "max_chunks": max_chunks,
                "apply": apply,
            },
            timeout=120,
        )

    def apply_consolidation(self, clusters: list[dict]) -> dict:
        return self._post(
            "/v1/consolidate/apply",
            {"clusters": clusters},
            timeout=120,
        )
