"""全部片段浏览 —— 按 rowid 顺序分页查看库里已有的所有 chunk。

不经过 BM25 / 向量，纯 SQL 分页，用于核对「切分是否合理、内容是否进了库」。
"""

from __future__ import annotations

import os
import subprocess

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from .session import RecallSession

PAGE_SIZE = 200


class BrowseScreen(Screen):
    """全部 chunk 列表 + 详情。"""

    CSS = """
    #browse-body { height: 1fr; }
    #browse-left { width: 58%; }
    #browse-right { width: 42%; border-left: solid $panel; padding: 0 1; }
    #browse-summary { height: auto; padding: 1 0; color: $text-muted; }
    #chunks { height: 1fr; }
    #chunk-detail { height: 1fr; overflow-y: auto; }
    """

    BINDINGS = [
        Binding("escape", "app.pop_screen", "返回"),
        Binding("n", "next_page", "下一页"),
        Binding("p", "prev_page", "上一页"),
        Binding("g", "first_page", "首页"),
        Binding("end", "last_page", "末页"),
        Binding("o", "open_file", "打开文件"),
        Binding("r", "reload", "刷新"),
        Binding("ctrl+r", "reindex_file", "重建文件"),
    ]

    def __init__(self, session: RecallSession):
        super().__init__()
        self.session = session
        self._items: list[dict] = []
        self._total = 0
        self._offset = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="browse-body"):
            with Vertical(id="browse-left"):
                yield Static("加载中…", id="browse-summary")
                yield DataTable(id="chunks", cursor_type="row", zebra_stripes=True)
            with Vertical(id="browse-right"):
                yield Static("（无选中片段）", id="chunk-detail", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#chunks", DataTable)
        table.add_columns("#", "来源", "标签", "片段")
        self._load(0)

    # ---------- 取数 ----------

    @work(thread=True, exclusive=True, group="chunks")
    def _load(self, offset: int) -> None:
        try:
            data = self.session.chunks(offset=max(offset, 0), limit=PAGE_SIZE)
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(self._set_error, str(exc))
            return
        self.app.call_from_thread(self._render_page, data)

    def _set_error(self, message: str) -> None:
        self.query_one("#browse-summary", Static).update(f"加载失败：{message}")

    def _render_page(self, data: dict) -> None:
        self._items = list(data.get("items") or [])
        self._total = int(data.get("total") or 0)
        self._offset = int(data.get("offset") or 0)

        table = self.query_one("#chunks", DataTable)
        table.clear()
        for index, item in enumerate(self._items):
            snippet = (item.get("text") or "").replace("\n", " ")[:70]
            table.add_row(
                str(self._offset + index + 1),
                item.get("source") or "-",
                item.get("tags") or "-",
                snippet,
                key=str(index),
            )

        start = self._offset + 1 if self._items else 0
        end = self._offset + len(self._items)
        hint = "n/p 翻页 · esc 返回"
        if self._total > PAGE_SIZE:
            hint = f"第 {start}-{end} 条 / 共 {self._total} 条 · {hint}"
        else:
            hint = f"共 {self._total} 条 · {hint}"
        brain = self.session.state.brain or "default"
        self.query_one("#browse-summary", Static).update(
            f"全部片段 [{brain}] · {hint}"
        )

        if self._items:
            table.focus()
            table.move_cursor(row=0)
            self._show_detail(0)
        else:
            self.query_one("#chunk-detail", Static).update("（库里还没有 chunk，先 shibei import）")

    # ---------- 交互 ----------

    @on(DataTable.RowHighlighted, "#chunks")
    def _highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_detail(event.cursor_row)

    def _show_detail(self, index: int) -> None:
        if not 0 <= index < len(self._items):
            return
        item = self._items[index]
        head = [
            f'#{item.get("_rowid")}  {item.get("source") or "-"}',
            f'tags {item.get("tags") or "-"}',
            f'{self._offset + index + 1}/{self._total}',
        ]
        text = str(item.get("text") or "").strip()
        parent = str(item.get("parent_text") or "").strip()
        parts = ["  ·  ".join(head), "", text]
        if parent and parent != text:
            parts += ["", "──── parent ────", parent]
        self.query_one("#chunk-detail", Static).update("\n".join(parts))

    def action_next_page(self) -> None:
        if self._offset + PAGE_SIZE < self._total:
            self._load(self._offset + PAGE_SIZE)

    def action_prev_page(self) -> None:
        self._load(max(self._offset - PAGE_SIZE, 0))

    def action_first_page(self) -> None:
        self._load(0)

    def action_last_page(self) -> None:
        if self._total <= 0:
            return
        last = ((self._total - 1) // PAGE_SIZE) * PAGE_SIZE
        self._load(last)

    def action_reindex_file(self) -> None:
        table = self.query_one("#chunks", DataTable)
        index = table.cursor_row
        if not 0 <= index < len(self._items):
            return
        item = self._items[index]
        source = str(item.get("source") or item.get("file_path") or "")
        if not source:
            return
        self.query_one("#browse-summary", Static).update(f"重建中：{source}")
        self._reindex(source, self._offset)

    @work(thread=True, exclusive=True, group="reindex")
    def _reindex(self, source: str, offset: int) -> None:
        try:
            info = self.session.reindex(source)
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(self._set_error, str(exc))
            return
        self.app.call_from_thread(self._after_reindex, info, offset)

    def _after_reindex(self, info: dict, offset: int) -> None:
        self.query_one("#browse-summary", Static).update(
            f'重建完成：{info.get("files", 0)} 文件 / {info.get("chunks", 0)} chunks'
        )
        self._load(offset)

    def action_reload(self) -> None:
        self._load(self._offset)

    def action_open_file(self) -> None:
        table = self.query_one("#chunks", DataTable)
        index = table.cursor_row
        if not 0 <= index < len(self._items):
            return
        path = str(self._items[index].get("file_path") or "")
        if not path or not os.path.exists(path):
            self.query_one("#browse-summary", Static).update(f"文件不存在：{path or '-'}")
            return
        editor = os.environ.get("EDITOR", "vim")
        with self.app.suspend():
            subprocess.call([editor, path])
