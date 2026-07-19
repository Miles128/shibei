"""Chunk 摘要生成器测试"""

from shibei.config import LLMConfig
from shibei.llm import LLMOptions
from shibei.summarizer import ChunkSummarizer


def test_summarizer_disabled():
    """未启用时直接返回空字符串，不调用网络"""
    cfg = LLMConfig(enabled=False)
    summarizer = ChunkSummarizer(cfg)
    assert summarizer.summarize("任意内容") == ""


def test_summarizer_empty_content():
    """空内容返回空字符串"""
    cfg = LLMConfig(enabled=True)
    summarizer = ChunkSummarizer(cfg)
    assert summarizer.summarize("") == ""
    assert summarizer.summarize("   ") == ""


def test_summarizer_success(monkeypatch):
    """LLM 调用成功时返回清理后的摘要"""
    cfg = LLMConfig(enabled=True, max_content_length=100, max_summary_length=20)
    summarizer = ChunkSummarizer(cfg)

    def fake_chat(messages):
        assert "100字以内" not in messages[0]["content"]
        assert "20 字以内" in messages[0]["content"]
        return '"  这是摘要  "'

    monkeypatch.setattr(summarizer._client, "chat", fake_chat)
    assert summarizer.summarize("这是需要摘要的原文，内容比较长。") == "这是摘要"


def test_summarizer_truncates_input(monkeypatch):
    """超长输入按 max_content_length 截断"""
    cfg = LLMConfig(enabled=True, max_content_length=10)
    summarizer = ChunkSummarizer(cfg)

    captured = []

    def fake_chat(messages):
        captured.append(messages[0]["content"])
        return "摘要"

    monkeypatch.setattr(summarizer._client, "chat", fake_chat)
    summarizer.summarize("一二三四五六七八九十十一十二")
    # prompt 中 content 部分应只保留前 10 个字符
    assert "一二三四五六七八九十" in captured[0]
    assert "十一十二" not in captured[0]


def test_summarizer_failure_degrades(monkeypatch):
    """LLM 调用失败时降级为空字符串，不抛异常"""
    cfg = LLMConfig(enabled=True)
    summarizer = ChunkSummarizer(cfg)

    def fake_chat(messages):
        raise ConnectionError("网络错误")

    monkeypatch.setattr(summarizer._client, "chat", fake_chat)
    assert summarizer.summarize("内容") == ""


def test_summarizer_options_passed():
    """配置项正确传递给 LLMClient"""
    cfg = LLMConfig(
        enabled=True,
        base_url="https://custom.example/v1",
        model="custom-model",
        timeout=5.0,
        temperature=0.7,
        max_tokens=200,
    )
    summarizer = ChunkSummarizer(cfg)
    assert summarizer._client is not None
    opts: LLMOptions = summarizer._client.options
    assert opts.base_url == "https://custom.example/v1"
    assert opts.model == "custom-model"
    assert opts.timeout == 5.0
    assert opts.temperature == 0.7
    assert opts.max_tokens == 200
