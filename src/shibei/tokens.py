"""按库授权的 token — 一个 daemon 服务多个程序时的访问控制。

``~/.shibei/tokens.yaml``（0600）：

.. code-block:: yaml

    tokens:
      - token: "xxxxx"
        name: cursor
        read: ["*"]          # 可读的库；["*"] = 全部
        write: ["work"]      # 可写的库；缺省 = 只读

daemon 启动时自带的 bootstrap token（写在 run/sidecar.json）等同 admin，读写全部，
所以已有客户端不需要改任何东西。
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from threading import Lock

import yaml

from .brains import home_dir

ADMIN_SCOPE = {"name": "admin", "read": ["*"], "write": ["*"]}


def tokens_file() -> Path:
    custom = os.environ.get("SHIBEI_TOKENS")
    if custom:
        return Path(custom).expanduser()
    return home_dir() / "tokens.yaml"


def _normalize_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def load_tokens(path: 'Path | None' = None) -> dict[str, dict]:
    """返回 ``token -> {name, read, write}``。支持列表和映射两种写法。"""
    path = path or tokens_file()
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    raw = data.get("tokens") if isinstance(data, dict) else None
    entries: dict[str, dict] = {}
    if isinstance(raw, dict):
        for token, value in raw.items():
            value = value if isinstance(value, dict) else {}
            entries[str(token)] = {
                "name": str(value.get("name") or ""),
                "read": _normalize_list(value.get("read")),
                "write": _normalize_list(value.get("write")),
            }
    elif isinstance(raw, list):
        for value in raw:
            if not isinstance(value, dict) or not value.get("token"):
                continue
            entries[str(value["token"])] = {
                "name": str(value.get("name") or ""),
                "read": _normalize_list(value.get("read")),
                "write": _normalize_list(value.get("write")),
            }
    return entries


def save_tokens(entries: dict[str, dict], path: 'Path | None' = None) -> Path:
    path = path or tokens_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tokens": [
            {
                "token": token,
                "name": entry.get("name", ""),
                "read": entry.get("read", []),
                "write": entry.get("write", []),
            }
            for token, entry in entries.items()
        ]
    }
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    return path


def create_token(
    name: str,
    read: 'list[str] | None' = None,
    write: 'list[str] | None' = None,
    path: 'Path | None' = None,
) -> tuple[str, dict]:
    if not name:
        raise ValueError("token 名称不能为空")
    entries = load_tokens(path)
    token = secrets.token_urlsafe(24)
    entry = {
        "name": name,
        "read": _normalize_list(read),
        "write": _normalize_list(write),
    }
    entries[token] = entry
    save_tokens(entries, path)
    return token, entry


def revoke_token(name_or_token: str, path: 'Path | None' = None) -> bool:
    entries = load_tokens(path)
    removed = False
    for token in list(entries):
        entry = entries[token]
        if token == name_or_token or entry.get("name") == name_or_token:
            entries.pop(token)
            removed = True
    if removed:
        save_tokens(entries, path)
    return removed


def list_tokens(path: 'Path | None' = None) -> list[dict]:
    """列出 token（只显示前后几位，避免把密钥打到终端历史里）。"""
    out = []
    for token, entry in load_tokens(path).items():
        masked = f"{token[:6]}…{token[-4:]}" if len(token) > 12 else "…"
        out.append({
            "name": entry.get("name", ""),
            "token": masked,
            "read": entry.get("read", []),
            "write": entry.get("write", []),
        })
    return out


class TokenStore:
    """带 mtime 热加载的 token 表。"""

    def __init__(self, path: 'Path | None' = None):
        self.path = path or tokens_file()
        self._entries: dict[str, dict] = {}
        self._mtime: float = -1.0
        self._lock = Lock()
        self.reload()

    def reload(self) -> None:
        self._entries = load_tokens(self.path)
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime = 0.0

    def _maybe_reload(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime != self._mtime:
            with self._lock:
                self.reload()

    def get(self, token: str) -> 'dict | None':
        self._maybe_reload()
        entry = self._entries.get(token)
        if entry is None:
            return None
        return {
            "name": entry.get("name", ""),
            "read": list(entry.get("read") or []),
            "write": list(entry.get("write") or []),
        }
