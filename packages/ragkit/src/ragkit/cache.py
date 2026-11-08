"""稳定的查询缓存键。"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def make_cache_key(*parts: Any) -> str:
    """把任意可 JSON 化片段拼成稳定 key；列表会保序，None 忽略。

    返回 sha1 十六进制，避免长文本直接进内存键。
    """
    payload = [part for part in parts if part is not None]
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()
