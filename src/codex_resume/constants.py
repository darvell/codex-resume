"""Constants and configuration values for codex-resume."""

from __future__ import annotations

# Session Discovery
MAX_PREVIEW_MESSAGES = 6
HEAD_LINE_LIMIT = 16
REMOTE_QUERY_TIMEOUT = 20
DEFAULT_MAX_AGE_DAYS = 7

# UI Timing
RELATIVE_TIME_UPDATE_INTERVAL = 5.0  # seconds
LOADING_ANIMATION_INTERVAL = 0.12  # seconds (smoother than 0.14)

# UI Dimensions
MIN_SUMMARY_WIDTH = 20
MAX_SUMMARY_WIDTH = 100
MIN_DIR_WIDTH = 10
MAX_DIR_WIDTH = 45
DIR_WIDTH_BUFFER = 2

LAST_COLUMN_WIDTH = 12
ID_COLUMN_WIDTH = 8
TABLE_PADDING_BORDERS = 12

# Text Formatting
SUMMARY_MAX_LENGTH = 160
SUMMARY_TRUNCATE_SUFFIX = "..."
PREVIEW_MAX_LENGTH = 120
PREVIEW_TRUNCATE_SUFFIX = "…"
CWD_MAX_LENGTH = 28
CWD_TRUNCATE_LENGTH = 18

# Spinner and Status Symbols
SPINNER_FRAMES = ["⠋", "⠙", "⠚", "⠞", "⠖", "⠦", "⠴", "⠲", "⠳", "⠓"]
STATUS_SYMBOLS = {
    "pending": "⏳",
    "running": "⚙️ ",
    "done": "✅",
    "error": "❌",
}

# Better loading animation - smooth spinner
LOADING_SPINNER_FRAMES = [
    "◐",
    "◓",
    "◑",
    "◒",
]

# Color scheme constants
COLOR_ACCENT = "$accent"
COLOR_SUCCESS = "$success"
COLOR_ERROR = "$error"
COLOR_MUTED = "$text-muted"
COLOR_CYAN = "cyan"
COLOR_MAGENTA = "magenta"
COLOR_GREEN = "green"
COLOR_RED = "red"
COLOR_YELLOW = "yellow"

# Hidden session marker
HIDDEN_MARKER = "████"
HIDDEN_STATUS = "HIDDEN"
