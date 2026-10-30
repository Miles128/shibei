"""LLM preset 解析测试。"""

from shibei.config import load_config


def write_cfg(tmp_path, body: str):
    p = tmp_path / "config.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_deepseek_preset(tmp_path):
    p = write_cfg(tmp_path, "llm:\n  preset: deepseek\n")
    cfg = load_config(str(p))
    assert cfg.llm.base_url == "https://api.deepseek.com/v1"
    assert cfg.llm.model == "deepseek-chat"


def test_ollama_preset(tmp_path):
    p = write_cfg(tmp_path, "llm:\n  preset: ollama\n")
    cfg = load_config(str(p))
    assert cfg.llm.base_url.startswith("http://127.0.0.1:11434")
    assert cfg.llm.model


def test_custom_preset_keeps_explicit_values(tmp_path):
    p = write_cfg(
        tmp_path,
        "llm:\n  preset: custom\n  base_url: https://example.com/v1\n  model: my-model\n",
    )
    cfg = load_config(str(p))
    assert cfg.llm.base_url == "https://example.com/v1"
    assert cfg.llm.model == "my-model"
