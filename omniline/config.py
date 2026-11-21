"""TOML configuration loading and persistence."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9 and 3.10
    tomllib = import_module("tomli")


DEFAULT_THEME = {
    "list-panel": "bg:#2e3440",
    "content-panel": "bg:#242933",
    "divider": "#4c566a",
    "selected": "reverse",
    "favorite": "bold",
    "underline": "underline",
    "favorite-underline": "bold underline",
    "title": "bold underline",
    "sender": "underline",
    "date": "bold",
    "boundary": "#4c566a",
    "anchor": "#4c566a",
    "anchor-cursor": "reverse",
    "cursor-empty": "underline",
    "own": "#88c0d0",
    "own-box": "bg:#2e3440 #88c0d0",
    "other": "#d8dee9",
    "match": "reverse #ebcb8b",
    "other-match": "bold",
    "status": "reverse",
    "search": "#eceff4 bg:#3b4252",
    "compose": "#eceff4 bg:#3b4252",
    "dim": "#81a1c1",
}


@dataclass
class AppConfig:
    path: Path | None
    tokens_file: Path
    pinned_chats: list[str]
    timezone: str
    cli_sort: str
    history_limit: int
    tui_send_confirmation: bool
    tui_send_only_on_fixed_lines: bool
    tui_theme: dict[str, str]
    web_theme: dict[str, str]

    def save_pinned_chats(self, chats: list[str]) -> None:
        """Persist shared pins without rewriting unrelated user comments."""
        if self.path is None:
            raise ValueError("cannot persist pins without a config file")
        text = self.path.read_text(encoding="utf-8")
        encoded = json.dumps(chats, ensure_ascii=False)
        section = re.search(r"(?ms)^\[shared\]\n(.*?)(?=^\[|\Z)", text)
        if section is None:
            raise ValueError("config.toml has no [shared] section")
        body = section.group(1)
        if re.search(r"(?m)^pinned_chats\s*=", body):
            body = re.sub(r"(?m)^pinned_chats\s*=.*$", f"pinned_chats = {encoded}", body)
        else:
            body = f"pinned_chats = {encoded}\n" + body
        updated = text[: section.start(1)] + body + text[section.end(1) :]
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(updated, encoding="utf-8")
        temporary.replace(self.path)
        self.pinned_chats = list(chats)


def init_user_directory(
    directory: Path | None = None,
    *,
    force: bool = False,
    source_directory: Path | None = None,
) -> tuple[list[Path], list[Path]]:
    """Install the distribution config and themes into a user directory."""
    destination = directory or Path.home() / ".omniline"
    source_root = Path(__file__).resolve().parent.parent
    packaged_root = Path(__file__).resolve().parent
    source = source_directory or (
        source_root if (source_root / "config.toml").exists() else packaged_root
    )
    files = [
        source / "config.toml",
        source / "themes/dark.toml",
        source / "themes/light.toml",
    ]
    targets = [
        destination / "config.toml",
        destination / "themes/dark.toml",
        destination / "themes/light.toml",
    ]
    written: list[Path] = []
    skipped: list[Path] = []
    for source_file, target in zip(files, targets):
        if not source_file.exists():
            raise RuntimeError("a distribution config or theme file is missing")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not force:
            skipped.append(target)
            continue
        shutil.copyfile(source_file, target)
        written.append(target)
    return written, skipped


def _read(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _theme(path: Path | None) -> dict[str, str]:
    theme = dict(DEFAULT_THEME)
    if path is None or not path.exists():
        return theme
    values = _read(path).get("theme", {})
    if not isinstance(values, dict):
        raise ValueError(f"{path.name}: [theme] must be a table")
    theme.update({key.replace("_", "-"): str(value) for key, value in values.items()})
    return theme


def _table(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{name}] must be a TOML table")
    return value


def _bool_setting(section: dict[str, Any], name: str, default: bool) -> bool:
    value = section.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false")
    return value


def load_config(explicit: str | None = None, tui_theme_file: str | None = None) -> AppConfig:
    """Load the first available config and its frontend-specific themes."""
    explicit_path = Path(explicit).expanduser() if explicit else None
    if explicit_path and explicit_path.is_dir():
        explicit_path /= "config.toml"
    distribution = Path(__file__).resolve().parent.parent / "config.toml"
    packaged = Path(__file__).resolve().parent / "config.toml"
    candidates = (
        [explicit_path]
        if explicit_path
        else [Path.home() / ".omniline/config.toml", distribution, packaged]
    )
    path = next((candidate for candidate in candidates if candidate and candidate.exists()), None)
    data = _read(path) if path else {}
    base = path.parent if path else Path.cwd()
    paths = _table(data, "paths")
    shared = _table(data, "shared")
    cli = _table(data, "cli")
    tui = _table(data, "tui")
    web = _table(data, "web")
    token_value = paths.get("tokens_file", "tokens.json")
    if not isinstance(token_value, str):
        raise ValueError("[paths].tokens_file must be a string")
    tokens = Path(token_value).expanduser()
    if not tokens.is_absolute():
        tokens = base / tokens
    def theme_path(section: dict[str, Any]) -> Path | None:
        value = section.get("theme_file")
        if not value:
            return None
        if not isinstance(value, str):
            raise ValueError("theme_file must be a string")
        theme = Path(value).expanduser()
        return theme if theme.is_absolute() else base / theme
    tui_theme = theme_path(tui)
    if tui_theme_file:
        tui_theme = Path(tui_theme_file).expanduser()
        if not tui_theme.is_absolute():
            tui_theme = base / tui_theme
    pinned_value = shared.get("pinned_chats", [])
    if not isinstance(pinned_value, list) or not all(
        isinstance(mid, str) for mid in pinned_value
    ):
        raise ValueError("[shared].pinned_chats must be a list of strings")
    history_value = shared.get("history_limit", 50)
    if isinstance(history_value, bool) or not isinstance(history_value, int) or history_value < 1:
        raise ValueError("[shared].history_limit must be a positive integer")
    return AppConfig(
        path=path,
        tokens_file=tokens,
        pinned_chats=list(pinned_value),
        timezone=str(shared.get("timezone", "Asia/Taipei")),
        cli_sort=str(cli.get("sort", "name")),
        history_limit=history_value,
        tui_send_confirmation=_bool_setting(tui, "send_confirmation", True),
        tui_send_only_on_fixed_lines=_bool_setting(tui, "send_only_on_fixed_lines", True),
        tui_theme=_theme(tui_theme),
        web_theme=_theme(theme_path(web)),
    )
