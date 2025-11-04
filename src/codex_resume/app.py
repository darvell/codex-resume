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
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Select, Static
from textual.events import Key
from textual.widgets._data_table import ColumnKey
from textual.timer import Timer

from .config import Config, RemoteServerConfig, save_config
from .constants import (
    COLOR_CYAN,
    COLOR_GREEN,
    COLOR_MAGENTA,
    COLOR_MUTED,
    COLOR_RED,
    COLOR_YELLOW,
    CWD_MAX_LENGTH,
    CWD_TRUNCATE_LENGTH,
    DEFAULT_MAX_AGE_DAYS,
    DIR_WIDTH_BUFFER,
    HIDDEN_MARKER,
    HIDDEN_STATUS,
    ID_COLUMN_WIDTH,
    LAST_COLUMN_WIDTH,
    LOADING_ANIMATION_INTERVAL,
    LOADING_SPINNER_FRAMES,
    MAX_DIR_WIDTH,
    MAX_SUMMARY_WIDTH,
    MIN_DIR_WIDTH,
    MIN_SUMMARY_WIDTH,
    RELATIVE_TIME_UPDATE_INTERVAL,
    TABLE_PADDING_BORDERS,
)
from .sessions import Session, discover_sessions


def _generate_loading_frames() -> List[str]:
    """Generate elegant loading animation frames."""
    frames: List[str] = []
    for spinner in LOADING_SPINNER_FRAMES:
        frame_lines = [
            "    ╔══════════════╗",
            f"    ║  {spinner}  Loading  {spinner}  ║",
            "    ╚══════════════╝",
        ]
        frames.append("\n".join(frame_lines))
    return frames


_LOADING_FRAMES = _generate_loading_frames()


@dataclass
class ResumeChoice:
    session: Session
    extra_args: List[str]


class OptionsScreen(ModalScreen[Optional[Config]]):
    """Modal UI for toggling configuration flags and remote servers."""

    def __init__(self, config: Config, extra_args: List[str]) -> None:
        super().__init__()
        self._original = config
        self._extra_args = list(extra_args)
        self._remotes: List[RemoteServerConfig] = [
            RemoteServerConfig(target=remote.target, sessions_dir=remote.sessions_dir)
            for remote in config.remote_servers
        ]
        self._use_npx_checkbox: Checkbox
        self._npx_version_select: Select[str]
        self._extra_args_input: Input
        self._target_input: Input
        self._sessions_input: Input
        self._remote_table: DataTable
        self._status: Static
        self._remove_button: Button

    def compose(self) -> ComposeResult:  # type: ignore[override]
        from textual.widgets import Select

        with Vertical(classes="modal-panel"):
            yield Static("Configuration", classes="options-title")

            with Horizontal(classes="config-columns"):
                # Left column: NPX settings and extra args
                with Vertical(classes="config-column"):
                    yield Static("NPX Settings", classes="column-header")
                    self._use_npx_checkbox = Checkbox(
                        "Use npx @openai/codex", value=self._original.use_npx_codex
                    )
                    yield self._use_npx_checkbox

                    self._npx_version_select = Select[str](
                        options=[
                            ("@latest", "latest"),
                            ("@alpha", "alpha"),
                        ],
                        value=self._original.npx_version,
                        allow_blank=False,
                        id="npx-version-select",
                    )
                    yield self._npx_version_select

                    yield Static("Extra CLI Arguments", classes="column-header extra-args-header")
                    extra_args_value = " ".join(shlex.quote(arg) for arg in self._extra_args)
                    self._extra_args_input = Input(
                        placeholder="--flag value",
                        value=extra_args_value,
                        id="extra-args-input"
                    )
                    yield self._extra_args_input

                # Right column: Remote servers
                with Vertical(classes="config-column"):
                    yield Static("Remote Servers", classes="column-header")
                    self._remote_table = DataTable(id="remote-table")
                    self._remote_table.cursor_type = "row"
                    self._remote_table.zebra_stripes = True
                    self._remote_table.add_column("Target", key="target")
                    self._remote_table.add_column("Dir", key="dir")
                    yield self._remote_table
                    self._populate_remote_table()

                    self._target_input = Input(placeholder="user@host", id="remote-target")
                    yield self._target_input
                    self._sessions_input = Input(
                        placeholder="~/.codex/sessions",
                        id="remote-sessions",
                    )
                    yield self._sessions_input

                    with Horizontal(classes="remote-buttons"):
                        yield Button(label="Add", id="add-remote", variant="primary")
                        self._remove_button = Button(label="Remove", id="remove-selected")
                        yield self._remove_button
                    self._update_remove_button_state()

            self._status = Static("", classes="options-status")
            yield self._status

            with Horizontal(classes="action-buttons"):
                yield Button(label="Save", id="save", variant="success")
                yield Button(label="Cancel", id="cancel")

    def on_mount(self) -> None:
        self._use_npx_checkbox.focus()
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
        # Use the row coordinate directly as the index
        index = coordinate.row
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
        npx_version = self._npx_version_select.value or "latest"
        extra_args_text = self._extra_args_input.value.strip()
        extra_args = _parse_extra_args(extra_args_text)
        new_config = Config(
            default_extra_args=list(extra_args),
            remote_servers=[
                RemoteServerConfig(target=remote.target, sessions_dir=remote.sessions_dir)
                for remote in self._remotes
            ],
            use_npx_codex=self._use_npx_checkbox.value,
            npx_version=npx_version,
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
    /* Main layout */
    #main {
        height: 1fr;
    }

    DataTable {
        width: 100%;
    }

    /* Sessions table - enhanced styling */
    #sessions {
        border: heavy $accent;
        background: $surface;
    }

    #sessions:focus {
        border: heavy $accent-lighten-1;
    }

    /* Preview panel - more elegant */
    #preview {
        padding: 1 2;
        height: auto;
        min-height: 8;
        border-top: double $accent;
        overflow-y: auto;
        background: $panel;
    }

    /* Input fields */
    Input {
        border: round $accent-lighten-2;
        margin: 0;
    }

    Input:focus {
        border: round $accent;
    }

    /* Info modal footer */
    .info-footer {
        padding: 1 2;
        color: $text-muted;
        text-align: center;
    }

    /* Modal panel - enhanced */
    .modal-panel {
        background: $panel;
        border: heavy $accent;
        padding: 1 2;
        min-width: 70;
        max-width: 95;
    }

    /* Options title - centered and prominent */
    .options-title {
        padding: 1 0;
        margin: 0 0 1 0;
        text-style: bold;
        color: $accent;
        text-align: center;
        border-bottom: solid $accent-lighten-2;
    }

    /* Config columns layout */
    .config-columns {
        width: 100%;
        height: auto;
        margin: 1 0;
    }

    .config-column {
        width: 1fr;
        height: auto;
        padding: 0 1;
    }

    .config-column:first-child {
        padding-left: 0;
        border-right: solid $panel-lighten-1;
    }

    .config-column:last-child {
        padding-right: 0;
    }

    /* Column headers */
    .column-header {
        text-style: bold;
        color: $accent-lighten-1;
        padding: 0 0 1 0;
        margin: 1 0 0 0;
        border-bottom: solid $panel-lighten-1;
    }

    .extra-args-header {
        margin-top: 1;
        padding-top: 0;
    }

    /* Options status line */
    .options-status {
        padding: 1 0 0 0;
        color: $text-muted;
        min-height: 1;
        margin: 0;
        height: auto;
        border-top: solid $panel-lighten-1;
    }

    /* NPX version selector */
    #npx-version-select {
        margin: 0 0 1 0;
        border: round $accent-lighten-2;
    }

    /* Remote table styling */
    #remote-table {
        height: auto;
        max-height: 10;
        margin: 0 0 1 0;
        border: round $accent;
    }

    /* Checkbox styling */
    Checkbox {
        margin: 0 0 1 0;
    }

    /* Button spacing */
    Button {
        margin: 0 1 0 0;
        min-width: 12;
    }

    Button:hover {
        text-style: bold;
    }

    #save {
        background: $success;
        border: round $success-lighten-1;
    }

    #save:hover {
        background: $success-lighten-1;
    }

    #cancel {
        background: $error 30%;
        border: round $error;
    }

    #cancel:hover {
        background: $error 40%;
    }

    /* Remote buttons container */
    .remote-buttons {
        width: 100%;
        height: auto;
        margin: 0;
        padding: 0;
    }

    .remote-buttons > Button {
        margin: 0 1 0 0;
    }

    .remote-buttons > Button:last-child {
        margin-right: 0;
    }

    /* Action buttons container */
    .action-buttons {
        width: 100%;
        height: auto;
        margin: 1 0 0 0;
        padding: 1 0 0 0;
        border-top: solid $accent-lighten-2;
    }

    .action-buttons > Button {
        margin: 0 1 0 0;
    }

    .action-buttons > Button:last-child {
        margin-right: 0;
    }

    /* Header and Footer enhancements */
    Header {
        background: $panel;
        border-bottom: double $accent;
    }

    Footer {
        background: $panel;
        border-top: double $accent;
    }
    """

    BINDINGS = [
        Binding("enter", "resume", "Resume"),
        Binding("r", "resume", "Resume"),
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
        self._session_window_days = DEFAULT_MAX_AGE_DAYS
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
        # Performance caching
        self._relative_time_cache: dict[str, str] = {}
        self._dir_format_cache: dict[str, str] = {}

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
        self._relative_timer = self.set_interval(
            RELATIVE_TIME_UPDATE_INTERVAL, self._refresh_relative_times, pause=False
        )

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
            message += f"\nUsing npx @openai/codex@{self._config.npx_version}"
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
        # Clear caches when reloading sessions
        self._relative_time_cache.clear()
        self._dir_format_cache.clear()
        self._apply_sessions(sessions)

    def _apply_sessions(self, sessions: List[Session]) -> None:
        import shutil

        self._sessions = sessions
        self._session_index = {session.storage_key: idx for idx, session in enumerate(self._sessions)}
        self._hidden_sessions.intersection_update(self._session_index.keys())
        if not hasattr(self, "_table"):
            return

        # Get terminal width
        try:
            terminal_width = shutil.get_terminal_size().columns
        except (AttributeError, ValueError):
            terminal_width = 80

        # Calculate max dir width needed
        max_dir_width = MIN_DIR_WIDTH
        for session in self._sessions:
            dir_text = _format_session_dir(session)
            max_dir_width = max(max_dir_width, len(dir_text))

        # Add extra buffer for remote host prefix and formatting
        max_dir_width += DIR_WIDTH_BUFFER

        # Set a reasonable max (don't let it take up too much space)
        max_dir_width = min(max_dir_width, MAX_DIR_WIDTH)
        max_dir_width = max(max_dir_width, MIN_DIR_WIDTH)

        # Calculate summary width based on remaining space
        summary_width = (
            terminal_width
            - LAST_COLUMN_WIDTH
            - ID_COLUMN_WIDTH
            - max_dir_width
            - TABLE_PADDING_BORDERS
        )
        summary_width = max(summary_width, MIN_SUMMARY_WIDTH)
        summary_width = min(summary_width, MAX_SUMMARY_WIDTH)

        # Update column widths - all fixed now
        dir_column = self._get_column("dir")
        summary_column = self._get_column("summary")
        if dir_column:
            dir_column.width = max_dir_width
            dir_column.auto_width = False
        if summary_column:
            summary_column.width = summary_width
            summary_column.auto_width = False

        self._table.clear()
        for index, session in enumerate(self._sessions):
            summary_text = session.summary
            if summary_text == "No summary available" and session.preview:
                summary_text = session.preview[0][1]
            self._table.add_row(
                _format_relative(session.last_event_at or session.started_at),
                session.id[:8],
                summary_text,
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

    def action_open_options(self) -> None:
        self.push_screen(OptionsScreen(self._config, self._extra_args), self._handle_options_result)

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

    def _handle_options_result(self, result: Optional[Config]) -> None:
        if result is None:
            return

        # Check if remote servers changed
        old_remotes = [(r.target, r.sessions_dir) for r in self._config.remote_servers]
        new_remotes = [(r.target, r.sessions_dir) for r in result.remote_servers]
        remotes_changed = old_remotes != new_remotes

        self._config.use_npx_codex = result.use_npx_codex
        self._config.npx_version = result.npx_version
        self._config.remote_servers = result.remote_servers
        self._config.default_extra_args = result.default_extra_args
        self._extra_args = list(result.default_extra_args)
        save_config(self._config)

        # Only reload sessions if remote servers changed
        if remotes_changed:
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

        # Use a styled table grid for better layout
        table = Table.grid(padding=(0, 1))
        table.add_column(justify="right", style=f"{COLOR_CYAN} bold", width=14)
        table.add_column()

        # Core info with better formatting
        table.add_row("Summary", f"[bold]{escape(session.summary)}[/bold]")
        table.add_row("Session ID", f"[{COLOR_MUTED}]{session.id}[/{COLOR_MUTED}]")

        last_time = _format_relative(session.last_event_at or session.started_at)
        table.add_row("Last Activity", f"[{COLOR_YELLOW}]{last_time}[/{COLOR_YELLOW}]")

        dir_display = _format_session_dir(session, full=True)
        if dir_display and dir_display != "-":
            table.add_row("Directory", f"[{COLOR_CYAN}]{dir_display}[/{COLOR_CYAN}]")

        if session.remote_host:
            table.add_row("Remote Host", f"[{COLOR_MAGENTA}]{session.remote_host}[/{COLOR_MAGENTA}]")

        # Preview messages with role-based colors
        if session.preview:
            for idx, (role, snippet) in enumerate(session.preview[:4]):
                label = (role or "msg").capitalize()
                role_color = COLOR_GREEN if role == "assistant" else COLOR_CYAN
                display_label = f"{label} {idx + 1}" if len(session.preview) > 2 else label
                table.add_row(display_label, f"[{role_color}]{snippet}[/{role_color}]")

        if session.truncated:
            table.add_row("Note", f"[dim {COLOR_YELLOW}]Log sample limited to first 4KB[/dim {COLOR_YELLOW}]")

        self._preview.update(table)

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
        """Configure table columns with fixed widths."""
        self._table.clear(columns=True)
        self._column_keys = {
            "last": self._table.add_column("Last", width=LAST_COLUMN_WIDTH),
            "id": self._table.add_column("ID", width=ID_COLUMN_WIDTH),
            "summary": self._table.add_column("Summary"),
            "dir": self._table.add_column("Dir", width=MIN_DIR_WIDTH),
        }
        # Fixed width columns (Last, ID)
        for name in ("last", "id"):
            column = self._get_column(name)
            if column:
                column.auto_width = False
        # Summary column will stretch to fill (set in _apply_sessions)
        # Dir column width will be calculated dynamically based on content

    def _get_column(self, name: str):
        key = self._column_keys.get(name)
        if key is None:
            return None
        return self._table.columns.get(key)

    def _set_row_values(self, session: Session, hidden: bool) -> None:
        """Update row values with optional hidden marker."""
        index = self._session_index.get(session.storage_key)
        if index is None:
            return
        row_key = str(index)

        if hidden:
            values = (HIDDEN_STATUS, HIDDEN_MARKER * 2, HIDDEN_MARKER * 3, HIDDEN_MARKER * 3)
        else:
            relative_time = _format_relative(session.last_event_at or session.started_at)
            # Cache the relative time
            self._relative_time_cache[session.storage_key] = relative_time
            values = (
                relative_time,
                session.id[:ID_COLUMN_WIDTH],
                session.summary,  # Don't truncate, let it fill available space
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
        """Refresh relative time stamps with caching for better performance."""
        if self._loading_active or not self._sessions:
            return

        last_column = self._get_column("last")
        if last_column is None:
            return

        # Batch update cells that have changed
        updates_needed = []
        for session in self._sessions:
            if session.storage_key in self._hidden_sessions:
                continue

            index = self._session_index.get(session.storage_key)
            if index is None:
                continue

            new_value = _format_relative(session.last_event_at or session.started_at)
            cached_value = self._relative_time_cache.get(session.storage_key)

            # Only update if value changed
            if cached_value != new_value:
                self._relative_time_cache[session.storage_key] = new_value
                updates_needed.append((str(index), new_value))

        # Apply batched updates
        for row_key, new_value in updates_needed:
            self._table.update_cell(row_key, last_column.key, new_value)

        if self._active_row_index is not None:
            self._update_preview(self._active_row_index)
        if self._show_details and self._info_modal and self._current_session():
            self._open_info_dialog(self._current_session())

    def _start_loading_animation(self, message: str) -> None:
        self._loading_message = escape(message)
        self._loading_active = True
        self._loading_frame_index = 0
        if self._loading_timer:
            self._loading_timer.stop()
        if hasattr(self, "_table"):
            self._table.disabled = True
        self._render_loading_frame()
        self._loading_timer = self.set_interval(
            LOADING_ANIMATION_INTERVAL, self._advance_loading_frame, pause=False
        )

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
    """Render detailed session information in a styled panel."""
    table = Table.grid(padding=(0, 1))
    table.add_column(justify="right", style=f"{COLOR_CYAN} bold", width=14)
    table.add_column(no_wrap=True)

    table.add_row("Summary", f"[bold]{escape(session.summary)}[/bold]")
    table.add_row("Session ID", f"[{COLOR_MUTED}]{session.id}[/{COLOR_MUTED}]")
    table.add_row("Started", f"[{COLOR_YELLOW}]{_format_verbose_dt(session.started_at)}[/{COLOR_YELLOW}]")

    if session.last_event_at:
        relative = _format_relative(session.last_event_at)
        table.add_row(
            "Last Event",
            f"[{COLOR_YELLOW}]{_format_verbose_dt(session.last_event_at)}[/{COLOR_YELLOW}] [{COLOR_MUTED}]({relative})[/{COLOR_MUTED}]",
        )

    if session.remote_host:
        table.add_row("Remote Host", f"[{COLOR_MAGENTA}]{session.remote_host}[/{COLOR_MAGENTA}]")

    if session.cwd:
        table.add_row("Working Dir", f"[{COLOR_CYAN}]{_format_session_dir(session, full=True)}[/{COLOR_CYAN}]")

    if session.cli_version:
        table.add_row("CLI Version", f"[{COLOR_GREEN}]{session.cli_version}[/{COLOR_GREEN}]")

    if session.originator:
        table.add_row("Originator", session.originator)

    extras = " ".join(shlex.quote(arg) for arg in extra_args) or "(none)"
    table.add_row("Extra Args", f"[{COLOR_MUTED}]{extras}[/{COLOR_MUTED}]")
    table.add_row("Log File", f"[{COLOR_MUTED}]{_format_full_path(session.path)}[/{COLOR_MUTED}]")

    events_value = (
        str(session.total_events) if session.total_events is not None else "Unknown (first 4KB scanned)"
    )
    table.add_row("Events", events_value)

    if session.truncated:
        table.add_row("Log Sample", f"[{COLOR_YELLOW}]Limited to first 4KB[/{COLOR_YELLOW}]")

    if session.preview:
        preview_text = Text()
        for idx, (role, snippet) in enumerate(session.preview):
            if idx:
                preview_text.append("\n")
            label = (role or "message").strip().capitalize() or "Message"
            style = f"bold {COLOR_GREEN}" if role == "assistant" else f"bold {COLOR_MAGENTA}"
            preview_text.append(f"{label}: ", style=style)
            preview_text.append(snippet)
        table.add_row("Preview", preview_text)

    return Panel(table, title="[bold]Session Details[/bold]", border_style=COLOR_GREEN)


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
    """Shorten working directory path for display."""
    if not cwd:
        return "-"
    display = _format_full_path(cwd)
    if full:
        return display

    # Handle home directory paths
    if display.startswith("~"):
        if len(display) <= CWD_MAX_LENGTH:
            return display
        relative = display[2:].lstrip("/")
        if not relative:
            return "~"
        parts = relative.split("/")
        if len(parts) == 1:
            candidate = f"~/{parts[0]}"
            return candidate if len(candidate) <= CWD_MAX_LENGTH else candidate[: CWD_MAX_LENGTH - 1] + "…"
        suffix = "/".join(parts[-2:])
        candidate = f"~/{suffix}"
        if len(candidate) <= CWD_MAX_LENGTH:
            return candidate
        tail = parts[-1]
        short_tail = tail if len(tail) <= CWD_MAX_LENGTH - 2 else tail[: CWD_MAX_LENGTH - 3] + "…"
        return f"~/{short_tail}"

    # Handle absolute paths
    path = Path(cwd)
    candidate = path.name or str(path)
    if len(candidate) <= CWD_TRUNCATE_LENGTH:
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
