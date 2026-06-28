"""LLM 客户端测试"""

import pytest

from shibei.llm import LLMConfig, LLMClient


def test_llm_ready_disabled():
    """未启用时 ready 为 False"""
    cfg = LLMConfig(enabled=False)
    client = LLMClient(cfg)
    assert client.enabled is False
    assert client.ready is False


def test_llm_ready_no_key():
    """没配 key 时不 ready"""
    cfg = LLMConfig(enabled=True, provider="openai-compatible", api_key="")
    client = LLMClient(cfg)
    assert client.ready is False


def test_llm_ready_with_key():
    """配了 key 才 ready"""
    cfg = LLMConfig(enabled=True, provider="openai-compatible", api_key="sk-test")
    client = LLMClient(cfg)
    assert client.ready is True


def test_llm_chat_raises_when_not_ready():
    """未 ready 时调用 chat 抛异常"""
    cfg = LLMConfig(enabled=False)
    client = LLMClient(cfg)
    with pytest.raises(RuntimeError):
        client.chat("hello")


def test_llm_enhance_insights_mock():
    """mock chat 后 enhance_insights 返回结构化洞察"""
    cfg = LLMConfig(enabled=True, provider="openai-compatible", api_key="sk-test")
    client = LLMClient(cfg)
    client.chat = lambda prompt, system_prompt=None: '["洞察A", "洞察B"]'

    insights = client.enhance_insights(
        [{"text": "人工智能正在改变世界"}, {"text": "大模型技术突破"}],
        ["人工智能", "大模型"],
    )
    assert insights == ["洞察A", "洞察B"]


def test_llm_rewrite_query_mock():
    """mock chat 后 rewrite_query 返回改写结果"""
    cfg = LLMConfig(enabled=True, provider="openai-compatible", api_key="sk-test")
    client = LLMClient(cfg)
    client.chat = lambda prompt, system_prompt=None: "人工智能 大模型"

    assert client.rewrite_query("啥是人工智能啊") == "人工智能 大模型"


def test_llm_extract_facts_mock():
    """mock chat 后 extract_facts 返回事实列表"""
    cfg = LLMConfig(enabled=True, provider="openai-compatible", api_key="sk-test")
    client = LLMClient(cfg)
    client.chat = lambda prompt, system_prompt=None: '["用户喜欢 Python", "用户用 Mac"]'

    facts = client.extract_facts("我喜欢用 Python，电脑是 Mac")
    assert "用户喜欢 Python" in facts
    assert "用户用 Mac" in facts


def test_extract_json_list_fallback():
    """LLM 输出非 JSON 时按行解析"""
    text = "- 事实1\n- 事实2"
    assert LLMClient._extract_json_list(text) == ["事实1", "事实2"]


def test_extract_json_list_markdown_code_block():
    """LLM 输出 markdown JSON 代码块时正确提取"""
    text = '```json\n["a", "b"]\n```'
    assert LLMClient._extract_json_list(text) == ["a", "b"]
