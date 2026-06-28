"""分块器 — 支持 overlap + 中文句子边界 + parent-child chunk

设计：
  1. 按 ## 标题切（保持原有 split_pattern）
  2. 标题内按句子边界切（中文句号/换行），不切断词
  3. 句子累积到 max_chars 内合并
  4. 相邻 chunk 重叠 N 字符（overlap > 0 时）
  5. parent-child：小 chunk 用于索引，parent_window 个相邻 child 合并为 parent

返回结构：
  - parent_window=0: list[str]  （普通 chunk）
  - parent_window>0: list[ChildChunk] （child + parent_id）
"""

import re
from dataclasses import dataclass


@dataclass
class ChildChunk:
    """parent-child 模式下的子 chunk。"""
    text: str           # child 文本（用于索引）
    parent_text: str    # parent 文本（用于返回上下文）
    parent_id: str      # parent 标识（source + parent 序号）
    child_index: int    # 在 parent 中的位置


def _split_by_sentences(text: str, boundaries: str) -> list[str]:
    """按句子边界切分，保留句末标点。

    中文句号/问号/感叹号 + 换行 都视为句子边界。
    不切断英文单词（按空格切）。
    """
    if not text:
        return []

    # 构造正则：在 boundaries 中任意字符后切分，保留分隔符
    # 例：boundaries="。！？!?\n" → 在这些字符后切
    pattern = f"([{re.escape(boundaries)}])"
    parts = re.split(pattern, text)

    # 合并句子 + 标点
    sentences = []
    i = 0
    while i < len(parts):
        s = parts[i]
        # 如果下一个是标点，合并到当前句子
        if i + 1 < len(parts) and len(parts[i + 1]) == 1:
            s = s + parts[i + 1]
            i += 2
        else:
            i += 1
        s = s.strip()
        if s:
            sentences.append(s)
    return sentences


def _merge_sentences(
    sentences: list[str],
    max_chars: int,
    overlap: int,
) -> list[str]:
    """把句子合并成 chunk，控制 max_chars，可选 overlap。

    overlap 逻辑：下一个 chunk 从上一个 chunk 末尾的 overlap 字符开始。
    """
    if not sentences:
        return []

    chunks = []
    buf = ""

    for sent in sentences:
        # 单句超长：强制按 max_chars 切（不再按句子）
        if len(sent) > max_chars:
            # 先 flush buf
            if buf:
                chunks.append(buf)
                buf = ""
            # 硬切 sent
            for i in range(0, len(sent), max_chars):
                chunks.append(sent[i:i + max_chars])
            continue

        # 累积到 buf
        if len(buf) + len(sent) <= max_chars:
            buf = (buf + sent) if not buf else (buf + " " + sent)
        else:
            # buf 满了，存入
            if buf:
                chunks.append(buf)
            # overlap：从 buf 末尾取 overlap 字符作为下一个 chunk 开头
            if overlap > 0 and len(buf) > overlap:
                buf = buf[-overlap:] + " " + sent
            else:
                buf = sent
    if buf:
        chunks.append(buf)

    return chunks


def chunk_text(
    text: str,
    max_chars: int = 800,
    split_pattern: str = r"\n(?=## )",
    overlap: int = 0,
    sentence_boundaries: str = "。！？!?\n",
    parent_window: int = 0,
    source: str = "",
) -> 'list[str] | list[ChildChunk]':
    """分块主入口。

    Args:
        text: 原文
        max_chars: 每块最大字符数
        split_pattern: 一级切分正则（默认按 ## 标题）
        overlap: 相邻 chunk 重叠字符数（0=不重叠）
        sentence_boundaries: 句子边界字符
        parent_window: >0 启用 parent-child，parent 包含 N 个 child
        source: 文件路径（用于生成 parent_id）

    Returns:
        parent_window=0: list[str]
        parent_window>0: list[ChildChunk]
    """
    # L1: 按标题切
    sections = re.split(split_pattern, text) if split_pattern else [text]

    # L2+L3: 每个标题内按句子切 + 合并 + overlap
    raw_chunks: list[str] = []
    for sec in sections:
        sec = sec.strip()
        if not sec or len(sec) < 10:
            continue
        sentences = _split_by_sentences(sec, sentence_boundaries)
        merged = _merge_sentences(sentences, max_chars, overlap)
        raw_chunks.extend(merged)

    # parent_window=0: 直接返回字符串列表
    if parent_window <= 0:
        return raw_chunks

    # parent_window>0: 构建 parent-child
    return _build_parent_child(raw_chunks, parent_window, source)


def _build_parent_child(
    chunks: list[str],
    parent_window: int,
    source: str,
) -> list[ChildChunk]:
    """把相邻 child chunks 组合成 parent。

    parent_window=2 表示每个 parent 包含 2 个相邻 child。
    滑动窗口：child[0,1] → parent_0, child[1,2] → parent_1, ...
    这样每个 child 都有 parent，且 parent 之间有重叠。

    Args:
        chunks: child chunk 文本列表
        parent_window: parent 包含的 child 数
        source: 文件路径（用于 parent_id）

    Returns:
        list[ChildChunk]，每个 child 携带其 parent 信息
    """
    if not chunks:
        return []

    # 滑动窗口生成 parent
    parents: list[tuple[str, list[int]]] = []  # (parent_text, [child_indices])
    for i in range(len(chunks)):
        window_end = min(i + parent_window, len(chunks))
        window = chunks[i:window_end]
        parent_text = "\n\n".join(window)
        parent_id = f"{source}#parent_{i}"
        parents.append((parent_text, list(range(i, window_end))))

    # 每个 child 关联到包含它的第一个 parent
    # child[i] 的 parent 是 parents[i]（滑动窗口起点为 i）
    result = []
    for i, child_text in enumerate(chunks):
        if i < len(parents):
            parent_text, _ = parents[i]
            parent_id = f"{source}#parent_{i}"
        else:
            # 最后几个 child 可能没有以它为起点的 parent，用最后一个
            parent_text, _ = parents[-1]
            parent_id = f"{source}#parent_{len(parents) - 1}"

        result.append(ChildChunk(
            text=child_text,
            parent_text=parent_text,
            parent_id=parent_id,
            child_index=i,
        ))
    return result
