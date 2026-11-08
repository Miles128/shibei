"""召回诊断台（Textual TUI）。

启动：``shibei ui``，或 ``python -m shibei.tui``。

设计约束：
- 所有 sidecar IO 都进 ``@work(thread=True)``，界面永不冻结；
- 渲染只读 ``RecallSession`` 的投影，逻辑不落在 widget 里；
- chunk 文本来自本地文件，一律按纯文本渲染（``markup=False``）。
"""

from __future__ import annotations

import os
import subprocess

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Input, RichLog, Static

from .browse import BrowseScreen
from .session import FLAG_HINTS, FLAGS, RecallSession

FLAG_VARIANT = {None: "default", True: "primary", False: "error"}


class RecallApp(App):
    """查询 -> 结果表 + trace + 详情，三个面板联动。"""

    CSS = """
    Screen { layout: vertical; }
    #searchbar { height: 3; padding: 0 1; }
    #query { width: 1fr; }
    #flags { height: 3; padding: 0 1; }
    .flag { min-width: 9; margin-right: 1; }
    #body { height: 1fr; }
    #left { width: 62%; }
    #right { width: 38%; layout: vertical; border-left: solid $panel; padding: 0 1; }
    #results { height: 1fr; }
    #memories { height: 6; display: none; }
    #summary { height: auto; padding: 1 0; color: $text-muted; }
    #trace { height: 9; border: round $panel; }
    #detail { height: 1fr; overflow-y: auto; }
    """

    BINDINGS = [
        Binding("slash", "focus_query", "输入"),
        Binding("a", "browse", "全部片段"),
        Binding("f2", "browse", "全部片段"),
        Binding("b", "cycle_brain", "切换库"),
        Binding("ctrl+r", "rerun", "重跑"),
        Binding("ctrl+s", "save_eval", "存评测"),
        Binding("escape", "focus_results", "离开输入"),
        Binding("o", "open_file", "打开文件"),
        Binding("1", "toggle_flag('rewrite')", "改写"),
        Binding("2", "toggle_flag('expand')", "扩展"),
        Binding("3", "toggle_flag('fuzzy')", "模糊"),
        Binding("4", "toggle_flag('vector')", "向量"),
        Binding("5", "toggle_flag('rerank')", "重排"),
        Binding("6", "toggle_flag('mmr')", "去重"),
        Binding("7", "toggle_flag('memories')", "记忆"),
        Binding("q", "quit", "退出"),
    ]

    def __init__(
        self,
        config_path: str = "config.yaml",
        session: "RecallSession | None" = None,
        brain: "str | None" = None,
    ):
        super().__init__()
        self.config_path = config_path
        self.session = session
        self.initial_brain = brain
        self.sub_title = "连接中…"

    # ---------- 布局 ----------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="searchbar"):
            yield Input(placeholder="输入查询，回车召回…", id="query")
        with Horizontal(id="flags"):
            for key in FLAGS:
                yield Button(
                    self._flag_label(key),
                    id=f"flag-{key}",
                    classes="flag",
                    variant="default",
                )
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield DataTable(id="results", cursor_type="row", zebra_stripes=True)
                yield DataTable(id="memories", cursor_type="row", zebra_stripes=True)
            with Vertical(id="right"):
                yield Static("输入查询后回车；1-7 切换检索开关", id="summary")
                yield RichLog(id="trace", markup=True, wrap=False)
                yield Static("（无选中结果）", id="detail", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        results = self.query_one("#results", DataTable)
        results.add_columns("#", "score", "库", "路由", "来源", "片段")
        memories = self.query_one("#memories", DataTable)
        memories.add_columns("#", "score", "记忆")
        if self.session is not None and self.session.connected:
            if self.initial_brain:
                self.session.select_brain(self.initial_brain)
            self._on_ready()
        else:
            self.connect()

    def _flag_label(self, key: str) -> str:
        mark = self.session.flag_mark(key) if self.session else "·"
        return f"{key} {mark}"

    # ---------- 连接 ----------

    @work(thread=True, exclusive=True, group="connect")
    def connect(self) -> None:
        try:
            session = RecallSession(self.config_path)
            session.connect()
            if self.initial_brain:
                session.select_brain(self.initial_brain)
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self._set_error, f"连接 sidecar 失败：{exc}")
            return
        self.session = session
        self.call_from_thread(self._on_ready)

    def _on_ready(self) -> None:
        session = self.session
        brain = session.state.brain if session else "-"
        count = len(session.brains) if session else 0
        suffix = f" · {count} 个库（b 切换）" if count > 1 else ""
        self.sub_title = f"就绪 · {brain} · {self.config_path}{suffix}"
        self.query_one("#query", Input).focus()

    def _set_error(self, message: str) -> None:
        self.sub_title = "出错"
        self.query_one("#summary", Static).update(message)
        self.query_one("#trace", RichLog).write(f"[red]{message}[/red]")

    # ---------- 召回 ----------

    @on(Input.Submitted, "#query")
    def _submitted(self, event: Input.Submitted) -> None:
        self.start_search(event.value)

    def start_search(self, query: str) -> None:
        if self.session is None:
            self._set_error("sidecar 尚未就绪")
            return
        if not query.strip():
            return
        self.query_one("#trace", RichLog).clear()
        self.query_one("#summary", Static).update("检索中…")
        self.search(query)

    @work(thread=True, exclusive=True, group="search")
    def search(self, query: str) -> None:
        if self.session is None:
            return
        try:
            self.session.run(query)
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self._set_error, str(exc))
            return
        self.call_from_thread(self._render_results)

    def _render_results(self) -> None:
        session = self.session
        assert session is not None

        results = self.query_one("#results", DataTable)
        results.clear()
        for index, row in enumerate(session.rows()):
            results.add_row(*row, key=str(index))

        memories = self.query_one("#memories", DataTable)
        memory_rows = session.memory_rows()
        memories.clear()
        for index, memory_row in enumerate(memory_rows):
            memories.add_row(*memory_row, key=str(index))
        memories.display = bool(memory_rows)

        trace = self.query_one("#trace", RichLog)
        trace.clear()
        for stage in session.trace_rows():
            name = str(stage.get("name", ""))
            ms = float(stage.get("ms") or 0)
            detail = " ".join(
                f"{key}={value}"
                for key, value in stage.items()
                if key not in ("name", "ms") and value not in (None, "", {}, [])
            )
            trace.write(f"[b]{name:<10}[/b]{ms:>8.1f} ms  [dim]{detail}[/dim]")
        for note in session.trace_notes():
            trace.write(f"[yellow]{note}[/yellow]")

        self.query_one("#summary", Static).update(session.summary_line())

        if results.row_count:
            results.focus()
            results.move_cursor(row=0)
            self._show_detail(0)
        else:
            self.query_one("#detail", Static).update("（无结果）")

    # ---------- 交互 ----------

    @on(DataTable.RowHighlighted, "#results")
    def _row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self.session is None:
            return
        self.session.state.selected = event.cursor_row
        self._show_detail(event.cursor_row)

    def _show_detail(self, index: int) -> None:
        if self.session is None:
            return
        self.query_one("#detail", Static).update(self.session.detail(index))

    @on(DataTable.RowHighlighted, "#memories")
    def _memory_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self.session is None:
            return
        table = self.query_one("#memories", DataTable)
        # add_row 时的自动高亮不该抢走文档详情；只有用户真正进入记忆表才切换
        if not table.has_focus:
            return
        items = self.session.memories()
        if 0 <= event.cursor_row < len(items):
            item = items[event.cursor_row]
            self.query_one("#detail", Static).update(
                f'记忆 · {item.get("category") or "-"}\n\n{item.get("text") or ""}'
            )

    @on(Button.Pressed, ".flag")
    def _flag_pressed(self, event: Button.Pressed) -> None:
        if event.button.id:
            self.toggle_flag(event.button.id.removeprefix("flag-"))

    def toggle_flag(self, flag: str) -> None:
        if self.session is None:
            return
        session = self.session
        session.cycle_flag(flag)
        button = self.query_one(f"#flag-{flag}", Button)
        button.label = f"{flag} {session.flag_mark(flag)}"
        button.variant = FLAG_VARIANT[session.state.flags[flag]]
        button.tooltip = FLAG_HINTS.get(flag)
        if session.state.query:
            self.start_search(session.state.query)

    def action_toggle_flag(self, flag: str) -> None:
        self.toggle_flag(flag)

    def action_save_eval(self) -> None:
        if self.session is None or self.session.state.result is None:
            self._set_error("没有可保存的召回结果")
            return
        self.save_eval(self.session.result_basenames())

    @work(thread=True, group="eval")
    def save_eval(self, expect: 'list[str]') -> None:
        assert self.session is not None
        try:
            info = self.session.save_eval_sample(expect)
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self._set_error, f"保存评测样本失败：{exc}")
            return
        self.call_from_thread(self._on_eval_saved, info)

    def _on_eval_saved(self, info: dict) -> None:
        self.query_one("#summary", Static).update(
            f'已写入评测集 {info.get("path")}（{info.get("queries")} 条）'
        )

    def action_rerun(self) -> None:
        if self.session and self.session.state.query:
            self.start_search(self.session.state.query)

    def action_focus_results(self) -> None:
        self.query_one("#results", DataTable).focus()

    def action_focus_query(self) -> None:
        self.query_one("#query", Input).focus()

    def action_browse(self) -> None:
        if self.session is None:
            self._set_error("sidecar 尚未就绪")
            return
        self.push_screen(BrowseScreen(self.session))

    def action_cycle_brain(self) -> None:
        session = self.session
        if session is None:
            return
        before = session.state.brain
        after = session.cycle_brain()
        if after == before:
            self._set_error("只有 1 个库；用 shibei brains add 注册更多")
            return
        self.call_later(self._on_ready)
        if session.state.query:
            self.start_search(session.state.query)

    def action_open_file(self) -> None:
        if self.session is None:
            return
        path = self.session.current_path()
        if not path or not os.path.exists(path):
            self._set_error(f"文件不存在：{path or '-'}")
            return
        editor = os.environ.get("EDITOR", "vim")
        with self.suspend():
            subprocess.call([editor, path])


def main(config_path: str = "config.yaml", brain: "str | None" = None) -> None:
    RecallApp(config_path=config_path, brain=brain).run()


if __name__ == "__main__":
    import sys

    main(sys.argv[1] if len(sys.argv) > 1 else "config.yaml")
