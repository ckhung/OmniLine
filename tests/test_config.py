from pathlib import Path

import pytest

from omniline.config import init_user_directory, load_config


def test_load_config_resolves_theme_files_relative_to_config(tmp_path: Path) -> None:
    theme = tmp_path / "theme.toml"
    theme.write_text('[theme]\nlist_panel = "bg:#123456"\n', encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(
        '[paths]\ntokens_file = "session.json"\n'
        '[shared]\npinned_chats = ["C1"]\nhistory_limit = 12\n'
        '[tui]\ntheme_file = "theme.toml"\n'
        '[web]\ntheme_file = "theme.toml"\n',
        encoding="utf-8",
    )

    loaded = load_config(str(config))

    assert loaded.tokens_file == tmp_path / "session.json"
    assert loaded.web_access_log == tmp_path / "access.log"
    assert loaded.web_max_failed_attempts == 5
    assert loaded.web_failed_attempt_window_seconds == 300
    assert loaded.web_session_lifetime_hours == 24
    assert loaded.pinned_chats == ["C1"]
    assert loaded.history_limit == 12
    assert loaded.tui_theme["list-panel"] == "bg:#123456"
    assert loaded.web_theme["list-panel"] == "bg:#123456"

    override = tmp_path / "override.toml"
    override.write_text('[theme]\nlist_panel = "bg:#abcdef"\n', encoding="utf-8")
    overridden = load_config(str(config), str(override))
    assert overridden.tui_theme["list-panel"] == "bg:#abcdef"


def test_save_pinned_chats_updates_only_the_shared_pin_line(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '# retain this comment\n[shared]\n'
        'pinned_chats = ["C1"]\n'
        'timezone = "Asia/Taipei"\n',
        encoding="utf-8",
    )

    loaded = load_config(str(config))
    loaded.save_pinned_chats(["U2", "C3"])

    assert "# retain this comment" in config.read_text(encoding="utf-8")
    assert 'pinned_chats = ["U2", "C3"]' in config.read_text(encoding="utf-8")
    assert loaded.pinned_chats == ["U2", "C3"]


def test_init_user_directory_does_not_overwrite_without_force(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "themes").mkdir(parents=True)
    (source / "config.toml").write_text("source = true\n", encoding="utf-8")
    for name in ("dark.toml", "light.toml"):
        (source / "themes" / name).write_text(name, encoding="utf-8")
    destination = tmp_path / "user"

    written, skipped = init_user_directory(destination, source_directory=source)

    assert len(written) == 3
    assert skipped == []


def test_invalid_config_types_raise_user_facing_errors(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[paths]\ntokens_file = 7\n'
        '[shared]\npinned_chats = []\nhistory_limit = 50\n'
        '[tui]\nsend_confirmation = true\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="tokens_file must be a string"):
        load_config(str(config))
