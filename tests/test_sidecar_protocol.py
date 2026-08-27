"""Sidecar HTTP 协议：与内核直连返回同一套字段。"""

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import yaml

from shibei.services import Shibei


def _write_config(tmp_path: Path) -> Path:
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "redis.md").write_text("## Redis\n缓存穿透要用布隆过滤器。\n", encoding="utf-8")
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


def _http(base: str, token: str, method: str, path: str, body=None, auth=True):
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(base + path, data=data, headers=headers, method=method)
    with urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


@pytest.fixture
def sidecar_server(tmp_path):
    from shibei.sidecar import start_server

    cfg_path = _write_config(tmp_path)
    httpd, info = start_server(str(cfg_path))
    yield info, cfg_path
    httpd.shutdown()
    httpd.server_close()


def test_health_requires_token(sidecar_server):
    info, _ = sidecar_server
    with pytest.raises(HTTPError) as exc:
        _http(info["base_url"], info["token"], "GET", "/v1/health", auth=False)
    assert exc.value.code == 401


def test_health_ok(sidecar_server):
    info, cfg_path = sidecar_server
    status, body = _http(info["base_url"], info["token"], "GET", "/v1/health")
    assert status == 200
    assert body["ok"] is True
    assert body["config_path"] == str(Path(cfg_path).resolve())
    assert body["pid"] > 0


def test_remember_then_context_matches_kernel(sidecar_server):
    info, cfg_path = sidecar_server
    fact = "用户偏好 Python 而不是 Go"
    _, remembered = _http(
        info["base_url"],
        info["token"],
        "POST",
        "/v1/remember",
        {"fact": fact, "category": "preference", "tags": "tech"},
    )
    assert remembered["fact"] == fact

    _, pack = _http(
        info["base_url"],
        info["token"],
        "POST",
        "/v1/context",
        {"message": "后端用什么语言", "query": "Python Go 偏好"},
    )
    texts = [m.get("text") or m.get("fact", "") for m in pack.get("memories", [])]
    assert any("Python" in t for t in texts)

    direct = Shibei(str(cfg_path)).context("后端用什么语言", query="Python Go 偏好")
    assert pack["total"] == direct["total"]


def test_persona_after_remember(sidecar_server):
    info, _ = sidecar_server
    _http(
        info["base_url"],
        info["token"],
        "POST",
        "/v1/remember",
        {"fact": "用户住在上海", "category": "user"},
    )
    status, persona = _http(info["base_url"], info["token"], "GET", "/v1/persona")
    assert status == 200
    assert persona["total"] >= 1
    facts = []
    for items in persona["categories"].values():
        facts.extend(i["fact"] if isinstance(i, dict) else i for i in items)
    assert any("上海" in f for f in facts)


def test_import_then_search(sidecar_server):
    info, _ = sidecar_server
    _, imported = _http(info["base_url"], info["token"], "POST", "/v1/import", {})
    assert imported["files"] >= 1
    _, searched = _http(
        info["base_url"],
        info["token"],
        "POST",
        "/v1/search",
        {"query": "Redis 布隆"},
    )
    assert searched["total"] >= 1
    assert "布隆" in searched["results"][0]["text"]
