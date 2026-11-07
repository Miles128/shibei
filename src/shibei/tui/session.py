"""召回诊断台的状态机 — 不依赖任何 UI 框架。

Textual / Tkinter / PySide6 都只做「渲染 + 输入映射」，
所有取数、开关语义、文本投影都在这里，便于单测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

# 可三态切换的检索开关：None=跟随 config，True=强制开，False=强制关
FLAGS = ("rewrite", "expand", "fuzzy", "vector", "rerank", "memories")
FLAG_HINTS = {
    "rewrite": "规则改写",
    "expand": "同义词/模糊扩展",
    "fuzzy": "编辑距离兜底",
    "vector": "向量混合",
    "rerank": "cross-encoder 重排",
    "memories": "对话记忆",
}
FLAG_MARKS = {None: "·", True: "✓", False: "✗"}


class ClientLike(Protocol):
    def recall(self, **kwargs: Any) -> dict: ...


@dataclass
class RecallState:
    query: str = ""
    flags: dict[str, 'bool | None'] = field(
        default_factory=lambda: {key: None for key in FLAGS}
    )
    limit: int = 5
    brain: str = ""
    result: 'dict | None' = None
    selected: int = 0
    error: str = ""
    connected: bool = False


class RecallSession:
    """召回诊断台的会话：连 sidecar、跑 recall、把结果投影成 UI 友好的形状。"""

    def __init__(self, config_path: str = "config.yaml", client: 'ClientLike | None' = None):
        self.config_path = config_path
        self._client: 'Any | None' = client
        self.state = RecallState(connected=client is not None)
        self.brains: list[str] = []

    # ---------- 连接 ----------

    def connect(self) -> None:
        """连接（必要时拉起）sidecar。会阻塞，调用方负责放到后台线程。"""
        if self._client is None:
            from ..client import ShibeiClient

            self._client = ShibeiClient(self.config_path)
        self._load_brains()
        self.state.connected = True

    def _load_brains(self) -> None:
        if self._client is None:
            return
        try:
            data = self._client.brains()
            self.brains = [str(b.get("name")) for b in data.get("brains") or []]
            default = data.get("default")
        except Exception:  # noqa: BLE001
            # 老版本 daemon 没有 /v1/brains
            self.brains = []
            default = None
        if not self.brains:
            self.brains = [self.state.brain or "default"]
        if not self.state.brain:
            self.state.brain = default or self.brains[0]
        if self._client is not None and hasattr(self._client, "brain"):
            self._client.brain = self.state.brain

    @property
    def connected(self) -> bool:
        return self.state.connected

    # ---------- 多库 ----------

    def select_brain(self, name: str) -> None:
        if not name:
            return
        self.state.brain = name
        if self._client is not None and hasattr(self._client, "brain"):
            self._client.brain = name

    def cycle_brain(self) -> str:
        if len(self.brains) <= 1:
            return self.state.brain
        try:
            index = self.brains.index(self.state.brain)
        except ValueError:
            index = -1
        self.select_brain(self.brains[(index + 1) % len(self.brains)])
        return self.state.brain

    # ---------- 开关 ----------

    def cycle_flag(self, flag: str) -> 'bool | None':
        """None → True → False → None。"""
        if flag not in self.state.flags:
            raise KeyError(flag)
        current = self.state.flags[flag]
        self.state.flags[flag] = {None: True, True: False, False: None}[current]
        return self.state.flags[flag]

    def flag_mark(self, flag: str) -> str:
        return FLAG_MARKS[self.state.flags.get(flag)]

    # ---------- 召回 ----------

    def run(self, query: 'str | None' = None) -> dict:
        """同步执行一次召回。慢，调用方必须放到后台线程。"""
        if self._client is None:
            raise RuntimeError("sidecar 未连接")
        q = (query if query is not None else self.state.query).strip()
        if not q:
            raise ValueError("查询不能为空")

        flags = self.state.flags
        self.state.query = q
        try:
            data = self._client.recall(
                query=q,
                limit=self.state.limit,
                mode="raw",
                memories=flags["memories"] is not False,
                trace=True,
                full_text=True,
                rewrite=flags["rewrite"],
                expand=flags["expand"],
                fuzzy=flags["fuzzy"],
                vector=flags["vector"],
                rerank=flags["rerank"],
                brain=self.state.brain or None,
            )
        except Exception as exc:
            self.state.error = str(exc)
            raise

        self.state.error = ""
        self.state.result = data
        self.state.selected = 0
        return data

    # ---------- 全部片段 ----------

    def chunks(
        self,
        offset: int = 0,
        limit: int = 200,
        source: 'str | None' = None,
        tag: 'str | None' = None,
    ) -> dict:
        """分页读取库里已有的 chunk（不走向量/BM25 排序）。"""
        if self._client is None:
            raise RuntimeError("sidecar 未连接")
        return self._client.list_chunks(
            limit=limit, offset=offset, source=source, tag=tag
        )

    # ---------- 投影 ----------

    def results(self) -> list[dict]:
        return list((self.state.result or {}).get("results") or [])

    def memories(self) -> list[dict]:
        return list((self.state.result or {}).get("memories") or [])

    def rows(self) -> list[tuple[str, str, str, str, str, str]]:
        out = []
        for index, item in enumerate(self.results()):
            why = item.get("why") or {}
            routes = "+".join(why.get("routes") or []) or "-"
            out.append((
                str(index + 1),
                f'{float(item.get("score") or 0):.2f}',
                item.get("brain") or self.state.brain or "-",
                routes,
                item.get("source") or "-",
                (item.get("text") or "").replace("\n", " ")[:60],
            ))
        return out

    def memory_rows(self) -> list[tuple[str, str, str]]:
        out = []
        for index, item in enumerate(self.memories()):
            out.append((
                str(index + 1),
                f'{float(item.get("score") or 0):.2f}',
                (item.get("text") or "").replace("\n", " ")[:70],
            ))
        return out

    def summary_line(self) -> str:
        data = self.state.result
        if data is None:
            return "输入查询后回车；1-6 切换检索开关"
        trace = data.get("trace") or {}
        elapsed = float(trace.get("total_ms") or 0)
        parts = []
        if len(self.brains) > 1 or data.get("federated"):
            parts.append(f'库 {self.state.brain or "-"}')
        parts += [
            f'使用词 {data.get("query_used") or "-"}',
            f'{(data.get("total") or 0)} 条',
            f'{elapsed:.0f} ms',
        ]
        if data.get("total") == 0:
            parts.append("无结果：试试关掉 rewrite 或打开 fuzzy")
        return " · ".join(parts)

    def trace_rows(self) -> list[dict]:
        trace = (self.state.result or {}).get("trace") or {}
        return list(trace.get("stages") or [])

    def trace_notes(self) -> list[str]:
        trace = (self.state.result or {}).get("trace") or {}
        notes: list[str] = []
        federated = trace.get("federated")
        if federated:
            counts = federated.get("counts") or {}
            summary = " ".join(f"{name}:{count}" for name, count in counts.items())
            notes.append(f'联邦 k={federated.get("k")} · {summary}')
        dropped = trace.get("dropped") or {}
        if dropped:
            notes.append("丢弃 " + " ".join(
                f"{key}={value}" for key, value in dropped.items()
            ))
        for item in trace.get("degraded") or []:
            notes.append("降级 " + item)
        return notes

    def current(self, index: 'int | None' = None) -> 'dict | None':
        items = self.results()
        if not items:
            return None
        index = self.state.selected if index is None else index
        if not 0 <= index < len(items):
            return None
        return items[index]

    def current_path(self) -> str:
        item = self.current() or {}
        return str(item.get("file_path") or item.get("source") or "")

    def detail(self, index: 'int | None' = None) -> str:
        item = self.current(index)
        if item is None:
            return "（无选中结果）"
        why = item.get("why") or {}
        ranks = why.get("ranks") or {}
        routes = " · ".join(
            f"{route}#{ranks.get(route, '?')}" for route in (why.get("routes") or [])
        ) or "-"
        matched = " ".join(why.get("matched_terms") or []) or "-"
        head = [
            str(item.get("file_path") or item.get("source") or "-"),
            f'库 {item.get("brain") or self.state.brain or "-"}',
            f'分数 {item.get("score")} ({item.get("score_kind") or "bm25"})'
            f' · 维度 {item.get("rank")}'
            + (f' · 重排 {item.get("rerank_score")}' if item.get("rerank_score") is not None else ""),
            f"路由 {routes}",
            f"命中词 {matched}",
            f'tags {item.get("tags") or "-"}',
        ]
        body = str(item.get("text") or "").strip()
        parent = str(item.get("parent_text") or "").strip()
        chunks = ["\n".join(head), "", body]
        if parent and parent != body:
            chunks += ["", "──── parent ────", parent]
        return "\n".join(chunks)
