"""Shibei 服务层测试"""

import tempfile
from pathlib import Path

from shibei.config import Config, StorageConfig
from shibei.services import Shibei
from shibei.llm import LLMConfig, LLMClient


def test_remember_extracts_facts_with_llm():
    """LLM 启用时 remember 会自动提取多条事实"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        cfg = Config(
            storage=StorageConfig(path=str(db_path)),
        )

        brain = Shibei.__new__(Shibei)
        brain.config = cfg
        llm = LLMClient(LLMConfig(enabled=True, provider="openai-compatible", api_key="sk-test"))
        llm.chat = lambda prompt, system_prompt=None: '["用户喜欢 Python", "用户用 Mac"]'
        brain.llm = llm

        result = brain.remember("我喜欢用 Python，电脑是 Mac")
        assert "extracted_facts" in result
        assert len(result["extracted_facts"]) == 2


def test_remember_degrades_when_llm_fails():
    """LLM 提取失败时仍写入原 fact"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        cfg = Config(
            storage=StorageConfig(path=str(db_path)),
        )

        brain = Shibei.__new__(Shibei)
        brain.config = cfg
        llm = LLMClient(LLMConfig(enabled=True, provider="openai-compatible", api_key="sk-test"))
        llm.chat = lambda prompt, system_prompt=None: (_ for _ in ()).throw(
            RuntimeError("mock failure")
        )
        brain.llm = llm

        result = brain.remember("我喜欢用 Python")
        assert result["fact"] == "我喜欢用 Python"
        assert "extracted_facts" not in result
