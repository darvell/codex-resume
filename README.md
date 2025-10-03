# codex-resume

`codex-resume` is a Textual-powered terminal UI that scans your `~/.codex/sessions` archive, shows each session’s metadata and chat preview, and lets you relaunch anything with `codex resume <id>` in the original working directory.

> ⚠️ **WARNING WARNING VIBECODED GARBAGE ALERT** ⚠️
>
> was vibecoded. Install only if you do not fear slop.
>
> That said, this actually does work. It does the thing.

## Quick Start

```bash
uvx codex-resume
```

`uvx` will download the latest release, create an ephemeral environment, and launch the UI in one command. Use the arrow keys to pick a session, `E` to edit extra flags, and `Enter` to resume.

## Features

- Auto-discovers Codex CLI session logs and sorts them by last activity.
- Displays summaries and multi-line chat previews so “no summary” sessions stay readable.
- Shows rich metadata (CWD, CLI version, log path, event count) in a detail pane.
- Resumes sessions in their recorded working directories, with optional extra CLI arguments.
- Provides config helpers for persistent flags via `~/.config/codex-resume/config.json`.

## Installation

### uv tool (recommended)

```bash
uv tool install codex-resume
```

Launch moving forward with:

```bash
codex-resume
```

### pip

```bash
pip install codex-resume
```

## Usage

```
codex-resume [--extra "--search ."] [--set-default-extra "--search ."]
```

- Arrow keys: navigate sessions
- `Enter` / `R`: resume selected session
- `E`: edit extra arguments
- `F5` / `Ctrl+R`: refresh session list
- `Q`: quit without resuming

The detail pane shows the chat preview, metadata, and the exact command that will run.

## Configuration

Default extras live at `~/.config/codex-resume/config.json`. Update it through the CLI:

```bash
codex-resume --set-default-extra "--yolo --search ."
```

To view the config path:

```bash
codex-resume --show-config-path
```

## Development

1. Install dependencies and create a virtual environment (including dev tooling):
   ```bash
   uv sync --extra dev
   ```
2. Run the app from source:
   ```bash
   uv run codex-resume
   ```
3. Format and lint (optional):
   ```bash
   uv run ruff format
   uv run ruff check
   ```
4. Build a release:
   ```bash
   uv build
   ```

## Releasing

1. Update `src/codex_resume/__init__.py` and `pyproject.toml` with the new version.
2. Regenerate the lockfile: `uv lock --update-package codex-resume`.
3. Run the test commands above.
4. Publish:
   ```bash
   uv publish
   ```
5. Create a GitHub release for [darvell/codex-resume](https://github.com/darvell/codex-resume).
