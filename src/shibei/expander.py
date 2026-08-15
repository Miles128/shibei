"""查询扩展模块 — 零向量库的近义词/模糊匹配支持。

策略：
  1. 同义词表扩展：加载外部 YAML/JSON 同义词表，查询时把关键词扩成 OR 查询。
  2. 模糊匹配兜底：当同义词表未命中时，用编辑距离从词典中找相近词。

不依赖任何 LLM 或向量库，保持 BM25 索引结构不变。
"""

import json
import logging
import re
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

_CHINESE_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")
_ALNUM_RE = re.compile(r"[A-Za-z0-9_]+")


def _extract_query_terms(query: str, min_len: int = 2) -> list[str]:
    """把查询拆成适合扩展的词。

    优先使用 jieba 精确分词；没有 jieba 时退化为：连续中文片段 + 英文/数字 token。
    这里不拆 bigram/单字，避免扩展后的查询过度膨胀；bigram/单字由后续 tokenize_query 负责。
    """
    from .tokenizer import jieba_available, _iter_mixed_segments

    query = query.strip()
    if not query:
        return []

    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        if len(term) >= min_len and term not in seen:
            seen.add(term)
            terms.append(term)

    if jieba_available():
        import jieba
        for chunk in _iter_mixed_segments(query):
            if _CHINESE_RE.fullmatch(chunk):
                add(chunk)
                for t in jieba.cut(chunk):
                    t = t.strip()
                    if t:
                        add(t)
            else:
                for w in _ALNUM_RE.findall(chunk):
                    add(w)
        return terms

    # 降级：连续中文片段 + 英文/数字
    for frag in _CHINESE_RE.findall(query):
        add(frag)
    for w in _ALNUM_RE.findall(query):
        add(w)
    return terms


def _load_synonyms(path: 'str | Path') -> dict[str, list[str]]:
    """加载同义词表，支持 YAML/JSON。"""
    p = Path(path).expanduser()
    if not p.exists():
        logger.warning("同义词表不存在: %s", p)
        return {}

    raw = p.read_text(encoding="utf-8")
    suffix = p.suffix.lower()
    if suffix in (".yaml", ".yml"):
        import yaml
        data = yaml.safe_load(raw) or {}
    elif suffix == ".json":
        data = json.loads(raw)
    else:
        # 默认按 YAML 尝试，失败再按 JSON
        try:
            import yaml
            data = yaml.safe_load(raw) or {}
        except Exception:
            data = json.loads(raw)

    if not isinstance(data, dict):
        logger.warning("同义词表格式错误，应为 dict: %s", p)
        return {}

    synonyms: dict[str, list[str]] = {}
    for key, values in data.items():
        if not isinstance(key, str):
            continue
        if isinstance(values, str):
            values = [values]
        elif isinstance(values, Iterable) and not isinstance(values, (str, bytes)):
            values = [v for v in values if isinstance(v, str)]
        else:
            continue
        # 去重并保留原始 key
        seen = set()
        normalized = []
        for v in [key] + values:
            v = v.strip()
            if v and v not in seen:
                seen.add(v)
                normalized.append(v)
        if normalized:
            synonyms[key.strip()] = normalized
    return synonyms


def _edit_distance(a: str, b: str) -> int:
    """计算两个字符串的最小编辑距离（Levenshtein）。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    # 始终保持较短的字符串作为列
    if len(a) < len(b):
        a, b = b, a

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr.append(min(
                curr[-1] + 1,      # 删除
                prev[j] + 1,       # 插入
                prev[j - 1] + cost # 替换
            ))
        prev = curr
    return prev[-1]


class QueryExpander:
    """查询扩展器：同义词表 + 编辑距离模糊匹配。"""

    def __init__(
        self,
        synonyms_path: 'str | Path | None' = None,
        fuzzy: bool = False,
        fuzzy_max_distance: int = 1,
        fuzzy_max_candidates: int = 3,
        min_query_len: int = 2,
    ):
        self.synonyms: dict[str, list[str]] = (
            _load_synonyms(synonyms_path) if synonyms_path else {}
        )
        self.fuzzy = fuzzy
        self.fuzzy_max_distance = max(1, fuzzy_max_distance)
        self.fuzzy_max_candidates = max(0, fuzzy_max_candidates)
        self.min_query_len = max(1, min_query_len)

        # 构建反向索引：每个词属于哪些 key（用于模糊匹配候选）
        self._dictionary: set[str] = set()
        for key, values in self.synonyms.items():
            self._dictionary.add(key)
            self._dictionary.update(values)

    def _lookup_synonyms(self, word: str) -> list[str]:
        """查同义词表，返回该词对应的同义词列表（含自身）。"""
        word = word.strip()
        if not word or len(word) < self.min_query_len:
            return []
        if word in self.synonyms:
            return list(self.synonyms[word])

        # 尝试查找包含该词的 key 的同义词（如查询词是 key 的子串）
        # 只在词典完全匹配失败时使用，避免过度扩展
        return []

    def _fuzzy_candidates(self, word: str) -> list[str]:
        """用编辑距离找相近词，按距离排序。"""
        if not self.fuzzy or not self._dictionary or len(word) < self.min_query_len:
            return []

        candidates: list[tuple[int, str]] = []
        for candidate in self._dictionary:
            if candidate == word:
                continue
            # 长度差异过大时跳过，减少计算
            if abs(len(candidate) - len(word)) > self.fuzzy_max_distance:
                continue
            dist = _edit_distance(word, candidate)
            if dist <= self.fuzzy_max_distance:
                candidates.append((dist, candidate))

        candidates.sort(key=lambda x: (x[0], len(x[1]), x[1]))
        return [c for _, c in candidates[:self.fuzzy_max_candidates]]

    def expand_word(self, word: str) -> list[str]:
        """扩展单个词：先同义词表，再模糊匹配。"""
        word = word.strip()
        if not word or len(word) < self.min_query_len:
            return [word] if word else []

        synonyms = self._lookup_synonyms(word)
        if synonyms:
            return synonyms

        fuzzy = self._fuzzy_candidates(word)
        if fuzzy:
            return [word] + fuzzy

        return [word]

    def expand(self, query: str) -> dict:
        """扩展查询，返回结构化信息。

        Returns:
            {
                "original": "原始查询",
                "expanded": "扩展后查询（空格分隔，供 BM25 使用）",
                "expansions": {"词": ["同义词", ...]},
                "fuzzy": {"词": ["候选", ...]},
            }
        """
        query = query.strip()
        if not query:
            return {
                "original": query,
                "expanded": "",
                "expansions": {},
                "fuzzy": {},
            }

        all_words = _extract_query_terms(query, min_len=self.min_query_len)

        expanded_words: list[str] = []
        expansions: dict[str, list[str]] = {}
        fuzzy_map: dict[str, list[str]] = {}

        for word in all_words:
            syns = self._lookup_synonyms(word)
            if syns:
                # 同义词扩展：保留原始词 + 同义词
                expanded_words.extend(syns)
                expansions[word] = [s for s in syns if s != word]
                continue

            fuzzy = self._fuzzy_candidates(word)
            if fuzzy:
                expanded_words.append(word)
                expanded_words.extend(fuzzy)
                fuzzy_map[word] = fuzzy
                continue

            expanded_words.append(word)

        # 去重并保持顺序
        seen: set[str] = set()
        unique_words: list[str] = []
        for w in expanded_words:
            if w not in seen:
                seen.add(w)
                unique_words.append(w)

        return {
            "original": query,
            "expanded": " ".join(unique_words),
            "expansions": expansions,
            "fuzzy": fuzzy_map,
        }

    def expand_query_for_bm25(self, query: str) -> str:
        """直接返回扩展后的 BM25 查询字符串。"""
        return self.expand(query)["expanded"]
