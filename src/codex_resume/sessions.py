from __future__ import annotations

import json
import os
import subprocess
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from .config import RemoteServerConfig

_MAX_PREVIEW_MESSAGES = 6
_HEAD_LINE_LIMIT = 16
_REMOTE_QUERY_TIMEOUT = 20

_SESSION_COLLECTOR_SCRIPT = textwrap.dedent(
    r"""
    import json
    import os
    import re
    from pathlib import Path

    FILE_PATTERN = re.compile(r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(?P<uuid>.+)\.jsonl$")
    MAX_HEAD_LINES = int(os.environ.get("CODEX_RESUME_HEAD_LINES", "16"))
    ROOT = Path(os.environ.get("CODEX_RESUME_SESSIONS_ROOT", "~/.codex/sessions")).expanduser()

    def _read_head_lines(path):
        head_lines = []
        truncated = False
        with path.open("rb") as fh:
            for _ in range(MAX_HEAD_LINES):
                line = fh.readline()
                if not line:
                    break
                head_lines.append(line.decode("utf-8", "ignore").rstrip("\n"))
            if fh.readline():
                truncated = True
        return head_lines, truncated

    def _read_tail_line(path):
        size = path.stat().st_size
        if not size:
            return ""
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            pos = fh.tell() - 1
            buffer = bytearray()
            while pos >= 0:
                fh.seek(pos)
                byte = fh.read(1)
                if byte == b"\n":
                    if buffer:
                        break
                else:
                    buffer.extend(byte)
                pos -= 1
            if not buffer:
                fh.seek(0)
                buffer.extend(fh.read().rstrip(b"\n"))
            return bytes(reversed(buffer)).decode("utf-8", "ignore")

    def main():
        if not ROOT.exists():
            print("[]")
            return
        sessions = []
        for file_path in sorted(ROOT.rglob("*.jsonl")):
            match = FILE_PATTERN.match(file_path.name)
            if not match:
                continue
            head_lines, truncated = _read_head_lines(file_path)
            tail_line = _read_tail_line(file_path)
            stat_result = file_path.stat()
            sessions.append(
                {
                    "id": match.group("uuid"),
                    "path": str(file_path),
                    "head": head_lines,
                    "tail": tail_line,
                    "truncated": truncated,
                    "mtime": stat_result.st_mtime,
                    "size": stat_result.st_size,
                }
            )
        print(json.dumps(sessions))

    if __name__ == "__main__":
        main()
    """
)



@dataclass
class Session:
    """Lightweight representation of a Codex CLI session log."""

    path: Path | str
    id: str
    started_at: datetime
    cwd: Optional[str]
    summary: str
    last_event_at: Optional[datetime]
    cli_version: Optional[str]
    originator: Optional[str]
    total_events: Optional[int]
    preview: List[Tuple[str, str]]
    remote_host: Optional[str] = None
    truncated: bool = False

    @property
    def display_time(self) -> datetime:
        return self.started_at

    @property
    def storage_key(self) -> str:
        prefix = self.remote_host or "local"
        return f"{prefix}:{self.id}"


class SessionDiscoveryError(Exception):
    """Raised when a session file cannot be parsed."""


class RemoteSessionDiscoveryError(SessionDiscoveryError):
    """Raised when remote sessions could not be retrieved."""


ProgressCallback = Callable[[str, str], None]


def discover_sessions(
    root: Optional[Path] = None,
    remotes: Optional[Iterable[RemoteServerConfig]] = None,
    *,
    max_age_days: int = 7,
    progress: Optional[ProgressCallback] = None,
) -> List[Session]:
    """Enumerate session files under ``root`` and any configured remotes."""

    sessions: List[Session] = []
    if progress:
        progress("local", "running")
    try:
        local_payloads = _collect_local_session_payloads(root)
    except SessionDiscoveryError:
        if progress:
            progress("local", "error")
        local_payloads = []
    else:
        if progress:
            progress("local", "done")
    for payload in local_payloads:
        session = _parse_session_payload(payload, remote_host=None)
        if session:
            sessions.append(session)

    if remotes:
        remotes_list = list(remotes)
        futures = {}
        max_workers = min(8, max(1, len(remotes_list)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for remote in remotes_list:
                if progress:
                    progress(remote.target, "running")
                futures[executor.submit(_collect_remote_session_payloads, remote)] = remote
            for future in as_completed(futures):
                remote = futures[future]
                try:
                    remote_payloads = future.result()
                except RemoteSessionDiscoveryError:
                    if progress:
                        progress(remote.target, "error")
                    continue
                except Exception:
                    if progress:
                        progress(remote.target, "error")
                    continue
                if progress:
                    progress(remote.target, "done")
                for payload in remote_payloads:
                    session = _parse_session_payload(payload, remote_host=remote.target)
                    if session:
                        sessions.append(session)

    if sessions and max_age_days > 0:
        reference_dt = max(
            (session.last_event_at or session.started_at for session in sessions if session.started_at),
            default=None,
        )
        if reference_dt:
            cutoff = reference_dt - timedelta(days=max_age_days)
            sessions = [
                session
                for session in sessions
                if (session.last_event_at or session.started_at)
                and (session.last_event_at or session.started_at) >= cutoff
            ]

    sessions.sort(key=lambda s: (s.last_event_at or s.started_at), reverse=True)
    return sessions


def _collect_local_session_payloads(root: Optional[Path]) -> List[dict]:
    env = os.environ.copy()
    env["CODEX_RESUME_HEAD_LINES"] = str(_HEAD_LINE_LIMIT)
    if root:
        env["CODEX_RESUME_SESSIONS_ROOT"] = str(root)
    else:
        env.pop("CODEX_RESUME_SESSIONS_ROOT", None)

    interpreters = ("python3", "python")
    last_error: Optional[Exception] = None
    for interpreter in interpreters:
        try:
            result = subprocess.run(
                [interpreter, "-"],
                input=_SESSION_COLLECTOR_SCRIPT,
                capture_output=True,
                text=True,
                timeout=_REMOTE_QUERY_TIMEOUT,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            last_error = exc
            continue
        if result.returncode != 0:
            last_error = SessionDiscoveryError(result.stderr.strip() or f"{interpreter} exited with {result.returncode}")
            continue
        stdout = result.stdout.strip() or "[]"
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(data, list):
            return [entry for entry in data if isinstance(entry, dict)]
    if last_error:
        raise SessionDiscoveryError(str(last_error))
    return []


def _collect_remote_session_payloads(remote: RemoteServerConfig) -> List[dict]:
    env_args = [f"CODEX_RESUME_HEAD_LINES={_HEAD_LINE_LIMIT}"]
    if remote.sessions_dir:
        env_args.append(f"CODEX_RESUME_SESSIONS_ROOT={remote.sessions_dir}")

    interpreters = ("python3", "python")
    last_error: Optional[Exception] = None
    for interpreter in interpreters:
        command = ["ssh", remote.target, "env", *env_args, interpreter, "-"]
        try:
            result = subprocess.run(
                command,
                input=_SESSION_COLLECTOR_SCRIPT,
                capture_output=True,
                text=True,
                timeout=_REMOTE_QUERY_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            last_error = exc
            continue
        if result.returncode != 0:
            last_error = RemoteSessionDiscoveryError(result.stderr.strip() or f"ssh exited with {result.returncode}")
            continue
        stdout = result.stdout.strip() or "[]"
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(data, list):
            return [entry for entry in data if isinstance(entry, dict)]
    message = str(last_error) if last_error else "unknown remote session error"
    raise RemoteSessionDiscoveryError(message)


def _parse_session_payload(payload: dict, *, remote_host: Optional[str]) -> Optional[Session]:
    session_id = str(payload.get("id", "")).strip()
    if not session_id:
        return None

    path_text = str(payload.get("path", "")).strip()
    raw_head = payload.get("head") or []
    head_lines = [str(line) for line in raw_head if isinstance(line, str)]
    tail_line = payload.get("tail")
    combined_lines: List[str] = list(head_lines)
    if isinstance(tail_line, str) and tail_line:
        if tail_line not in combined_lines:
            combined_lines.append(tail_line)

    truncated_flag = bool(payload.get("truncated")) or len(head_lines) >= _HEAD_LINE_LIMIT
    fallback_dt = _datetime_from_timestamp(payload.get("mtime"))

    return _build_session_from_lines(
        session_id=session_id,
        path_text=path_text,
        lines=combined_lines,
        truncated=truncated_flag,
        fallback_dt=fallback_dt,
        remote_host=remote_host,
    )


def _build_session_from_lines(
    *,
    session_id: str,
    path_text: str,
    lines: Sequence[str],
    truncated: bool,
    fallback_dt: datetime,
    remote_host: Optional[str],
) -> Session:
    started_at: Optional[datetime] = None
    cwd: Optional[str] = None
    cli_version: Optional[str] = None
    originator: Optional[str] = None
    last_event_at: Optional[datetime] = None
    total_events = 0

    first_user_message: Optional[str] = None
    reasoning_summary: Optional[str] = None
    preview_messages: List[Tuple[str, str]] = []

    for raw_line in _iter_unique_lines(lines):
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue

        total_events += 1
        event_timestamp = _parse_dt(payload.get("timestamp"))
        if event_timestamp:
            last_event_at = event_timestamp

        event_type = payload.get("type")
        data = payload.get("payload", {})

        if event_type == "session_meta" and started_at is None:
            started_at = _parse_dt(data.get("timestamp")) or event_timestamp
            cwd = data.get("cwd") or cwd
            cli_version = data.get("cli_version") or cli_version
            originator = data.get("originator") or originator

        if event_type == "turn_context":
            context_cwd = data.get("cwd")
            if context_cwd and not cwd:
                cwd = context_cwd

        if event_type == "response_item":
            item_type = data.get("type")
            if item_type == "message":
                role = data.get("role")
                text = _extract_content_text(data)
                cleaned_text = _condense_preview(text) if text else ""
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
        started_at = fallback_dt
    if not last_event_at:
        last_event_at = fallback_dt

    summary_text = _choose_summary(first_user_message, reasoning_summary)
    summary = summary_text or "No summary available"

    if remote_host:
        path_value: Path | str = f"{remote_host}:{path_text}" if path_text else remote_host
    else:
        path_value = Path(path_text) if path_text else Path()

    total_events_value: Optional[int] = total_events if not truncated else None

    return Session(
        path=path_value,
        id=session_id,
        started_at=started_at,
        cwd=cwd,
        summary=summary,
        last_event_at=last_event_at,
        cli_version=cli_version,
        originator=originator,
        total_events=total_events_value,
        preview=preview_messages,
        remote_host=remote_host,
        truncated=truncated,
    )


def _iter_unique_lines(lines: Sequence[str]) -> Iterable[str]:
    seen: set[str] = set()
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        yield line


def _datetime_from_timestamp(raw: Optional[float]) -> datetime:
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            pass
    return datetime.fromtimestamp(0, tz=timezone.utc)


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


