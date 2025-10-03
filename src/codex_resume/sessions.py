from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

_DEFAULT_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"
_FILE_ID_PATTERN = re.compile(
    r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(?P<uuid>.+)\.jsonl$"
)


_MAX_PREVIEW_MESSAGES = 6


@dataclass
class Session:
    """Lightweight representation of a Codex CLI session log."""

    path: Path
    id: str
    started_at: datetime
    cwd: Optional[str]
    summary: str
    last_event_at: Optional[datetime]
    cli_version: Optional[str]
    originator: Optional[str]
    total_events: int
    preview: List[Tuple[str, str]]

    @property
    def display_time(self) -> datetime:
        return self.started_at


class SessionDiscoveryError(Exception):
    """Raised when a session file cannot be parsed."""


def discover_sessions(root: Optional[Path] = None) -> List[Session]:
    """Enumerate session files under ``root`` sorted by last activity."""

    root_path = root or _DEFAULT_SESSIONS_ROOT
    if not root_path.exists():
        return []

    sessions: List[Session] = []
    for file_path in sorted(root_path.rglob("*.jsonl")):
        try:
            session = parse_session_file(file_path)
        except SessionDiscoveryError:
            continue
        if session:
            sessions.append(session)

    sessions.sort(key=lambda s: (s.last_event_at or s.started_at), reverse=True)
    return sessions


def parse_session_file(path: Path) -> Optional[Session]:
    match = _FILE_ID_PATTERN.match(path.name)
    if not match:
        raise SessionDiscoveryError(f"Unrecognized session filename: {path.name}")
    session_id = match.group("uuid")

    started_at: Optional[datetime] = None
    cwd: Optional[str] = None
    cli_version: Optional[str] = None
    originator: Optional[str] = None
    summary: Optional[str] = None
    last_event_at: Optional[datetime] = None
    total_events = 0

    first_user_message: Optional[str] = None
    reasoning_summary: Optional[str] = None
    preview_messages: List[Tuple[str, str]] = []

    with path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            total_events += 1
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            event_timestamp = _parse_dt(payload.get("timestamp"))
            if event_timestamp:
                last_event_at = event_timestamp

            event_type = payload.get("type")
            data = payload.get("payload", {})

            if event_type == "session_meta" and started_at is None:
                started_at = _parse_dt(data.get("timestamp")) or event_timestamp or _file_timestamp(path)
                cwd = data.get("cwd")
                cli_version = data.get("cli_version")
                originator = data.get("originator")

            if event_type == "turn_context":
                context_cwd = data.get("cwd")
                if context_cwd and not cwd:
                    cwd = context_cwd

            if event_type == "response_item":
                item_type = data.get("type")
                if item_type == "message":
                    role = data.get("role")
                    text = _extract_content_text(data)
                    if text:
                        cleaned_text = _condense_preview(text)
                    else:
                        cleaned_text = ""
                    if role == "user" and text and not _is_env_context(text):
                        if first_user_message is None:
                            first_user_message = text
                    elif role == "assistant" and text and reasoning_summary is None:
                        reasoning_summary = text

                    if cleaned_text and not _is_env_context(text or ""):
                        if len(preview_messages) < _MAX_PREVIEW_MESSAGES:
                            preview_messages.append((role or "", cleaned_text))
                elif item_type == "reasoning" and not reasoning_summary:
                    summary_items = data.get("summary") or []
                    text = _extract_summary_text(summary_items)
                    if text:
                        reasoning_summary = text

    if not started_at:
        started_at = _file_timestamp(path)

    summary_text = _choose_summary(first_user_message, reasoning_summary)
    summary = summary_text or "No summary available"

    return Session(
        path=path,
        id=session_id,
        started_at=started_at,
        cwd=cwd,
        summary=summary,
        last_event_at=last_event_at,
        cli_version=cli_version,
        originator=originator,
        total_events=total_events,
        preview=preview_messages,
    )


def _extract_content_text(data: dict) -> str:
    parts: List[str] = []
    for chunk in data.get("content", []):
        if not isinstance(chunk, dict):
            continue
        if chunk.get("type") == "input_text":
            text = chunk.get("text")
            if text:
                parts.append(str(text))
    joined = "\n".join(parts).strip()
    return joined


def _extract_summary_text(summary_items: Iterable[dict]) -> Optional[str]:
    for item in summary_items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "summary_text":
            text = item.get("text")
            if text:
                return str(text)
    return None


def _choose_summary(first_user: Optional[str], reasoning: Optional[str]) -> Optional[str]:
    candidate = first_user or reasoning
    if not candidate:
        return None
    single_line = " ".join(candidate.strip().split())
    if len(single_line) > 160:
        return single_line[:157] + "..."
    return single_line


def _is_env_context(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("<environment_context>") or stripped.startswith("{\"environment_context\"")


def _condense_preview(text: str) -> str:
    single_line = " ".join(text.strip().split())
    if len(single_line) > 120:
        return single_line[:117] + "…"
    return single_line


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _file_timestamp(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
