from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import CONFIG_PATH, load_config, save_config


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

    app = CodexResumeApp(
        sessions_root=str(args.sessions_root) if args.sessions_root else None,
        config=config,
        extra_args=override_extra,
    )
    result = app.run()

    if isinstance(result, ResumeChoice):
        command = ["codex", "resume", result.session.id, *result.extra_args]
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
