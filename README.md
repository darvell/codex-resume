# codex-resume

![codex-resume main screen](docs/main_screen.png)

`codex-resume` is a Textual-powered terminal UI built for resuming OpenAI Codex CLI sessions. It scans your `~/.codex/sessions` archive (and any remote hosts you configure), surfaces metadata and chat previews, and relaunches sessions with your preferred `codex resume` command—all without leaving the terminal.

![codex-resume info modal](docs/extra_info.png)

> ⚠️ **WARNING WARNING VIBECODED GARBAGE ALERT** ⚠️
>
> was vibecoded. Install only if you do not fear slop.
>
> That said, this actually does work. It does the thing.

## Quick Start

```bash
uvx codex-resume
```

`uvx` will download the latest release, create an ephemeral environment, and launch the UI in one command. Prefer `pipx` instead? Same idea:

```bash
pipx run codex-resume
```

Either way, arrow keys pick a session, `E` edits extra flags, `O` opens configuration (remote hosts + npx toggle), and `Enter` resumes.

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

## Highlights

- **Remote support out of the box** – point codex-resume at any number of SSH hosts and it’ll grab their Codex logs in parallel. Resuming a session automatically drops into that server (optionally via `npx --yes codex@latest`) and runs `codex resume` with the same flags you use locally.
- **Fast startup** – the CLI preloads the last week of history (local + remote) before the UI appears, with a live spinner that shows which hosts are being scanned.
- **Rich previews and metadata** – peek at session summaries, timestamps, and the first snippets of the conversation. Hidden sessions stay hidden even after refresh.
- **Customise once, reuse forever** – edit extra arguments (`E`) and save them as the global default, or toggle “always use `npx @openai/codex@latest`” in the options panel (`O`).
- **Readable paths** – directories are shown relative to `~` when possible, so you can tell at a glance where a session ran.

## Usage

```bash
codex-resume [--sessions-root PATH] [--extra "--search --yolo"] [--set-default-extra "--search --yolo"]
```

In the UI:

- Arrow keys / mouse to navigate sessions
- `Enter` / `R` – resume selected session
- `E` – edit launch arguments (`Save` or `Save & Apply As Default`)
- `O` – configure remote servers and the `npx @openai/codex@latest` toggle
- `I` – show full session details
- `X` – hide/unhide a row
- `F5` / `Ctrl+R` – refresh logs
- `Q` – quit
