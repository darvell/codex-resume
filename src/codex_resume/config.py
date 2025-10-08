from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

_CONFIG_DIR_ENV = "XDG_CONFIG_HOME"
_DEFAULT_CONFIG_DIR = Path.home() / ".config"
_CONFIG_DIR_NAME = "codex-resume"
_CONFIG_FILE_NAME = "config.json"


def _resolve_config_path() -> Path:
    base_dir = Path(os.environ.get(_CONFIG_DIR_ENV, _DEFAULT_CONFIG_DIR))
    return base_dir / _CONFIG_DIR_NAME / _CONFIG_FILE_NAME


CONFIG_PATH = _resolve_config_path()


@dataclass
class RemoteServerConfig:
    """Connection metadata for a remote Codex CLI host."""

    target: str
    sessions_dir: str = "~/.codex/sessions"

    @classmethod
    def from_raw(cls, raw: Any) -> "RemoteServerConfig":
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, str):
            return cls(target=raw)
        if isinstance(raw, dict):
            target = str(raw.get("target", "")).strip()
            if not target:
                raise ValueError("remote server config requires a non-empty 'target'")
            sessions_dir = str(raw.get("sessions_dir", "~/.codex/sessions")).strip() or "~/.codex/sessions"
            return cls(target=target, sessions_dir=sessions_dir)
        raise ValueError(f"Unsupported remote server config: {raw!r}")

    def to_dict(self) -> Dict[str, Any]:
        return {"target": self.target, "sessions_dir": self.sessions_dir}


@dataclass
class Config:
    """Represents persisted settings for codex-resume."""

    default_extra_args: List[str] = field(default_factory=list)
    remote_servers: List[RemoteServerConfig] = field(default_factory=list)
    use_npx_codex: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        raw_args = data.get("default_extra_args", [])
        if not isinstance(raw_args, list):
            raw_args = []
        stringified = [str(arg) for arg in raw_args]
        raw_remotes = data.get("remote_servers", [])
        remote_configs: List[RemoteServerConfig] = []
        if isinstance(raw_remotes, list):
            for entry in raw_remotes:
                try:
                    remote_configs.append(RemoteServerConfig.from_raw(entry))
                except ValueError:
                    continue
        use_npx = bool(data.get("use_npx_codex", False))
        return cls(default_extra_args=stringified, remote_servers=remote_configs, use_npx_codex=use_npx)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "default_extra_args": list(self.default_extra_args),
            "remote_servers": [remote.to_dict() for remote in self.remote_servers],
            "use_npx_codex": self.use_npx_codex,
        }


def load_config() -> Config:
    """Load config from disk, returning defaults on failure."""

    path = CONFIG_PATH
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return Config()
    except json.JSONDecodeError:
        return Config()
    return Config.from_dict(data)


def save_config(config: Config) -> None:
    """Persist config to disk, creating directories as needed."""

    path = CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(config.to_dict(), fh, indent=2)
        fh.write("\n")
