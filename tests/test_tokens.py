"""按库 token 的存取与热加载。"""

from pathlib import Path

from shibei.tokens import (
    TokenStore,
    create_token,
    list_tokens,
    load_tokens,
    revoke_token,
)


def test_create_load_list_revoke(tmp_path):
    path = tmp_path / "tokens.yaml"
    token, entry = create_token("cursor", read=["*"], write=["work"], path=path)

    assert entry == {"name": "cursor", "read": ["*"], "write": ["work"]}
    loaded = load_tokens(path)
    assert loaded[token]["name"] == "cursor"

    masked = list_tokens(path)[0]
    assert masked["token"] != token
    assert masked["token"].startswith(token[:6])
    assert masked["write"] == ["work"]

    assert revoke_token("cursor", path=path) is True
    assert load_tokens(path) == {}
    assert revoke_token("cursor", path=path) is False


def test_mapping_form_defaults_to_read_only(tmp_path):
    path = tmp_path / "tokens.yaml"
    path.write_text(
        "tokens:\n  abc: {name: ro, read: [work]}\n", encoding="utf-8"
    )
    entries = load_tokens(path)
    assert entries["abc"] == {"name": "ro", "read": ["work"], "write": []}


def test_string_read_write_is_split(tmp_path):
    path = tmp_path / "tokens.yaml"
    path.write_text(
        "tokens:\n"
        "  - token: t\n"
        "    name: x\n"
        "    read: '*'\n"
        "    write: a,b\n",
        encoding="utf-8",
    )
    entries = load_tokens(path)
    assert entries["t"]["read"] == ["*"]
    assert entries["t"]["write"] == ["a", "b"]


def test_store_hot_reload(tmp_path):
    path = tmp_path / "tokens.yaml"
    store = TokenStore(path)
    assert store.get("missing") is None

    token, _ = create_token("late", read=["work"], path=path)
    assert store.get(token)["read"] == ["work"]

    revoke_token("late", path=path)
    assert store.get(token) is None


def test_file_permissions_are_600(tmp_path):
    path = tmp_path / "tokens.yaml"
    create_token("x", read=["*"], path=path)
    assert oct(path.stat().st_mode)[-3:] == "600"
