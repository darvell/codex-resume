from __future__ import annotations

import shlex
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, List, Optional

from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Static
from textual.events import Key
from textual.widgets._data_table import ColumnKey
from textual.timer import Timer

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


class InfoModal(ModalScreen[None]):
    """Modal overlay presenting session details."""

    def __init__(self, panel: Panel) -> None:
        super().__init__()
        self._panel = panel
        self._body: Optional[Static] = None

    def compose(self) -> ComposeResult:  # type: ignore[override]
        self._body = Static(self._panel)
        yield self._body
        yield Static("Press Enter, Esc, or Q to close.", classes="info-footer")

    def on_key(self, event: Key) -> None:
        if event.key.lower() in {"escape", "enter", "q", "i"}:
            event.stop()
            self.dismiss(None)

    def update_panel(self, panel: Panel) -> None:
        self._panel = panel
        if self._body:
            self._body.update(panel)

class CodexResumeApp(App[Optional[ResumeChoice]]):
    TITLE = "codex-resume"
    CSS = """
    #main {
        height: 1fr;
    }

    DataTable {
        width: 100%;
    }

    #preview {
        padding: 1 1;
        height: auto;
        border-top: solid $accent;
    }

    Input {
        border: tall $accent-lighten-2;
    }

    .info-footer {
        padding: 1 2;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("enter", "resume", "Resume"),
        Binding("r", "resume", "Resume"),
        Binding("e", "edit_extra", "Extra Args"),
        Binding("i", "toggle_info", "Info Panel"),
        Binding("x", "toggle_hidden", "Hide"),
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
        self._show_details = False
        self._hidden_sessions: set[str] = set()
        self._session_index: dict[str, int] = {}
        self._column_keys: dict[str, ColumnKey] = {}
        self._relative_timer: Optional[Timer] = None
        self._info_modal: Optional[InfoModal] = None

    def compose(self) -> ComposeResult:  # type: ignore[override]
        yield Header(show_clock=False)
        with Vertical(id="main"):
            self._table = DataTable(id="sessions")
            self._table.cursor_type = "row"
            self._table.zebra_stripes = True
            yield self._table
            self._preview = Static(id="preview")
            yield self._preview
        yield Footer()

    async def on_mount(self) -> None:
        self._configure_columns()
        await self._reload_sessions()
        if self._sessions:
            self._table.focus()
            self._table.move_cursor(row=0, column=0)
            self._set_active_row_index(0)
        self._relative_timer = self.set_interval(5, self._refresh_relative_times, pause=False)

    async def on_unmount(self) -> None:
        if self._relative_timer:
            self._relative_timer.stop()
            self._relative_timer = None

    async def _reload_sessions(self) -> None:
        root_path = None if self._sessions_root is None else Path(self._sessions_root)
        self._sessions = discover_sessions(root=root_path)
        self._session_index = {session.id: idx for idx, session in enumerate(self._sessions)}
        self._table.clear()
        for index, session in enumerate(self._sessions):
            summary_text = session.summary
            if summary_text == "No summary available" and session.preview:
                summary_text = session.preview[0][1]
            self._table.add_row(
                _format_relative(session.last_event_at or session.started_at),
                session.id[:8],
                _truncate(summary_text, 60),
                _shorten_cwd(session.cwd, full=True),
                key=str(index),
            )
        self._apply_hidden_markers()
        if self._sessions:
            self._set_active_row_index(0)
            self._update_preview(0)
        else:
            self._set_active_row_index(None)
            self._preview.update("No sessions found in ~/.codex/sessions")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        row_index = _row_key_to_index(event.row_key)
        self._set_active_row_index(row_index)
        self._update_preview(row_index)

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
            self._update_preview(0)

    def action_quit(self) -> None:
        self.exit(None)

    def _handle_extra_result(self, result: Optional[List[str]]) -> None:
        if result is None:
            return
        self._extra_args = result
        session = self._current_session()
        if session and self._show_details:
            self._open_info_dialog(session)
        elif self._active_row_index is not None:
            self._update_preview(self._active_row_index)

    def _current_session(self) -> Optional[Session]:
        if not self._sessions:
            return None
        if self._active_row_index is None:
            return None
        index = max(0, min(self._active_row_index, len(self._sessions) - 1))
        return self._sessions[index]

    def _set_active_row_index(self, index: Optional[int]) -> None:
        self._active_row_index = index

    def action_toggle_info(self) -> None:
        self._show_details = not self._show_details
        session = self._current_session()
        if self._show_details and session:
            self._open_info_dialog(session)
        else:
            self._show_details = False

    def action_toggle_hidden(self) -> None:
        session = self._current_session()
        if session is None:
            return
        if session.id in self._hidden_sessions:
            self._hidden_sessions.remove(session.id)
            self._set_row_values(session, hidden=False)
        else:
            self._hidden_sessions.add(session.id)
            self._set_row_values(session, hidden=True)
        if self._show_details:
            self._open_info_dialog(session)
        elif self._active_row_index is not None:
            self._update_preview(self._active_row_index)

    def _update_preview(self, index: int) -> None:
        if not self._sessions or index < 0 or index >= len(self._sessions):
            self._preview.update("")
            return
        session = self._sessions[index]
        if session.id in self._hidden_sessions:
            self._preview.update("Session hidden. Press X to reveal.")
            return
        lines: List[str] = []
        lines.append(f"Summary: {session.summary}")
        last_time = _format_relative(session.last_event_at or session.started_at)
        lines.append(f"Last activity {last_time}")
        if session.cwd:
            lines.append(f"Directory {_shorten_cwd(session.cwd, full=True)}")
        if session.preview:
            for role, snippet in session.preview[:2]:
                label = (role or "msg").capitalize()
                lines.append(f"{label}: {_truncate(snippet, 70)}")
        self._preview.update("\n".join(lines))

    def _open_info_dialog(self, session: Session) -> None:
        if session.id in self._hidden_sessions:
            panel = Panel("Session hidden. Press X to reveal.", title="Session Hidden", border_style="red")
        else:
            panel = _render_session_detail(session, self._extra_args)
        if self._info_modal:
            self._info_modal.update_panel(panel)
            return
        modal = InfoModal(panel)
        self._info_modal = modal
        self.push_screen(modal, self._dismiss_info)

    def _dismiss_info(self, _: Optional[None]) -> None:
        self._show_details = False
        self._info_modal = None

    def _configure_columns(self) -> None:
        self._table.clear(columns=True)
        self._column_keys = {
            "last": self._table.add_column("Last", width=12),
            "id": self._table.add_column("ID", width=6),
            "summary": self._table.add_column("Summary"),
            "dir": self._table.add_column("Dir"),
        }
        for name in ("last", "id"):
            column = self._get_column(name)
            if column:
                column.auto_width = False
        dir_column = self._get_column("dir")
        if dir_column:
            dir_column.auto_width = True

    def _get_column(self, name: str):
        key = self._column_keys.get(name)
        if key is None:
            return None
        return self._table.columns.get(key)

    def _set_row_values(self, session: Session, hidden: bool) -> None:
        index = self._session_index.get(session.id)
        if index is None:
            return
        row_key = str(index)
        if hidden:
            values = ("HIDDEN", "████████", "████████████", "████████████")
        else:
            values = (
                _format_relative(session.last_event_at or session.started_at),
                session.id[:8],
                _truncate(session.summary, 60),
                _shorten_cwd(session.cwd, full=True),
            )
        for column_name, value in zip(("last", "id", "summary", "dir"), values):
            column = self._get_column(column_name)
            if column is None:
                continue
            self._table.update_cell(row_key, column.key, value)

    def _apply_hidden_markers(self) -> None:
        for session in self._sessions:
            self._set_row_values(session, hidden=session.id in self._hidden_sessions)

    def _refresh_relative_times(self) -> None:
        if not self._sessions:
            return
        last_column = self._get_column("last")
        if last_column is None:
            return
        for session in self._sessions:
            index = self._session_index.get(session.id)
            if index is None or session.id in self._hidden_sessions:
                continue
            row_key = str(index)
            new_value = _format_relative(session.last_event_at or session.started_at)
            self._table.update_cell(row_key, last_column.key, new_value)
        if self._active_row_index is not None:
            self._update_preview(self._active_row_index)
        if self._show_details and self._info_modal and self._current_session():
            self._open_info_dialog(self._current_session())


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
        table.add_row("Last Event", f"{_format_verbose_dt(session.last_event_at)} ({_format_relative(session.last_event_at)})")
    if session.cwd:
        table.add_row("Working Dir", _shorten_cwd(session.cwd, full=True))
    if session.cli_version:
        table.add_row("CLI Version", session.cli_version)
    if session.originator:
        table.add_row("Originator", session.originator)
    extras = " ".join(shlex.quote(arg) for arg in extra_args) or "(none)"
    table.add_row("Extra Args", extras)
    table.add_row("Log File", _format_full_path(session.path))
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


def _format_relative(dt_value: datetime) -> str:
    now = datetime.now(timezone.utc)
    target = dt_value.astimezone(timezone.utc)
    delta = now - target
    if delta < timedelta(seconds=0):
        delta = timedelta(seconds=0)
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        rem_minutes = minutes % 60
        if rem_minutes:
            return f"{hours}h {rem_minutes}m ago"
        return f"{hours}h ago"
    days = hours // 24
    if days < 14:
        return f"{days}d ago"
    weeks = days // 7
    if weeks < 8:
        return f"{weeks}w ago"
    months = days // 30
    if months < 18:
        return f"{months}mo ago"
    years = days // 365
    return f"{years}y ago"


def _format_full_path(path_value: Optional[str | Path]) -> str:
    if path_value is None:
        return "-"
    text = str(path_value)
    try:
        home = str(Path.home())
    except RuntimeError:
        home = None
    if home and text.startswith(home):
        text = "~" + text[len(home) :]
    return text


def _shorten_cwd(cwd: Optional[str], full: bool = False) -> str:
    if not cwd:
        return "-"
    display = _format_full_path(cwd)
    if full:
        return display
    path = Path(cwd)
    candidate = path.name or str(path)
    if len(candidate) <= 18:
        return candidate
    return candidate[:8] + "…" + candidate[-8:]


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
