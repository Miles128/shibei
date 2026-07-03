"""Tokenizer 测试 — 中文分词与 Markdown 噪音过滤"""

from shibei.tokenizer import (
    is_noise_token,
    tokenize_text,
    tokenize_query,
    jieba_available,
)
from shibei.dreamer import extract_keywords


def test_is_noise_token_markdown_symbols():
    """Markdown 格式符号应被识别为噪音"""
    assert is_noise_token("#")
    assert is_noise_token("##")
    assert is_noise_token("###")
    assert is_noise_token("---")
    assert is_noise_token("***")
    assert is_noise_token(">")
    assert is_noise_token("   ")
    assert is_noise_token("123")


def test_is_noise_token_meaningful_words():
    """正常词不应被识别为噪音"""
    assert not is_noise_token("AI")
    assert not is_noise_token("中文")
    assert not is_noise_token("Agent")
    assert not is_noise_token("RAG")
    assert not is_noise_token("# AI 标题")  # 带内容，不是纯格式符号


def test_tokenize_text_filters_heading_marks():
    """tokenize_text 不应把 Markdown 标题符号编入索引"""
    text = "## AI 应用\n\n### 大模型\n\n这是正文。"
    tokens = tokenize_text(text)
    assert "##" not in tokens
    assert "###" not in tokens
    assert "#" not in tokens.split()
    # 正常词应保留
    assert "AI" in tokens or not jieba_available()
    assert "正文" in tokens


def test_tokenize_query_filters_heading_marks():
    """tokenize_query 不应把 Markdown 标题符号当查询词"""
    query = "## AI 应用"
    expr = tokenize_query(query)
    assert "##" not in expr
    assert "#" not in expr


def test_extract_keywords_filters_heading_marks():
    """extract_keywords 不应把 Markdown 标题符号当关键词"""
    text = "## AI 应用\n\n### 大模型\n\nAgent 技术正在改变 RAG。"
    kws = extract_keywords(text, top_n=10)
    assert "##" not in kws
    assert "###" not in kws
    assert "#" not in kws
