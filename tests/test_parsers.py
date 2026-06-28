"""文件解析器测试"""

import tempfile
from pathlib import Path

from shibei.parsers import parse_file, SUPPORTED_EXTENSIONS


def test_parse_md():
    """Markdown 解析"""
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
        f.write("# Title\n\nHello world.")
        f.flush()
        result = parse_file(Path(f.name))
    assert "Title" in result
    assert "Hello world" in result


def test_parse_txt():
    """纯文本解析"""
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
        f.write("Just plain text.\nLine two.")
        f.flush()
        result = parse_file(Path(f.name))
    assert "Just plain text" in result


def test_parse_csv():
    """CSV 解析"""
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as f:
        f.write("name,age\nAlice,30\nBob,25\n")
        f.flush()
        result = parse_file(Path(f.name))
    assert "name" in result
    assert "Alice" in result
    assert "30" in result


def test_supported_extensions():
    """支持的扩展名列表"""
    assert ".md" in SUPPORTED_EXTENSIONS
    assert ".txt" in SUPPORTED_EXTENSIONS
    assert ".docx" in SUPPORTED_EXTENSIONS
    assert ".xlsx" in SUPPORTED_EXTENSIONS
    assert ".csv" in SUPPORTED_EXTENSIONS
    assert len(SUPPORTED_EXTENSIONS) >= 5


def test_parse_unsupported():
    """不支持的格式抛异常"""
    try:
        parse_file(Path("/tmp/test.xyz"))
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Unsupported" in str(e)
