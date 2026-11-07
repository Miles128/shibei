"""多库 sidecar：/v1/brains、brain 路由、联邦召回。"""

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import yaml

from shibei.bm25 import BM25Index
from shibei.services import Shibei


def _make_brain(root: Path, name: str, text: str) -> Path:
    base = root / name
    db = base / "db"
    db.mkdir(parents=True, exist_ok=True)
    BM25Index(str(db / "fts5.db")).add_batch([
        (f"/{name}.md", f"/{name}.md", "", text, "", ""),
    ])
    cfg = base / "config.yaml"
    cfg.write_text(f"storage:\n  path: {db}\nsources: []\n", encoding="utf-8")
    return cfg


def _http(base: str, token: str, method: str, path: str, body=None):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(base + path, data=data, headers=headers, method=method)
    with urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


@pytest.fixture
def multi(tmp_path, monkeypatch):
    cfg_a = _make_brain(tmp_path, "a", "Redis 缓存穿透要用布隆过滤器。")
    cfg_b = _make_brain(tmp_path, "b", "Redis 集群分片与哨兵。")
    registry = tmp_path / "brains.yaml"
    registry.write_text(
        yaml.safe_dump({
            "default": {"config": str(cfg_a)},
            "work": {"config": str(cfg_b), "description": "工作"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHIBEI_BRAINS", str(registry))
    monkeypatch.setenv("SHIBEI_TOKENS", str(tmp_path / "tokens.yaml"))
    monkeypatch.setenv("SHIBEI_HOME", str(tmp_path / "home"))

    from shibei.sidecar import start_server

    httpd, info = start_server(str(cfg_a), multi=True)
    yield info, cfg_a, cfg_b
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def multi_scoped(tmp_path, monkeypatch):
    cfg_a = _make_brain(tmp_path, "a", "Redis 缓存穿透要用布隆过滤器。")
    cfg_b = _make_brain(tmp_path, "b", "Redis 集群分片与哨兵。")
    registry = tmp_path / "brains.yaml"
    registry.write_text(
        yaml.safe_dump({
            "default": {"config": str(cfg_a)},
            "work": {"config": str(cfg_b)},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHIBEI_BRAINS", str(registry))
    tokens_path = tmp_path / "tokens.yaml"
    monkeypatch.setenv("SHIBEI_TOKENS", str(tokens_path))
    monkeypatch.setenv("SHIBEI_HOME", str(tmp_path / "home"))

    from shibei.tokens import create_token

    reader, _ = create_token("reader", read=["work"], write=[], path=tokens_path)

    from shibei.sidecar import start_server

    httpd, info = start_server(str(cfg_a), multi=True)
    yield info, reader
    httpd.shutdown()
    httpd.server_close()


def test_health_reports_brains(multi):
    info, _, _ = multi
    status, body = _http(info["base_url"], info["token"], "GET", "/v1/health")
    assert status == 200
    assert body["multi"] is True
    assert body["brains"] == ["default", "work"]


def test_brains_endpoint(multi):
    info, _, _ = multi
    status, body = _http(info["base_url"], info["token"], "GET", "/v1/brains")
    assert status == 200
    assert body["default"] == "default"
    names = {entry["name"]: entry for entry in body["brains"]}
    assert set(names) == {"default", "work"}
    assert names["work"]["description"] == "工作"
    assert names["default"]["chunks"] == 1
    assert names["default"]["default"] is True


def test_recall_routes_to_named_brain(multi):
    info, _, _ = multi
    _, body = _http(
        info["base_url"], info["token"], "POST", "/v1/recall",
        {"brain": "work", "query": "Redis", "trace": True},
    )
    assert body["results"][0]["brain"] == "work"
    assert "federated" not in body


def test_recall_wildcard_federates(multi):
    info, _, _ = multi
    _, body = _http(
        info["base_url"], info["token"], "POST", "/v1/recall",
        {"brain": "*", "query": "Redis", "limit": 5},
    )
    assert body["federated"] is True
    assert body["brains"] == ["default", "work"]
    assert body["trace"]["federated"]["k"] == 60
    assert body["results"]


def test_recall_brain_list_subset(multi):
    info, _, _ = multi
    _, body = _http(
        info["base_url"], info["token"], "POST", "/v1/recall",
        {"brains": ["work"], "query": "Redis"},
    )
    assert body.get("federated") is not True
    assert body["brain"] == "work"


def test_unknown_brain_returns_404(multi):
    info, _, _ = multi
    with pytest.raises(HTTPError) as exc:
        _http(
            info["base_url"], info["token"], "POST", "/v1/recall",
            {"brain": "nope", "query": "Redis"},
        )
    assert exc.value.code == 404


def test_writes_are_isolated_per_brain(multi):
    info, cfg_a, cfg_b = multi
    _http(
        info["base_url"], info["token"], "POST", "/v1/remember",
        {"brain": "work", "fact": "只在 work 库", "category": "project"},
    )
    assert Shibei(str(cfg_b)).list_memories()
    assert Shibei(str(cfg_a)).list_memories() == []


def test_mcp_handlers_pass_brain(multi):
    from shibei.client import ShibeiClient
    from shibei.server import _handle_brains, _handle_context, _handle_search

    info, _, _ = multi
    client = ShibeiClient.__new__(ShibeiClient)
    client._base = info["base_url"]
    client._token = info["token"]
    client.brain = None

    listed = _handle_brains(client, {})
    assert {entry["name"] for entry in listed["brains"]} == {"default", "work"}

    hit = _handle_search(client, {"query": "Redis", "brain": "work"})
    assert hit["results"][0]["brain"] == "work"

    pack = _handle_context(client, {"message": "Redis", "brain": "*"})
    assert pack["federated"] is True
    assert pack["brains"] == ["default", "work"]


# ---------- 按库 token ----------


def test_scoped_token_can_only_read_its_brain(multi_scoped):
    info, reader = multi_scoped

    status, listing = _http(info["base_url"], reader, "GET", "/v1/brains")
    assert status == 200
    assert {entry["name"] for entry in listing["brains"]} == {"work"}

    status, body = _http(
        info["base_url"], reader, "POST", "/v1/recall",
        {"brain": "work", "query": "Redis"},
    )
    assert status == 200
    assert body["results"][0]["brain"] == "work"


def test_scoped_token_cannot_touch_other_brain(multi_scoped):
    info, reader = multi_scoped
    with pytest.raises(HTTPError) as exc:
        _http(
            info["base_url"], reader, "POST", "/v1/recall",
            {"brain": "default", "query": "Redis"},
        )
    assert exc.value.code == 403


def test_scoped_token_is_read_only(multi_scoped):
    info, reader = multi_scoped
    with pytest.raises(HTTPError) as exc:
        _http(
            info["base_url"], reader, "POST", "/v1/remember",
            {"brain": "work", "fact": "不该写进去"},
        )
    assert exc.value.code == 403


def test_unknown_token_is_401(multi_scoped):
    info, _ = multi_scoped
    with pytest.raises(HTTPError) as exc:
        _http(info["base_url"], "bogus-token", "GET", "/v1/brains")
    assert exc.value.code == 401


def test_admin_token_can_write(multi):
    info, _, cfg_b = multi
    status, _ = _http(
        info["base_url"], info["token"], "POST", "/v1/remember",
        {"brain": "work", "fact": "admin 写入", "category": "project"},
    )
    assert status == 200
    assert Shibei(str(cfg_b)).list_memories()


# ---------- 运行时注册 ----------


def test_runtime_register_and_remove_brain(multi):
    info, _, _ = multi
    admin = info["token"]

    status, created = _http(
        info["base_url"], admin, "POST", "/v1/brains",
        {"name": "fresh", "create": True, "description": "临时库"},
    )
    assert status == 200
    assert created["ok"] is True

    _, listing = _http(info["base_url"], admin, "GET", "/v1/brains?stats=0")
    names = {entry["name"] for entry in listing["brains"]}
    assert "fresh" in names

    _, recalled = _http(
        info["base_url"], admin, "POST", "/v1/recall",
        {"brain": "fresh", "query": "任意"},
    )
    assert recalled["total"] == 0

    _, removed = _http(
        info["base_url"], admin, "POST", "/v1/brains/remove", {"name": "fresh"}
    )
    assert removed["ok"] is True
    _, listing = _http(info["base_url"], admin, "GET", "/v1/brains?stats=0")
    assert "fresh" not in {entry["name"] for entry in listing["brains"]}


def test_scoped_token_cannot_register_brain(multi_scoped):
    info, reader = multi_scoped
    with pytest.raises(HTTPError) as exc:
        _http(
            info["base_url"], reader, "POST", "/v1/brains",
            {"name": "sneaky", "create": True},
        )
    assert exc.value.code == 403


def test_invalid_brain_name_rejected(multi):
    info, _, _ = multi
    with pytest.raises(HTTPError) as exc:
        _http(
            info["base_url"], info["token"], "POST", "/v1/brains",
            {"name": "bad name!", "create": True},
        )
    assert exc.value.code == 400
