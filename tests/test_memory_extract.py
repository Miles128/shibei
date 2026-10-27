"""L1 抽取解析测试。"""

from shibei.memory_extract import parse_extraction


def test_parse_structured_json():
    raw = """[
      {
        "scene_name": "我在和四海做 Lumina 的 dsh 迁移",
        "memories": [
          {
            "content": "用户决定用 dsh 原生 workflow 脚本替代自研 Skill DAG",
            "type": "instruction",
            "priority": 90,
            "confidence": 0.9,
            "source_message_ids": ["msg-1", "msg-2"]
          }
        ]
      }
    ]"""
    items = parse_extraction(raw)
    assert len(items) == 1
    item = items[0]
    assert item["memory_type"] == "instruction"
    assert item["priority"] == 90
    assert item["confidence"] == 0.9
    assert item["source_message_ids"] == "msg-1,msg-2"
    assert "dsh 迁移" in item["scene_name"]


def test_parse_markdown_fence_and_defaults():
    raw = """```json
    [{"scene_name": "工作", "memories": [{"content": "用户喜欢简洁回答"}]}]
    ```"""
    items = parse_extraction(raw, default_message_id="turn-7")
    assert len(items) == 1
    assert items[0]["memory_type"] == "episodic"
    assert items[0]["priority"] == 50
    assert items[0]["source_message_ids"] == "turn-7"


def test_parse_clamps_and_skips_short():
    raw = """[
      {"memories": [
        {"content": "有效事实一条", "type": "weird", "priority": 999, "confidence": 3},
        {"content": "短", "type": "persona"}
      ]}
    ]"""
    items = parse_extraction(raw)
    assert len(items) == 1
    assert items[0]["priority"] == 100
    assert items[0]["confidence"] == 1.0
    assert items[0]["memory_type"] == "episodic"


def test_parse_invalid_returns_empty():
    assert parse_extraction("not json") == []
    assert parse_extraction("") == []
