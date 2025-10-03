# codex-resume

![codex-resume main screen](docs/main_screen.png)

`codex-resume` is a Textual-powered terminal UI that scans your `~/.codex/sessions` archive, shows each session’s metadata and chat preview, and lets you relaunch anything with `codex resume <id>` in the original working directory.

![codex-resume info modal](docs/extra_info.png)

> ⚠️ **WARNING WARNING VIBECODED GARBAGE ALERT** ⚠️
>
> was vibecoded. Install only if you do not fear slop.
>
> That said, this actually does work. It does the thing.

## Why codex-resume?

- **Zero thinking required** – point it at `~/.codex/sessions` and it auto-sorts everything by last activity.
- **Live timeline** – “Last activity” timestamps update every few seconds, so you know which session is still cooking.
- **Privacy toggle** – smash `X` to hide messy rows mid-demo; unhide when you’re ready.
- **Full-path context** – see the exact working directory (with `~` shorthand) before resuming.
- **Lightning restart** – press `Enter` or `R` and you’re back inside the Codex CLI with the same extra args.

## Quick Start

```bash
uvx codex-resume
```

`uvx` will download the latest release, create an ephemeral environment, and launch the UI in one command. Use the arrow keys to pick a session, `E` to edit extra flags, and `Enter` to resume.

## Features at a Glance

- Auto-discovers Codex CLI session logs and sorts them by last activity.
- Live “Last activity” column and preview pane refresh to stay accurate.
- Multi-line chat previews so “no summary” sessions stay readable.
- Hide/unhide rows on demand; info modal mirrors the hidden state.
- Resumes sessions in their recorded working directories with optional extra args.
- Stores default CLI flags in `~/.config/codex-resume/config.json`.

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

### Command line

```bash
codex-resume [--extra "--search ."] [--set-default-extra "--search ."]
```

### Key bindings

| Key            | Action                                    |
|----------------|-------------------------------------------|
| Arrow keys     | Move selection                            |
| `Enter` / `R`  | Resume highlighted session                |
| `E`            | Edit extra CLI arguments                  |
| `I`            | Toggle info popup                         |
| `X`            | Hide/unhide session row                   |
| `F5` / `Ctrl+R`| Refresh session list                      |
| `Q`            | Quit without resuming                     |

The preview pane shows the latest chat snippets, and the info popup expands with full metadata (last activity, cwd, CLI version, log path, event count, preview history).

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
