"""LLM 客户端测试"""

import json
import urllib.error

import pytest

from shibei.llm import LLMClient, LLMOptions


def _make_response(content: str, model: str = "gpt-4o-mini") -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_llm_client_chat_success(monkeypatch):
    """正常返回 assistant 内容"""

    def fake_urlopen(request, timeout=None):
        assert request.headers["Authorization"] == "Bearer sk-test"
        payload = json.loads(request.data)
        assert payload["model"] == "test-model"
        assert payload["messages"][0]["content"] == "hello"
        return _FakeResponse(_make_response("  world  "))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("SHIBEI_LLM_API_KEY", "sk-test")

    client = LLMClient(LLMOptions(base_url="https://api.test/v1", model="test-model"))
    assert client.chat([{"role": "user", "content": "hello"}]) == "world"


def test_llm_client_missing_api_key():
    """未配置 API key 时抛出 ValueError"""
    client = LLMClient(LLMOptions(api_key=None))
    with pytest.raises(ValueError, match="SHIBEI_LLM_API_KEY"):
        client.chat([{"role": "user", "content": "hello"}])


def test_llm_client_http_error(monkeypatch):
    """HTTP 错误应抛出并记录日志"""

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            url="https://api.test/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs={},  # type: ignore[arg-type]
            fp=None,
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("SHIBEI_LLM_API_KEY", "sk-test")

    client = LLMClient(LLMOptions())
    with pytest.raises(urllib.error.HTTPError):
        client.chat([{"role": "user", "content": "hello"}])


def test_llm_client_malformed_response(monkeypatch):
    """响应格式异常时抛出 ValueError"""

    def fake_urlopen(request, timeout=None):
        return _FakeResponse(json.dumps({"choices": []}).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("SHIBEI_LLM_API_KEY", "sk-test")

    client = LLMClient(LLMOptions())
    with pytest.raises(ValueError):
        client.chat([{"role": "user", "content": "hello"}])
