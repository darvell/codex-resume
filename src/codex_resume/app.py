from __future__ import annotations

import asyncio
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
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Static
from textual.events import Key
from textual.widgets._data_table import ColumnKey
from textual.timer import Timer

from .config import Config, RemoteServerConfig, save_config
from .sessions import Session, discover_sessions


def _generate_loading_frames() -> List[str]:
    template = [
        "   {0}   ",
        " {7}   {1} ",
        "{6}     {2}",
        " {5}   {3} ",
        "   {4}   ",
    ]
    frames: List[str] = []
    for active in range(8):
        chars = ["o"] * 8
        chars[active] = "@"
        frames.append("\n".join(row.format(*chars) for row in template))
    return frames


_LOADING_FRAMES = _generate_loading_frames()


@dataclass
class ResumeChoice:
    session: Session
    extra_args: List[str]


@dataclass
class ExtraArgsResult:
    args: List[str]
    persist: bool


class ExtraArgsScreen(ModalScreen[Optional[ExtraArgsResult]]):
    """Modal prompt that lets the user edit extra args."""

    def __init__(self, initial: List[str]) -> None:
        super().__init__()
        self._initial = " ".join(shlex.quote(arg) for arg in initial)
        self._input: Input

    def compose(self) -> ComposeResult:  # type: ignore[override]
        yield Static("Enter additional CLI arguments (leave blank for none):")
        self._input = Input(placeholder="--flag value", value=self._initial, id="extra-args-input")
        yield self._input
        yield Button(label="Save", id="save", variant="primary")
        yield Button(label="Save & Apply As Default", id="save-default", variant="success")
        yield Button(label="Cancel", id="cancel")

    def on_mount(self) -> None:
        self._input.focus()
        self._input.cursor_position = len(self._input.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._commit(persist=False)
        elif event.button.id == "save-default":
            self._commit(persist=True)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._commit(persist=False)

    def _commit(self, *, persist: bool) -> None:
        text = self._input.value.strip()
        self.dismiss(ExtraArgsResult(args=_parse_extra_args(text), persist=persist))


class OptionsScreen(ModalScreen[Optional[Config]]):
    """Modal UI for toggling configuration flags and remote servers."""

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._original = config
        self._remotes: List[RemoteServerConfig] = [
            RemoteServerConfig(target=remote.target, sessions_dir=remote.sessions_dir)
            for remote in config.remote_servers
        ]
        self._use_npx_checkbox: Checkbox
        self._target_input: Input
        self._sessions_input: Input
        self._remote_table: DataTable
        self._status: Static
        self._remove_button: Button

    def compose(self) -> ComposeResult:  # type: ignore[override]
        with Vertical(classes="modal-panel"):
            yield Static("Configuration", classes="options-title")
            self._use_npx_checkbox = Checkbox(
                "Use npx @openai/codex@latest when resuming", value=self._original.use_npx_codex
            )
            yield self._use_npx_checkbox

            yield Static("Remote servers:", classes="options-section")
            self._remote_table = DataTable(id="remote-table")
            self._remote_table.cursor_type = "row"
            self._remote_table.zebra_stripes = True
            self._remote_table.add_column("Target", key="target")
            self._remote_table.add_column("Sessions Dir", key="dir")
            yield self._remote_table
            self._populate_remote_table()

            yield Static("Add remote server (user@host):", classes="options-section")
            self._target_input = Input(placeholder="root@example.com", id="remote-target")
            yield self._target_input
            self._sessions_input = Input(
                placeholder="Sessions dir (optional, default ~/.codex/sessions)",
                id="remote-sessions",
            )
            yield self._sessions_input
            yield Button(label="Add Remote", id="add-remote", variant="primary")
            self._remove_button = Button(label="Remove Selected Remote", id="remove-selected")
            yield self._remove_button
            self._update_remove_button_state()

            self._status = Static("", classes="options-status")
            yield self._status

            yield Button(label="Save", id="save", variant="success")
            yield Button(label="Cancel", id="cancel")

    def on_mount(self) -> None:
        if self._remotes:
            self._populate_remote_table()
            self._remote_table.focus()
        else:
            self._target_input.focus()
        self._update_remove_button_state()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-remote":
            self._add_remote()
        elif event.button.id == "remove-selected":
            self._remove_selected_remote()
        elif event.button.id == "save":
            self._save()
        elif event.button.id == "cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in {"remote-target", "remote-sessions"}:
            self._add_remote()

    def _add_remote(self) -> None:
        target = self._target_input.value.strip()
        sessions_dir = self._sessions_input.value.strip() or "~/.codex/sessions"
        if not target:
            self._status.update("[red]Please enter a remote target.[/red]")
            self._target_input.focus()
            return
        self._remotes.append(RemoteServerConfig(target=target, sessions_dir=sessions_dir))
        self._target_input.value = ""
        self._sessions_input.value = ""
        self._status.update(f"Added remote [green]{escape(target)}[/green].")
        self._populate_remote_table()
        self._update_remove_button_state()
        self._remote_table.focus()

    def _remove_selected_remote(self) -> None:
        if not self._remotes:
            self._status.update("[yellow]No remote servers to remove.[/yellow]")
            return
        coordinate = self._remote_table.cursor_coordinate
        if coordinate is None:
            self._status.update("[yellow]Select a remote to remove.[/yellow]")
            return
        row_key = self._remote_table.row_keys[coordinate.row]
        try:
            index = int(row_key)
        except (TypeError, ValueError):
            self._status.update("[red]Unable to determine selected remote.[/red]")
            return
        if index < 0 or index >= len(self._remotes):
            self._status.update("[red]Selected remote is out of range.[/red]")
            return
        removed = self._remotes.pop(index)
        self._status.update(f"Removed remote [red]{escape(removed.target)}[/red].")
        self._populate_remote_table()
        self._update_remove_button_state()
        if self._remotes:
            self._remote_table.focus()
        else:
            self._target_input.focus()

    def _save(self) -> None:
        new_config = Config(
            default_extra_args=list(self._original.default_extra_args),
            remote_servers=[
                RemoteServerConfig(target=remote.target, sessions_dir=remote.sessions_dir)
                for remote in self._remotes
            ],
            use_npx_codex=self._use_npx_checkbox.value,
        )
        self.dismiss(new_config)

    def _populate_remote_table(self) -> None:
        if not hasattr(self, "_remote_table"):
            return
        self._remote_table.clear()
        for index, remote in enumerate(self._remotes):
            self._remote_table.add_row(remote.target, remote.sessions_dir, key=str(index))
        if self._remotes:
            self._remote_table.move_cursor(row=0, column=0)
        self._update_remove_button_state()

    def _update_remove_button_state(self) -> None:
        if hasattr(self, "_remove_button"):
            self._remove_button.disabled = not self._remotes



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

    .modal-panel {
        background: $panel;
        border: round $accent;
        padding: 1 2;
        min-width: 60;
    }

    .options-title {
        padding-bottom: 1;
        text-style: bold;
    }

    .options-section {
        padding: 1 0 0 0;
    }

    .options-status {
        padding: 1 0 0 0;
        color: $text-muted;
    }

    #remote-table {
        height: auto;
        margin: 1 0;
        border: tall $accent;
    }
    """

    BINDINGS = [
        Binding("enter", "resume", "Resume"),
        Binding("r", "resume", "Resume"),
        Binding("e", "edit_extra", "Extra Args"),
        Binding("o", "open_options", "Options"),
        Binding("i", "toggle_info", "Info Panel"),
        Binding("x", "toggle_hidden", "Hide"),
        Binding("f5", "refresh", "Refresh"),
        Binding("ctrl+r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        *,
        sessions_root: Optional[str] = None,
        config: Optional[Config] = None,
        extra_args: Optional[List[str]] = None,
        initial_sessions: Optional[List[Session]] = None,
    ) -> None:
        super().__init__()
        self._sessions_root = (None if sessions_root is None else sessions_root)
        self._config = config or Config()
        self._extra_args = list(extra_args or self._config.default_extra_args)
        self._session_window_days = 7
        self._sessions: List[Session] = list(initial_sessions or [])
        self._has_preloaded_sessions = initial_sessions is not None
        self._active_row_index: Optional[int] = None
        self._show_details = False
        self._hidden_sessions: set[str] = set()
        self._session_index: dict[str, int] = {}
        self._column_keys: dict[str, ColumnKey] = {}
        self._relative_timer: Optional[Timer] = None
        self._info_modal: Optional[InfoModal] = None
        self._loading_timer: Optional[Timer] = None
        self._loading_frame_index = 0
        self._loading_message = "Loading sessions…"
        self._loading_active = False

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
        if self._has_preloaded_sessions:
            self._apply_sessions(self._sessions)
            self._has_preloaded_sessions = False
        else:
            await self._reload_sessions()
        if self._sessions and hasattr(self, "_table"):
            self._table.focus()
            self._table.move_cursor(row=0, column=0)
        self._relative_timer = self.set_interval(5, self._refresh_relative_times, pause=False)

    async def on_unmount(self) -> None:
        if self._relative_timer:
            self._relative_timer.stop()
            self._relative_timer = None

    async def _reload_sessions(self) -> None:
        root_path = None if self._sessions_root is None else Path(self._sessions_root)
        message = "Scanning local sessions…"
        if self._config.remote_servers:
            message = "Scanning local & remote sessions…"
        if self._config.use_npx_codex:
            message += "\nUsing npx @openai/codex@latest"
        self._start_loading_animation(message)
        try:
            sessions = await asyncio.to_thread(
                discover_sessions,
                root=root_path,
                remotes=self._config.remote_servers,
                max_age_days=self._session_window_days,
            )
        except Exception as exc:
            self._stop_loading_animation()
            self._sessions = []
            self._session_index = {}
            self._hidden_sessions.clear()
            self._table.clear()
            self._set_active_row_index(None)
            friendly = escape(str(exc)) or "Unknown error"
            self._preview.update(f"[red]Failed to load sessions.[/red]\n{friendly}")
            return

        self._stop_loading_animation()
        self._apply_sessions(sessions)

    def _apply_sessions(self, sessions: List[Session]) -> None:
        self._sessions = sessions
        self._session_index = {session.storage_key: idx for idx, session in enumerate(self._sessions)}
        self._hidden_sessions.intersection_update(self._session_index.keys())
        if not hasattr(self, "_table"):
            return
        self._table.clear()
        for index, session in enumerate(self._sessions):
            summary_text = session.summary
            if summary_text == "No summary available" and session.preview:
                summary_text = session.preview[0][1]
            self._table.add_row(
                _format_relative(session.last_event_at or session.started_at),
                session.id[:8],
                _truncate(summary_text, 60),
                _format_session_dir(session),
                key=str(index),
            )
        self._apply_hidden_markers()
        if self._sessions:
            self._set_active_row_index(0)
            self._table.move_cursor(row=0, column=0)
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

    def action_open_options(self) -> None:
        self.push_screen(OptionsScreen(self._config), self._handle_options_result)

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

    def _handle_extra_result(self, result: Optional[ExtraArgsResult]) -> None:
        if result is None:
            return
        self._extra_args = result.args
        if result.persist:
            self._config.default_extra_args = list(result.args)
            save_config(self._config)
        session = self._current_session()
        if session and self._show_details:
            self._open_info_dialog(session)
        elif self._active_row_index is not None:
            self._update_preview(self._active_row_index)

    def _handle_options_result(self, result: Optional[Config]) -> None:
        if result is None:
            return
        self._config.use_npx_codex = result.use_npx_codex
        self._config.remote_servers = result.remote_servers
        save_config(self._config)
        self.call_after_refresh(self._async_refresh)

    def _current_session(self) -> Optional[Session]:
        if self._loading_active:
            return None
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
        key = session.storage_key
        if key in self._hidden_sessions:
            self._hidden_sessions.remove(key)
            self._set_row_values(session, hidden=False)
        else:
            self._hidden_sessions.add(key)
            self._set_row_values(session, hidden=True)
        if self._show_details:
            self._open_info_dialog(session)
        elif self._active_row_index is not None:
            self._update_preview(self._active_row_index)

    def _update_preview(self, index: int) -> None:
        if self._loading_active:
            return
        if not self._sessions or index < 0 or index >= len(self._sessions):
            self._preview.update("")
            return
        session = self._sessions[index]
        if session.storage_key in self._hidden_sessions:
            self._preview.update("Session hidden. Press X to reveal.")
            return
        lines: List[str] = []
        lines.append(f"Summary: {session.summary}")
        last_time = _format_relative(session.last_event_at or session.started_at)
        lines.append(f"Last activity {last_time}")
        dir_display = _format_session_dir(session, full=True)
        if dir_display:
            lines.append(f"Directory {dir_display}")
        if session.preview:
            for role, snippet in session.preview[:2]:
                label = (role or "msg").capitalize()
                lines.append(f"{label}: {_truncate(snippet, 70)}")
        if session.truncated:
            lines.append("Log sample is limited to the first 4KB")
        self._preview.update("\n".join(lines))

    def _open_info_dialog(self, session: Session) -> None:
        if self._loading_active:
            return
        if session.storage_key in self._hidden_sessions:
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
        index = self._session_index.get(session.storage_key)
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
                _format_session_dir(session),
            )
        for column_name, value in zip(("last", "id", "summary", "dir"), values):
            column = self._get_column(column_name)
            if column is None:
                continue
            self._table.update_cell(row_key, column.key, value)

    def _apply_hidden_markers(self) -> None:
        for session in self._sessions:
            self._set_row_values(session, hidden=session.storage_key in self._hidden_sessions)

    def _refresh_relative_times(self) -> None:
        if self._loading_active:
            return
        if not self._sessions:
            return
        last_column = self._get_column("last")
        if last_column is None:
            return
        for session in self._sessions:
            index = self._session_index.get(session.storage_key)
            if index is None or session.storage_key in self._hidden_sessions:
                continue
            row_key = str(index)
            new_value = _format_relative(session.last_event_at or session.started_at)
            self._table.update_cell(row_key, last_column.key, new_value)
        if self._active_row_index is not None:
            self._update_preview(self._active_row_index)
        if self._show_details and self._info_modal and self._current_session():
            self._open_info_dialog(self._current_session())

    def _start_loading_animation(self, message: str) -> None:
        if self._config.use_npx_codex:
            message += "\nUsing npx @openai/codex@latest"
        self._loading_message = escape(message)
        self._loading_active = True
        self._loading_frame_index = 0
        if self._loading_timer:
            self._loading_timer.stop()
        if hasattr(self, "_table"):
            self._table.disabled = True
        self._render_loading_frame()
        self._loading_timer = self.set_interval(0.14, self._advance_loading_frame, pause=False)

    def _stop_loading_animation(self) -> None:
        if self._loading_timer:
            self._loading_timer.stop()
            self._loading_timer = None
        self._loading_active = False
        if hasattr(self, "_table"):
            self._table.disabled = False

    def _advance_loading_frame(self) -> None:
        if not self._loading_active:
            return
        self._loading_frame_index = (self._loading_frame_index + 1) % len(_LOADING_FRAMES)
        self._render_loading_frame()

    def _render_loading_frame(self) -> None:
        if not self._loading_active:
            return
        if not _LOADING_FRAMES:
            self._preview.update(self._loading_message)
            return
        frame = escape(_LOADING_FRAMES[self._loading_frame_index % len(_LOADING_FRAMES)])
        self._preview.update(f"[cyan]{frame}[/cyan]\n\n{self._loading_message}")


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
    if session.remote_host:
        table.add_row("Remote Host", session.remote_host)
    if session.cwd:
        table.add_row("Working Dir", _format_session_dir(session, full=True))
    if session.cli_version:
        table.add_row("CLI Version", session.cli_version)
    if session.originator:
        table.add_row("Originator", session.originator)
    extras = " ".join(shlex.quote(arg) for arg in extra_args) or "(none)"
    table.add_row("Extra Args", extras)
    table.add_row("Log File", _format_full_path(session.path))
    events_value = (
        str(session.total_events) if session.total_events is not None else "Unknown (first 4KB scanned)"
    )
    table.add_row("Events", events_value)
    if session.truncated:
        table.add_row("Log Sample", "Limited to first 4KB")

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


def _format_session_dir(session: Session, full: bool = False) -> str:
    base = _shorten_cwd(session.cwd, full=full)
    if session.remote_host:
        prefix = f"({session.remote_host})"
        if not base or base == "-":
            return prefix
        return f"{prefix} {base}"
    return base


def _shorten_cwd(cwd: Optional[str], full: bool = False) -> str:
    if not cwd:
        return "-"
    display = _format_full_path(cwd)
    if full:
        return display

    max_len = 28
    if display.startswith("~"):
        if len(display) <= max_len:
            return display
        relative = display[2:].lstrip("/")
        if not relative:
            return "~"
        parts = relative.split("/")
        if len(parts) == 1:
            candidate = f"~/{parts[0]}"
            return candidate if len(candidate) <= max_len else candidate[: max_len - 1] + "…"
        suffix = "/".join(parts[-2:])
        candidate = f"~/{suffix}"
        if len(candidate) <= max_len:
            return candidate
        tail = parts[-1]
        short_tail = tail if len(tail) <= max_len - 2 else tail[: max_len - 3] + "…"
        return f"~/{short_tail}"

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
