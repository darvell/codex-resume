from __future__ import annotations

import shlex
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List, Optional

from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Static

from .config import Config
from .sessions import Session, discover_sessions


@dataclass
class ResumeChoice:
    session: Session
    extra_args: List[str]


class ExtraArgsScreen(ModalScreen[Optional[List[str]]]):
    """Modal prompt that lets the user edit extra args."""

    def __init__(self, initial: List[str]) -> None:
        super().__init__()
        self._initial = " ".join(shlex.quote(arg) for arg in initial)

    def compose(self) -> ComposeResult:  # type: ignore[override]
        yield Static("Enter additional CLI arguments (leave blank for none):")
        self._input = Input(placeholder="--flag value", value=self._initial)
        yield self._input
        yield Button(label="Save", id="save", variant="primary")
        yield Button(label="Cancel", id="cancel")

    def on_mount(self) -> None:
        self._input.focus()
        self._input.cursor_position = len(self._input.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            text = self._input.value.strip()
            self.dismiss(_parse_extra_args(text))
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.dismiss(_parse_extra_args(text))


class SessionsApp(App[Optional[ResumeChoice]]):
    CSS = """
    #main {
        height: 1fr;
    }

    DataTable {
        width: 70%;
    }

    #details {
        padding: 1 2;
    }

    Input {
        border: tall $accent-lighten-2;
    }
    """

    BINDINGS = [
        Binding("enter", "resume", "Resume"),
        Binding("r", "resume", "Resume"),
        Binding("e", "edit_extra", "Extra Args"),
        Binding("f5", "refresh", "Refresh"),
        Binding("ctrl+r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, *, sessions_root: Optional[str] = None, config: Optional[Config] = None, extra_args: Optional[List[str]] = None) -> None:
        super().__init__()
        self._sessions_root = (None if sessions_root is None else sessions_root)
        self._config = config or Config()
        self._extra_args = list(extra_args or self._config.default_extra_args)
        self._sessions: List[Session] = []
        self._active_row_index: Optional[int] = None

    def compose(self) -> ComposeResult:  # type: ignore[override]
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            self._table = DataTable(id="sessions")
            self._table.cursor_type = "row"
            self._table.zebra_stripes = True
            yield self._table
            self._details = Static(id="details")
            yield self._details
        yield Footer()

    async def on_mount(self) -> None:
        await self._reload_sessions()
        if self._sessions:
            self._table.focus()
            self._table.move_cursor(row=0, column=0)
            self._set_active_row_index(0)
            self._refresh_details(0)
        else:
            self._details.update("No sessions found in ~/.codex/sessions")

    async def _reload_sessions(self) -> None:
        root_path = None if self._sessions_root is None else Path(self._sessions_root)
        self._sessions = discover_sessions(root=root_path)
        self._table.clear()
        self._table.add_columns("Started", "ID", "Summary", "CWD")
        for index, session in enumerate(self._sessions):
            summary_text = session.summary
            if summary_text == "No summary available" and session.preview:
                summary_text = session.preview[0][1]
            self._table.add_row(
                _format_dt(session.started_at),
                session.id[:8],
                _truncate(summary_text, 90),
                session.cwd or "-",
                key=str(index),
            )
        if self._sessions:
            self._set_active_row_index(0)
        else:
            self._set_active_row_index(None)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        row_index = _row_key_to_index(event.row_key)
        self._set_active_row_index(row_index)
        self._refresh_details(row_index)

    def _refresh_details(self, index: int) -> None:
        if not self._sessions:
            return
        session = self._sessions[min(index, len(self._sessions) - 1)]
        detail = _render_session_detail(session, self._extra_args)
        self._details.update(detail)

    def action_resume(self) -> None:
        session = self._current_session()
        if session is None:
            return
        self.exit(ResumeChoice(session=session, extra_args=list(self._extra_args)))

    def action_edit_extra(self) -> None:
        self.push_screen(ExtraArgsScreen(self._extra_args), self._handle_extra_result)

    def action_refresh(self) -> None:
        self.call_after_refresh(self._async_refresh)

    async def _async_refresh(self) -> None:
        await self._reload_sessions()
        if self._sessions:
            self._table.move_cursor(row=0, column=0)
            self._set_active_row_index(0)
            self._refresh_details(0)

    def action_quit(self) -> None:
        self.exit(None)

    def _handle_extra_result(self, result: Optional[List[str]]) -> None:
        if result is None:
            return
        self._extra_args = result
        session = self._current_session()
        if session:
            self._details.update(_render_session_detail(session, self._extra_args))

    def _current_session(self) -> Optional[Session]:
        if not self._sessions:
            return None
        if self._active_row_index is None:
            return None
        index = max(0, min(self._active_row_index, len(self._sessions) - 1))
        return self._sessions[index]

    def _set_active_row_index(self, index: Optional[int]) -> None:
        self._active_row_index = index


def _format_dt(dt_value: datetime) -> str:
    local = dt_value.astimezone()
    return local.strftime("%Y-%m-%d %H:%M")


def _render_session_detail(session: Session, extra_args: Iterable[str]) -> Panel:
    table = Table.grid(padding=(0, 1))
    table.add_column(justify="right", style="cyan", width=12)
    table.add_column(no_wrap=True)

    table.add_row("Summary", escape(session.summary))
    table.add_row("Session ID", session.id)
    table.add_row("Started", _format_verbose_dt(session.started_at))
    if session.last_event_at:
        table.add_row("Last Event", _format_verbose_dt(session.last_event_at))
    if session.cwd:
        table.add_row("Working Dir", session.cwd)
    if session.cli_version:
        table.add_row("CLI Version", session.cli_version)
    if session.originator:
        table.add_row("Originator", session.originator)
    extras = " ".join(shlex.quote(arg) for arg in extra_args) or "(none)"
    table.add_row("Extra Args", extras)
    table.add_row("Log File", str(session.path))
    table.add_row("Events", str(session.total_events))

    if session.preview:
        preview_text = Text()
        for idx, (role, snippet) in enumerate(session.preview):
            if idx:
                preview_text.append("\n")
            label = (role or "message").strip().capitalize() or "Message"
            preview_text.append(f"{label}: ", style="bold magenta")
            preview_text.append(snippet)
        table.add_row("Preview", preview_text)

    return Panel(table, title="Session Details", border_style="green")


def _format_verbose_dt(dt_value: datetime) -> str:
    local = dt_value.astimezone()
    return local.strftime("%Y-%m-%d %H:%M:%S %Z")


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _parse_extra_args(text: str) -> List[str]:
    if not text:
        return []
    return shlex.split(text)


def _row_key_to_index(row_key: Any) -> int:
    value = row_key
    if hasattr(value, "value"):
        value = value.value
    if isinstance(value, (tuple, list)) and value:
        value = value[0]
    return int(value)
