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

# Markdown / 格式噪音：#、##、###、---、***、> 等
_NOISE_RE = re.compile(r"^[#\-*=|>_+\s\d]+$")


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
        for chunk in _CHINESE_RE.split(text):
            if not chunk:
                continue
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
        for chunk in _CHINESE_RE.split(text):
            if not chunk:
                continue
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
                    w for w in _ALNUM_RE.findall(chunk) if not is_noise_token(w)
                )
        return " OR ".join(tokens) if tokens else text

    # 降级：连续中文保持短语+bigram
    return _bigram_query_tokenize(text)


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
        result.extend(_ALNUM_RE.findall(non_cn_buf))
    if cn_buf:
        result.append(f'"{cn_buf}"')
        for i in range(len(cn_buf) - 1):
            result.append(f'"{cn_buf[i:i+2]}"')
    return " OR ".join(result) if result else text
