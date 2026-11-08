"""Textual 诊断台冒烟测试（未安装 textual 时跳过）。"""

import pytest

pytest.importorskip("textual")

from textual.widgets import DataTable, Input, Static  # noqa: E402

from shibei.tui.app import RecallApp  # noqa: E402
from shibei.tui.browse import BrowseScreen  # noqa: E402
from shibei.tui.imports import ImportReportScreen  # noqa: E402
from shibei.tui.session import RecallSession  # noqa: E402

from tests.test_tui_session import PAYLOAD, FakeClient  # noqa: E402


@pytest.mark.asyncio
async def test_app_renders_results_and_trace():
    session = RecallSession(client=FakeClient(PAYLOAD))
    app = RecallApp(session=session)
    async with app.run_test() as pilot:
        session.state.result = PAYLOAD
        app._render_results()
        await pilot.pause()

        table = app.query_one("#results", DataTable)
        assert table.row_count == 1
        assert table.get_row_at(0)[1] == "0.83"

        summary = str(app.query_one("#summary", Static).content)
        assert "Redis 缓存" in summary

        detail = str(app.query_one("#detail", Static).content)
        assert "content#1" in detail

        memories = app.query_one("#memories", DataTable)
        assert memories.display is True
        assert memories.row_count == 1


@pytest.mark.asyncio
async def test_toggle_flag_updates_button_and_reruns():
    client = FakeClient(PAYLOAD)
    session = RecallSession(client=client)
    app = RecallApp(session=session)
    async with app.run_test() as pilot:
        app.query_one("#query", Input).value = ""
        app.toggle_flag("fuzzy")
        await pilot.pause()

        button = app.query_one("#flag-fuzzy")
        assert "✓" in str(button.label)
        # 没有历史查询时不触发检索
        assert client.calls == []

        app.query_one("#query", Input).value = "Redis"
        session.state.query = "Redis"
        app.toggle_flag("fuzzy")
        await pilot.pause()
        assert client.calls, "切换开关后应自动重跑"


@pytest.mark.asyncio
async def test_browse_screen_lists_all_chunks():
    client = FakeClient(PAYLOAD)
    session = RecallSession(client=client)
    app = RecallApp(session=session)
    async with app.run_test(size=(140, 40)) as pilot:
        app.action_browse()
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, BrowseScreen)

        table = screen.query_one("#chunks", DataTable)
        for _ in range(50):
            if table.row_count:
                break
            await pilot.pause(0.05)

        assert table.row_count == 2
        assert table.get_row_at(0)[1] == "/redis.md"
        summary = str(screen.query_one("#browse-summary", Static).content)
        assert "全部片段" in summary
        assert "共 2 条" in summary
        detail = str(screen.query_one("#chunk-detail", Static).content)
        assert "Redis" in detail
        assert client.chunk_calls


@pytest.mark.asyncio
async def test_import_report_screen_lists_files():
    session = RecallSession(client=FakeClient(PAYLOAD))
    app = RecallApp(session=session)
    async with app.run_test(size=(140, 40)) as pilot:
        app.action_imports()
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, ImportReportScreen)

        table = screen.query_one("#report-files", DataTable)
        for _ in range(50):
            if table.row_count:
                break
            await pilot.pause(0.05)

        assert table.row_count == 2
        summary = str(screen.query_one("#report-summary", Static).content)
        assert "失败 1" in summary
