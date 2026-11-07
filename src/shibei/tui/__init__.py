"""拾贝召回诊断台（Textual TUI）。

    from shibei.tui import main
    main("config.yaml")
"""

from .app import RecallApp, main
from .browse import BrowseScreen
from .session import RecallSession

__all__ = ["RecallApp", "BrowseScreen", "RecallSession", "main"]
