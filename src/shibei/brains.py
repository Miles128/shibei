"""多库（brain）注册表 — 一个 sidecar 服务多个知识库。

注册表默认在 ``~/.shibei/brains.yaml``：

.. code-block:: yaml

    default:
      config: /abs/path/config.yaml
    work:
      config: /abs/path/work.yaml
      description: 工作笔记
"""

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Any

import yaml

DEFAULT_HOME = Path(os.environ.get("SHIBEI_HOME", str(Path.home() / ".shibei")))


def home_dir() -> Path:
    """运行时读取 SHIBEI_HOME（测试和脚本可以晚于 import 设置）。"""
    return Path(os.environ.get("SHIBEI_HOME", str(Path.home() / ".shibei")))


def brains_file() -> Path:
    custom = os.environ.get("SHIBEI_BRAINS")
    if custom:
        return Path(custom).expanduser()
    return home_dir() / "brains.yaml"


def load_brains(path: 'Path | None' = None) -> dict[str, dict]:
    """读取注册表；文件不存在或损坏时返回空字典。"""
    path = path or brains_file()
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    entries: dict[str, dict] = {}
    for name, value in data.items():
        if isinstance(value, str):
            entries[str(name)] = {"config": value}
        elif isinstance(value, dict) and (value.get("config") or value.get("url")):
            entries[str(name)] = {
                "config": str(value.get("config") or ""),
                "url": str(value.get("url") or ""),
                "token": str(value.get("token") or ""),
                "brain": str(value.get("brain") or ""),
                "timeout": float(value.get("timeout") or 30.0),
                "description": str(value.get("description") or ""),
            }
    return entries


def save_brains(entries: dict[str, dict], path: 'Path | None' = None) -> Path:
    path = path or brains_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        name: {key: value for key, value in entry.items() if value}
        for name, entry in entries.items()
    }
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def register_brain(
    name: str,
    config_path: str,
    description: str = "",
    path: 'Path | None' = None,
) -> Path:
    if not name or not config_path:
        raise ValueError("name 与 config_path 都不能为空")
    entries = load_brains(path)
    entries[name] = {
        "config": str(Path(config_path).expanduser().resolve()),
        "description": description,
    }
    return save_brains(entries, path)


def register_remote(
    name: str,
    url: str,
    token: str = "",
    brain: str = "",
    description: str = "",
    path: 'Path | None' = None,
) -> Path:
    if not name or not url:
        raise ValueError("name 与 url 都不能为空")
    entries = load_brains(path)
    entries[name] = {
        "config": "",
        "url": url,
        "token": token,
        "brain": brain,
        "timeout": 30.0,
        "description": description,
    }
    return save_brains(entries, path)


def unregister_brain(name: str, path: 'Path | None' = None) -> bool:
    entries = load_brains(path)
    if name not in entries:
        return False
    entries.pop(name)
    save_brains(entries, path)
    return True


def _abs(path: str) -> str:
    return str(Path(path).expanduser().resolve())


class Brains:
    """进程内的多库容器：按需实例化内核，并给每个库一把独立的写锁。"""

    def __init__(self, default_config: str, registry: 'Path | None' = None):
        self.registry = registry or brains_file()
        self.default_config = _abs(default_config)
        self._entries: dict[str, dict] = {}
        self._instances: dict[str, Any] = {}
        self._instance_paths: dict[str, str] = {}
        self._locks: dict[str, Lock] = {}
        self._lock = Lock()
        self._mtime: float = -1.0
        self._default_name: str = "default"
        self.reload()

    # ---------- 注册表 ----------

    def reload(self) -> None:
        entries = load_brains(self.registry)
        if not entries:
            entries = {"default": {"config": self.default_config}}

        if "default" in entries:
            self._default_name = "default"
        else:
            # 启动配置已经用某个名字注册过：直接用那个名字，避免同一库出现两个别名
            matched = next(
                (
                    name
                    for name, entry in entries.items()
                    if entry.get("config")
                    and _abs(entry["config"]) == self.default_config
                ),
                None,
            )
            if matched is not None:
                self._default_name = matched
            else:
                entries["default"] = {"config": self.default_config}
                self._default_name = "default"

        self._entries = entries
        try:
            self._mtime = self.registry.stat().st_mtime
        except OSError:
            self._mtime = 0.0

    def _maybe_reload(self) -> None:
        """注册表改动后免重启生效。"""
        try:
            mtime = self.registry.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime != self._mtime:
            self.reload()

    def names(self) -> list[str]:
        self._maybe_reload()
        return sorted(self._entries, key=lambda name: (name != "default", name))

    @property
    def default_name(self) -> str:
        return self._default_name

    def path_of(self, name: str) -> 'str | None':
        if name == "default":
            name = self._default_name
        entry = self._entries.get(name)
        if not entry or not entry.get("config"):
            return None
        return _abs(entry["config"])

    def config_path_for(self, config_path: str) -> 'str | None':
        """反查：这个配置文件属于哪个 brain。"""
        target = _abs(config_path)
        for name in self.names():
            if self.path_of(name) == target:
                return name
        return None

    # ---------- 实例 ----------

    def get(self, name: str):
        """按名称取内核；未注册的名字会先重读一次注册表（支持热添加）。"""
        self._maybe_reload()
        if name == "default":
            name = self._default_name
        if name not in self._entries:
            self.reload()
            if name == "default":
                name = self._default_name
        entry = self._entries.get(name)
        if entry is None:
            raise KeyError(name)
        if entry.get("url"):
            return self._get_remote(name, entry)
        key = _abs(entry["config"])
        with self._lock:
            instance = self._instances.get(name)
            if instance is not None and self._instance_paths.get(name) != key:
                # 同名库换了配置文件：重建
                self._instances.pop(name, None)
                self._instance_paths.pop(name, None)
                instance = None
            if instance is None:
                from .services import Shibei

                instance = Shibei(entry["config"])
                self._instances[name] = instance
                self._instance_paths[name] = key
            return instance

    def _get_remote(self, name: str, entry: dict):
        from .remote import RemoteBrain

        key = "remote:" + entry["url"]
        with self._lock:
            instance = self._instances.get(name)
            if instance is None or self._instance_paths.get(name) != key:
                instance = RemoteBrain(
                    name=name,
                    url=entry["url"],
                    token=entry.get("token") or "",
                    brain=entry.get("brain") or "",
                    timeout=float(entry.get("timeout") or 30.0),
                )
                self._instances[name] = instance
                self._instance_paths[name] = key
            return instance

    def lock_for(self, name: str) -> Lock:
        with self._lock:
            lock = self._locks.get(name)
            if lock is None:
                lock = self._locks[name] = Lock()
            return lock

    def pairs(self) -> list[tuple[str, Any]]:
        """所有可用库；单个库初始化失败时跳过，不拖垮整个 daemon。"""
        out: list[tuple[str, Any]] = []
        for name in self.names():
            try:
                out.append((name, self.get(name)))
            except Exception:  # noqa: BLE001
                continue
        return out

    def describe(self, with_counts: bool = True) -> list[dict]:
        out: list[dict] = []
        for name in self.names():
            entry = self._entries[name]
            if entry.get("url"):
                info = {
                    "name": name,
                    "remote": True,
                    "url": entry["url"],
                    "description": entry.get("description", ""),
                    "default": name == self.default_name,
                }
                try:
                    if with_counts:
                        info["chunks"] = self.get(name).list_chunks(limit=0)["total"]
                except Exception as exc:  # noqa: BLE001
                    info["error"] = str(exc)
                out.append(info)
                continue
            info = {
                "name": name,
                "config_path": _abs(entry["config"]),
                "description": entry.get("description", ""),
                "default": name == self.default_name,
            }
            try:
                brain = self.get(name)
                info["db_path"] = str(brain.config.db_path_expanded)
                if with_counts:
                    info["chunks"] = brain.list_chunks(limit=0)["total"]
            except Exception as exc:  # noqa: BLE001
                info["error"] = str(exc)
            out.append(info)
        return out
