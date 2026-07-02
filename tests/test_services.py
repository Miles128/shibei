"""Shibei 服务层测试"""

import tempfile
from pathlib import Path

from shibei.config import Config, StorageConfig
from shibei.services import Shibei


def test_remember_writes_fact():
    """remember 写入单条事实记忆"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir)
        cfg = Config(
            storage=StorageConfig(path=str(db_path)),
        )

        brain = Shibei.__new__(Shibei)
        brain.config = cfg

        result = brain.remember("我喜欢用 Python")
        assert result["fact"] == "我喜欢用 Python"
        assert result["source"] == "agent"
        assert "agent" in result["tags"]
