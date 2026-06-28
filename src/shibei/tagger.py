"""可配置标签推断器"""

import re
from pathlib import Path

from .config import TaggingConfig, TagRule


class Tagger:
    """基于正则规则的标签推断器。

    规则按顺序匹配，命中即停止。检查文件路径 + 内容前 500 字。
    """

    def __init__(self, cfg: TaggingConfig):
        self.rules: list[tuple[re.Pattern, str]] = []
        for rule in cfg.rules:
            self.rules.append((re.compile(rule.pattern, re.IGNORECASE), rule.tag))

    def infer(self, path: Path, content: str = "") -> list[str]:
        """返回匹配到的标签列表（去重）"""
        search_text = str(path) + " " + content[:500]
        tags = []
        for pattern, tag in self.rules:
            if pattern.search(search_text):
                tags.append(tag)
        return list(dict.fromkeys(tags))  # 去重保序
