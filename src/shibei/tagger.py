"""标签推断 — 支持路径 glob 规则与 regex 规则。"""

import fnmatch
import re
from pathlib import Path

from .config import Config, PathTagRule, TagRule, TaggingConfig


class Tagger:
    """根据文件路径 + 内容推断标签。

    兼容两种传入方式：
      - Tagger(config)  # Config 对象
      - Tagger(config.tagging)  # TaggingConfig 对象
    """

    def __init__(self, config: 'Config | TaggingConfig'):
        self.cfg = config.tagging if hasattr(config, "tagging") else config

    def infer(self, file_path: Path, content: str = "") -> list[str]:
        """返回该文件应打上的标签列表（去重、有序）。"""
        tags: list[str] = []
        seen = set()

        # 1. 路径规则优先（更确定、零维护）
        for rule in self.cfg.path_rules:
            if not rule.pattern:
                continue
            matched_tags = self._match_path_rule(rule, file_path)
            for tag in matched_tags:
                if tag and tag not in seen:
                    seen.add(tag)
                    tags.append(tag)

        # 2. regex 规则兜底（兼容旧配置）
        text = f"{file_path}\n{content[:500]}"
        for rule in self.cfg.rules:
            if not rule.pattern:
                continue
            try:
                if re.search(rule.pattern, text, re.IGNORECASE):
                    tag = rule.tag.strip()
                    if tag and tag not in seen:
                        seen.add(tag)
                        tags.append(tag)
            except re.error:
                continue

        return tags

    @staticmethod
    def _match_path_rule(rule: PathTagRule, file_path: Path) -> list[str]:
        """用 glob 匹配路径，命中则返回 tags 列表。

        支持 source-root-relative 写法：pattern="面试/HSBC/*.md" 能匹配
        /notes/面试/HSBC/prep.md 中的 "面试/HSBC/prep.md" 后缀。
        """
        path_str = file_path.as_posix()
        parts = path_str.split("/")

        candidates = {path_str, file_path.name}
        # 生成所有后缀路径：a/b/c.md, b/c.md, c.md
        for i in range(len(parts)):
            candidates.add("/".join(parts[i:]))

        for candidate in candidates:
            if fnmatch.fnmatch(candidate, rule.pattern):
                return [t.strip() for t in rule.tags.split(",") if t.strip()]
        return []


def infer_tags(config: Config, file_path: Path, content: str = "") -> list[str]:
    """便捷函数：用配置推断标签。"""
    return Tagger(config).infer(file_path, content)
