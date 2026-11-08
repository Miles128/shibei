"""导入诊断 —— 最近一次导入的逐文件结果、失败原因与 chunk 预览。"""

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

STATUS_LABEL = {
    "ok": "✓ 成功",
    "failed": "✗ 失败",
    "skipped": "· 跳过",
    "removed": "- 移除",
}


class ImportReportScreen(Screen):
    """展示 import_report.json：哪个文件进了多少 chunk、哪个失败了。"""

    CSS = """
    #report-body { height: 1fr; }
    #report-left { width: 58%; }
    #report-right { width: 42%; border-left: solid $panel; padding: 0 1; }
    #report-summary { height: auto; padding: 1 0; color: $text-muted; }
    #report-files { height: 1fr; }
    #report-detail { height: 1fr; overflow-y: auto; }
    """

    BINDINGS = [
        Binding("escape", "app.pop_screen", "返回"),
        Binding("g", "refresh", "刷新"),
        Binding("ctrl+r", "reindex_file", "重建文件"),
        Binding("o", "open_file", "打开文件"),
    ]

    def __init__(self, session: RecallSession):
        super().__init__()
        self.session = session
        self._files: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="report-body"):
            with Vertical(id="report-left"):
                yield Static("加载中…", id="report-summary")
                yield DataTable(id="report-files", cursor_type="row", zebra_stripes=True)
            with Vertical(id="report-right"):
                yield Static("（无选中文件）", id="report-detail", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#report-files", DataTable)
        table.add_columns("状态", "chunks", "文件")
        self._load()

    # ---------- 数据 ----------

    @work(thread=True, exclusive=True, group="report")
    def _load(self) -> None:
        try:
            data = self.session.import_report()
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(self._set_error, str(exc))
            return
        self.app.call_from_thread(self._render_report, data)

    def _render_report(self, data: dict) -> None:
        self._files = list(data.get("files") or [])
        table = self.query_one("#report-files", DataTable)
        table.clear()

        for index, item in enumerate(self._files):
            status = STATUS_LABEL.get(item.get("status", ""), item.get("status", ""))
            table.add_row(
                status,
                str(item.get("chunks") or 0),
                os.path.basename(str(item.get("path") or "")),
                key=str(index),
            )

        summary = data.get("summary") or {}
        generated = data.get("generated_at") or "-"
        self.query_one("#report-summary", Static).update(
            f"生成于 {generated} · 文件 {len(self._files)} · "
            f"失败 {summary.get('failed', 0)} · 跳过 {summary.get('skipped', 0)} · "
            f"chunks {summary.get('chunks', 0)}"
        )
        if self._files:
            table.focus()
            table.move_cursor(row=0)

    def _set_error(self, message: str) -> None:
        self.query_one("#report-summary", Static).update(f"加载失败：{message}")

    # ---------- 交互 ----------

    @on(DataTable.RowHighlighted, "#report-files")
    def _highlighted(self, event: DataTable.RowHighlighted) -> None:
        if not 0 <= event.cursor_row < len(self._files):
            return
        item = self._files[event.cursor_row]
        path = str(item.get("path") or "")
        if item.get("status") == "failed":
            self.query_one("#report-detail", Static).update(
                f"✗ {path}\n\n{item.get('error') or '未知错误'}"
            )
            return
        self.show_chunks(path)

    @work(thread=True, exclusive=True, group="report-chunks")
    def show_chunks(self, path: str) -> None:
        try:
            data = self.session.chunks(limit=20, source=path)
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(self._set_error, str(exc))
            return
        self.app.call_from_thread(self._render_chunks, path, data)

    def _render_chunks(self, path: str, data: dict) -> None:
        items = list(data.get("items") or [])
        lines = [f"{path}", f"chunks {data.get('total', len(items))}", ""]
        for index, item in enumerate(items[:8], start=1):
            section = item.get("section") or "-"
            text = (item.get("text") or "").replace("\n", " ")[:160]
            lines.append(f"[{index}] ({section}) {text}")
        if not items:
            lines.append("（该文件没有 chunk：可能被跳过或已移除）")
        self.query_one("#report-detail", Static).update("\n".join(lines))

    def _current(self) -> 'dict | None':
        table = self.query_one("#report-files", DataTable)
        index = table.cursor_row
        if 0 <= index < len(self._files):
            return self._files[index]
        return None

    def action_refresh(self) -> None:
        self._load()

    def action_reindex_file(self) -> None:
        item = self._current()
        if not item or item.get("status") == "removed":
            return
        path = str(item.get("path") or "")
        if not path:
            return
        self.query_one("#report-summary", Static).update(f"重建中：{path}")
        self._reindex(path)

    @work(thread=True, exclusive=True, group="report-reindex")
    def _reindex(self, path: str) -> None:
        try:
            info = self.session.reindex(path)
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(self._set_error, str(exc))
            return
        self.app.call_from_thread(
            self._after_reindex, f'重建完成：{info.get("chunks", 0)} chunks'
        )
        self._load()

    def _after_reindex(self, message: str) -> None:
        self.query_one("#report-summary", Static).update(message)

    def action_open_file(self) -> None:
        item = self._current()
        if not item:
            return
        path = str(item.get("path") or "")
        if not path or not os.path.exists(path):
            self._set_error(f"文件不存在：{path or '-'}")
            return
        editor = os.environ.get("EDITOR", "vim")
        with self.app.suspend():
            subprocess.call([editor, path])
