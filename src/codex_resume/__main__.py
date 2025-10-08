from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import __version__
from .config import CONFIG_PATH, load_config, save_config
from .sessions import discover_sessions

SPINNER_FRAMES = ["⠋", "⠙", "⠚", "⠞", "⠖", "⠦", "⠴", "⠲", "⠳", "⠓"]
STATUS_SYMBOLS = {
    "pending": "…",
    "running": "…",
    "done": "✓",
    "error": "✗",
}


def _format_status_line(prefix: str, status_map: Dict[str, str], lock: threading.Lock) -> str:
    with lock:
        items = list(status_map.items())
    segments = []
    for name, state in items:
        symbol = STATUS_SYMBOLS.get(state, state)
        segments.append(f"({name}: {symbol})")
    status_text = " ".join(segments)
    return f"\r{prefix} Reading your codex history... {status_text}"


def _spinner_worker(status_map: Dict[str, str], lock: threading.Lock, stop_event: threading.Event) -> None:
    idx = 0
    while not stop_event.is_set():
        line = _format_status_line(SPINNER_FRAMES[idx % len(SPINNER_FRAMES)], status_map, lock)
        print(line, end="", flush=True)
        idx += 1
        if stop_event.wait(0.1):
            break
    final_line = _format_status_line("✔", status_map, lock)
    print(final_line, end="", flush=True)


def _parse_cli(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Browse and resume Codex CLI sessions with a TUI.")
    parser.add_argument(
        "--sessions-root",
        type=Path,
        help="Override the sessions directory (defaults to ~/.codex/sessions).",
    )
    parser.add_argument(
        "--extra",
        metavar="ARGS",
        help="Temporarily override extra arguments passed to `codex resume` (quoted string).",
    )
    parser.add_argument(
        "--set-default-extra",
        metavar="ARGS",
        help="Persist default extra arguments (quoted string).",
    )
    parser.add_argument(
        "--show-config-path",
        action="store_true",
        help="Print the config file location and exit.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the codex-resume version and exit.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_cli(argv)
    if args.version:
        print(__version__)
        return 0

    if args.show_config_path:
        print(CONFIG_PATH)
        return 0

    config = load_config()

    if args.set_default_extra is not None:
        config.default_extra_args = _split_args(args.set_default_extra)
        save_config(config)
        print(f"Saved default extra arguments to {CONFIG_PATH}")
        return 0

    override_extra = _split_args(args.extra) if args.extra is not None else None

    from .app import CodexResumeApp, ResumeChoice

    sessions_root_str = str(args.sessions_root) if args.sessions_root else None

    status_map: Dict[str, str] = {}
    lock = threading.Lock()
    spinner_stop = threading.Event()

    def _register_source(name: str, initial: str = "pending") -> None:
        with lock:
            status_map[name] = initial

    def _progress(source: str, state: str) -> None:
        with lock:
            status_map[source] = state

    sources: List[str] = ["local"]
    for remote in config.remote_servers:
        sources.append(remote.target)
    for name in sources:
        _register_source(name)

    spinner_thread = threading.Thread(
        target=_spinner_worker,
        args=(status_map, lock, spinner_stop),
        daemon=True,
    )
    spinner_thread.start()

    try:
        initial_sessions = discover_sessions(
            root=args.sessions_root,
            remotes=config.remote_servers,
            max_age_days=7,
            progress=_progress,
        )
        _progress("local", "done")
    except Exception as exc:
        _progress("local", "error")
        print(f"\nWarning: failed to load sessions: {exc}", file=sys.stderr)
        initial_sessions = []
    finally:
        spinner_stop.set()
        spinner_thread.join()
        print()  # ensure the spinner line is terminated

    app = CodexResumeApp(
        sessions_root=sessions_root_str,
        config=config,
        extra_args=override_extra,
        initial_sessions=initial_sessions,
    )
    result = app.run()

    if isinstance(result, ResumeChoice):
        if config.use_npx_codex:
            base = ["npx", "--yes", "@openai/codex@latest"]
        else:
            base = ["codex"]
        command = [*base, *result.extra_args, "resume", result.session.id]

        if result.session.remote_host:
            ssh_target = result.session.remote_host
            remote_command = " ".join(shlex.quote(part) for part in command)
            if result.session.cwd:
                print(f"Remote working directory: {result.session.cwd}")
                remote_command = f"cd {shlex.quote(result.session.cwd)} && {remote_command}"
            ssh_args = [
                "ssh",
                "-tt",
                ssh_target,
                "bash",
                "-lc",
                remote_command,
            ]
            pretty_command = f"ssh -tt {shlex.quote(ssh_target)} bash -lc {shlex.quote(remote_command)}"
            print(f"Launching: {pretty_command}")
            try:
                code = subprocess.call(ssh_args)
            except FileNotFoundError:
                print("Error: `ssh` command not found in PATH.", file=sys.stderr)
                return 127
            return code
        else:
            pretty_command = " ".join(shlex.quote(part) for part in command)
            print(f"Launching: {pretty_command}")
            cwd_arg: Optional[str] = None
            if result.session.cwd:
                cwd_path = Path(result.session.cwd)
                if cwd_path.is_dir():
                    cwd_arg = str(cwd_path)
                    print(f"Working directory: {cwd_arg}")
                else:
                    print(
                        f"Warning: logged working directory '{result.session.cwd}' no longer exists; running in current directory.",
                        file=sys.stderr,
                    )
            try:
                code = subprocess.call(command, cwd=cwd_arg)
            except FileNotFoundError:
                print("Error: `codex` command not found in PATH.", file=sys.stderr)
                return 127
            return code

    return 0


def _split_args(raw: str) -> List[str]:
    raw = raw.strip()
    if not raw:
        return []
    return shlex.split(raw)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
