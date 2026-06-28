"""Profile 管理测试"""

import tempfile
from pathlib import Path

from shibei.profile import (
    create_profile, delete_profile, list_profiles,
    profile_exists, get_profile_path, resolve_config_path,
)


def test_create_and_list_profile(tmp_path, monkeypatch):
    """创建和列出 profile"""
    monkeypatch.setattr("shibei.profile.PROFILES_DIR", tmp_path)

    create_profile("test-profile")
    profiles = list_profiles()
    assert "test-profile" in profiles
    assert profile_exists("test-profile")


def test_delete_profile(tmp_path, monkeypatch):
    """删除 profile"""
    monkeypatch.setattr("shibei.profile.PROFILES_DIR", tmp_path)

    create_profile("to-delete")
    assert profile_exists("to-delete")

    delete_profile("to-delete")
    assert not profile_exists("to-delete")


def test_create_duplicate_profile_raises(tmp_path, monkeypatch):
    """重复创建抛异常"""
    monkeypatch.setattr("shibei.profile.PROFILES_DIR", tmp_path)

    create_profile("dup")
    try:
        create_profile("dup")
        assert False, "Should have raised FileExistsError"
    except FileExistsError:
        pass


def test_delete_nonexistent_raises(tmp_path, monkeypatch):
    """删除不存在的 profile 抛异常"""
    monkeypatch.setattr("shibei.profile.PROFILES_DIR", tmp_path)

    try:
        delete_profile("nonexistent")
        assert False, "Should have raised FileNotFoundError"
    except FileNotFoundError:
        pass


def test_resolve_config_with_profile(tmp_path, monkeypatch):
    """通过 profile 名解析配置路径"""
    monkeypatch.setattr("shibei.profile.PROFILES_DIR", tmp_path)

    create_profile("my-profile")
    path = resolve_config_path(profile="my-profile")
    assert path == str(get_profile_path("my-profile"))


def test_resolve_config_with_explicit(tmp_path):
    """显式 --config 优先于 profile"""
    path = resolve_config_path(config="/explicit/path.yaml", profile="ignored")
    assert path == "/explicit/path.yaml"


def test_resolve_config_default():
    """无参数时用默认 config.yaml"""
    path = resolve_config_path()
    assert path == "config.yaml"
