"""中文分词公共模块 — 统一 jieba 检测和分词逻辑。

bm25.py 和 memory.py 共用此模块，消除重复代码。

分词策略：
  - 优先使用 jieba（若安装）：精确分词，搜索质量更高
  - 降级到 bigram + 单字：零依赖兜底
"""

import re

_CHINESE_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")
_ALNUM_RE = re.compile(r"[A-Za-z0-9_]+")
_CN_CHAR_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_MIXED_SEGMENT_RE = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf]+|[A-Za-z0-9_]+"
)

# Markdown / 格式噪音：#、##、###、---、***、> 等
_NOISE_RE = re.compile(r"^[#\-*=|>_+\s\d]+$")

# 查询相关性门槛用的停用词：只保留承载信息的词
_QUERY_STOPWORDS = {
    "的", "了", "吗", "呢", "吧", "啊", "呀", "嘛", "是", "有", "在",
    "和", "与", "或", "及", "这个", "那个", "什么", "怎么", "怎么样",
    "如何", "哪个", "哪些", "请", "帮", "我", "你", "您", "他", "她",
    "我们", "你们", "他们", "需要", "可以", "一个", "一下",
}


def is_noise_token(token: str) -> bool:
    """判断 token 是否为 Markdown 格式符号、纯数字、纯空白等无意义词。"""
    if not token or token.isspace():
        return True
    return bool(_NOISE_RE.fullmatch(token))

_jieba_ok: 'bool | None' = None


def jieba_available() -> bool:
    """检测 jieba 是否可用（只检测一次，缓存结果）。"""
    global _jieba_ok
    if _jieba_ok is None:
        try:
            import jieba  # noqa: F401
            _jieba_ok = True
        except ImportError:
            _jieba_ok = False
    return _jieba_ok


def _iter_mixed_segments(text: str):
    """按中英文片段迭代，避免 split 把纯中文串切成空串。"""
    for match in _MIXED_SEGMENT_RE.finditer(text):
        yield match.group(0)


def tokenize_text(text: str) -> str:
    """中文分词：jieba 优先，降级到 bigram + 单字。

    用于文档索引（BM25 FTS5 和 memory fact 存储）。
    返回空格分隔的 token 字符串。
    """
    text = text.strip()
    if not text:
        return ""

    if jieba_available():
        import jieba
        tokens: list[str] = []
        for chunk in _iter_mixed_segments(text):
            if _CHINESE_RE.fullmatch(chunk):
                tokens.extend(
                    t.strip()
                    for t in jieba.cut(chunk)
                    if t.strip() and not is_noise_token(t.strip())
                )
            else:
                tokens.extend(
                    w for w in _ALNUM_RE.findall(chunk) if not is_noise_token(w)
                )
        return " ".join(tokens)

    # 降级：bigram + 单字 + 英文完整 token
    return _bigram_tokenize(text)


def tokenize_query(text: str) -> str:
    """查询分词：jieba 优先，降级到短语+bigram。

    用于 FTS5 查询表达式，返回 OR 连接的短语。
    """
    text = text.strip()
    if not text:
        return ""

    if jieba_available():
        import jieba
        tokens: list[str] = []
        for chunk in _iter_mixed_segments(text):
            if _CHINESE_RE.fullmatch(chunk):
                tokens.append(f'"{chunk}"')
                sub = [
                    t.strip()
                    for t in jieba.cut(chunk)
                    if t.strip() and not is_noise_token(t.strip())
                ]
                tokens.extend(f'"{t}"' for t in sub)
                for i in range(len(chunk) - 1):
                    tokens.append(f'"{chunk[i:i+2]}"')
            else:
                tokens.extend(
                    f'"{w}"' for w in _ALNUM_RE.findall(chunk) if not is_noise_token(w)
                )
        # 无有效 token（纯噪音查询如 "###"）时返回空串，由调用方短路返回空结果，
        # 避免把原始文本拼进 FTS5 MATCH 表达式导致语法错误。
        return " OR ".join(tokens) if tokens else ""

    # 降级：连续中文保持短语+bigram
    return _bigram_query_tokenize(text)


def query_terms(text: str, max_terms: int = 20) -> list[str]:
    """提取查询的核心词（去停用词/单字），用于相关性覆盖率门槛。"""
    text = (text or "").strip()
    if not text:
        return []
    terms: list[str] = []
    if jieba_available():
        import jieba
        candidates = [t.strip() for t in jieba.cut(text)]
    else:
        candidates = []
        for chunk in _iter_mixed_segments(text):
            if _CHINESE_RE.fullmatch(chunk):
                candidates.append(chunk)
            else:
                candidates.extend(_ALNUM_RE.findall(chunk))
    for term in candidates:
        if len(term) < 2 or term in _QUERY_STOPWORDS:
            continue
        if not any(ch.isalnum() or _CN_CHAR_RE.match(ch) for ch in term):
            continue
        if term not in terms:
            terms.append(term)
        if len(terms) >= max_terms:
            break
    return terms


def query_coverage_groups(text: str, groups: list[list[str]]) -> float:
    """按同义词/模糊候选分组计算覆盖度：组内任一词命中即算该组命中。"""
    if not groups:
        return 1.0
    lowered = (text or "").lower()
    total = 0
    matched = 0
    for group in groups:
        alternatives = [t for t in group if t]
        if not alternatives:
            continue
        # 权重按原始查询词算，避免长英文同义词（interview）抬高泛词的分量
        weight = max(len(alternatives[0]) - 1, 0)
        total += weight
        if any(t.lower() in lowered for t in alternatives):
            matched += weight
    return matched / total if total else 1.0


def _bigram_tokenize(text: str) -> str:
    """降级分词：bigram + 单字 + 英文完整 token。"""
    result = []
    non_cn_buf = ""
    cn_buf = ""
    for ch in text:
        if _CN_CHAR_RE.match(ch):
            if non_cn_buf:
                result.extend(_ALNUM_RE.findall(non_cn_buf))
                non_cn_buf = ""
            cn_buf += ch
        else:
            if cn_buf:
                result.append(cn_buf)
                for i in range(len(cn_buf)):
                    result.append(cn_buf[i])
                    if i + 1 < len(cn_buf):
                        result.append(cn_buf[i : i + 2])
                cn_buf = ""
            non_cn_buf += ch
    if non_cn_buf:
        result.extend(_ALNUM_RE.findall(non_cn_buf))
    if cn_buf:
        result.append(cn_buf)
        for i in range(len(cn_buf)):
            result.append(cn_buf[i])
            if i + 1 < len(cn_buf):
                result.append(cn_buf[i : i + 2])
    return " ".join(result)


def _bigram_query_tokenize(text: str) -> str:
    """降级查询分词：短语+bigram，OR 连接。"""
    result = []
    non_cn_buf = ""
    cn_buf = ""
    for ch in text:
        if _CN_CHAR_RE.match(ch):
            if non_cn_buf:
                result.extend(_ALNUM_RE.findall(non_cn_buf))
                non_cn_buf = ""
            cn_buf += ch
        else:
            if cn_buf:
                result.append(f'"{cn_buf}"')
                for i in range(len(cn_buf) - 1):
                    result.append(f'"{cn_buf[i:i+2]}"')
                cn_buf = ""
            non_cn_buf += ch
    if non_cn_buf:
        result.extend(f'"{w}"' for w in _ALNUM_RE.findall(non_cn_buf))
    if cn_buf:
        result.append(f'"{cn_buf}"')
        for i in range(len(cn_buf) - 1):
            result.append(f'"{cn_buf[i:i+2]}"')
    return " OR ".join(result) if result else ""
