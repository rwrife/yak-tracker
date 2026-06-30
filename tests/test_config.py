"""Tests for configuration loading and merging (M5)."""

from __future__ import annotations

from pathlib import Path

from yak_tracker.config import (
    DEFAULTS,
    STARTER_CONFIG,
    VALID_FORMATS,
    Config,
    ConfigExistsError,
    default_config_path,
    load_config,
    write_starter_config,
)


def test_missing_file_falls_back_to_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.idle_gap == DEFAULTS["idle_gap"]
    assert cfg.model == DEFAULTS["model"]
    assert cfg.ollama_host == DEFAULTS["ollama_host"]
    assert cfg.format == DEFAULTS["format"]
    assert cfg.repos == ()
    assert "no config file" in cfg.source
    assert cfg.warnings == ()


def test_loads_values_from_file(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        "\n".join(
            [
                'repos = ["~/code/a", "~/code/b"]',
                "idle_gap = 40",
                'model = "mistral"',
                'ollama_host = "http://10.0.0.2:11434"',
                "timeout = 90",
                'format = "standup"',
            ]
        )
    )
    cfg = load_config(cfg_file)
    assert cfg.idle_gap == 40.0
    assert cfg.model == "mistral"
    assert cfg.ollama_host == "http://10.0.0.2:11434"
    assert cfg.timeout == 90.0
    assert cfg.format == "standup"
    # repos expand ~ and become absolute.
    assert len(cfg.repos) == 2
    assert all(p.is_absolute() for p in cfg.repos)
    assert cfg.source == str(cfg_file)
    assert cfg.warnings == ()


def test_a_single_repo_string_is_accepted(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('repos = "~/just-one"\n')
    cfg = load_config(cfg_file)
    assert len(cfg.repos) == 1
    assert cfg.repos[0].is_absolute()


def test_bad_types_warn_and_keep_defaults(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        "\n".join(
            [
                'idle_gap = "lots"',  # not a number
                "timeout = -5",  # not positive
                "model = 42",  # not a string
            ]
        )
    )
    cfg = load_config(cfg_file)
    # All bad values dropped → defaults retained.
    assert cfg.idle_gap == DEFAULTS["idle_gap"]
    assert cfg.timeout == DEFAULTS["timeout"]
    assert cfg.model == DEFAULTS["model"]
    # Three distinct warnings recorded.
    assert len(cfg.warnings) == 3
    joined = " ".join(cfg.warnings)
    assert "idle_gap" in joined
    assert "timeout" in joined
    assert "model" in joined


def test_invalid_format_warns(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('format = "haiku"\n')
    cfg = load_config(cfg_file)
    assert cfg.format == DEFAULTS["format"]
    assert any("format" in w for w in cfg.warnings)
    # Sanity: the valid set is what the prose claims.
    assert set(VALID_FORMATS) == {"standup", "story", "learning"}


def test_unknown_keys_warn(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('frobnicate = true\nidle_gap = 10\n')
    cfg = load_config(cfg_file)
    assert cfg.idle_gap == 10.0  # known key still applied
    assert any("frobnicate" in w for w in cfg.warnings)


def test_malformed_toml_does_not_raise(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("this is = = not valid toml [[[\n")
    cfg = load_config(cfg_file)
    # Falls back to defaults, records the parse failure.
    assert cfg.idle_gap == DEFAULTS["idle_gap"]
    assert "could not read" in cfg.source or "parse" in cfg.source.lower()
    assert cfg.warnings


def test_boolean_for_number_is_rejected(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("idle_gap = true\n")
    cfg = load_config(cfg_file)
    assert cfg.idle_gap == DEFAULTS["idle_gap"]
    assert any("idle_gap" in w and "boolean" in w for w in cfg.warnings)


def test_with_overrides_precedence() -> None:
    base = Config()
    over = base.with_overrides(idle_gap=99, model="custom", format="learning")
    assert over.idle_gap == 99.0
    assert over.model == "custom"
    assert over.format == "learning"
    # Untouched fields keep their values; base is unmutated.
    assert over.ollama_host == base.ollama_host
    assert base.idle_gap == 25.0


def test_with_overrides_noop_returns_same() -> None:
    base = Config()
    assert base.with_overrides() is base


def test_env_var_sets_default_path(monkeypatch) -> None:
    monkeypatch.setenv("YAK_TRACKER_CONFIG", "/tmp/custom-yak.toml")
    assert default_config_path() == Path("/tmp/custom-yak.toml")


def test_xdg_config_home_used(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("YAK_TRACKER_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    expected = tmp_path / "yak-tracker" / "config.toml"
    assert default_config_path() == expected


# --- redact setting (issue #8) -------------------------------------------


def test_redact_defaults_to_true(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.redact is True
    assert DEFAULTS["redact"] is True


def test_redact_can_be_disabled_in_file(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("redact = false\n")
    cfg = load_config(cfg_file)
    assert cfg.redact is False
    assert cfg.warnings == ()


def test_redact_non_bool_is_ignored_with_warning(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('redact = "yes"\n')
    cfg = load_config(cfg_file)
    # Falls back to the default and records a warning rather than crashing.
    assert cfg.redact is True
    assert any("redact" in w for w in cfg.warnings)


def test_with_overrides_can_force_redact_off() -> None:
    base = Config()  # redact defaults True
    assert base.with_overrides(redact=False).redact is False
    # None means "no override" so the file/default value is kept.
    assert base.with_overrides(redact=None).redact is True


# --- starter config / `yak config --init` (M6) ---------------------------


def test_write_starter_config_creates_file_and_parents(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "config.toml"
    written = write_starter_config(target)
    assert written == target
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == STARTER_CONFIG


def test_starter_config_round_trips_to_defaults(tmp_path: Path) -> None:
    # The generated file must parse cleanly (no warnings) and yield exactly the
    # built-in defaults — this guards STARTER_CONFIG against key/type drift.
    target = tmp_path / "config.toml"
    write_starter_config(target)
    cfg = load_config(target)
    assert cfg.warnings == ()
    assert cfg.idle_gap == DEFAULTS["idle_gap"]
    assert cfg.model == DEFAULTS["model"]
    assert cfg.ollama_host == DEFAULTS["ollama_host"]
    assert cfg.timeout == DEFAULTS["timeout"]
    assert cfg.format == DEFAULTS["format"]
    assert cfg.redact == DEFAULTS["redact"]
    # vault_path is commented out in the starter (no sensible default path), so
    # it stays unset; filename_template ships at its default.
    assert cfg.vault_path is None
    assert cfg.vault_path == DEFAULTS["vault_path"]
    assert cfg.filename_template == DEFAULTS["filename_template"]
    # repos is commented out in the starter, so it stays at the default (empty).
    assert cfg.repos == ()


def test_write_starter_config_refuses_existing(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text("idle_gap = 5\n")
    try:
        write_starter_config(target)
    except ConfigExistsError as exc:
        assert exc.path == target
    else:  # pragma: no cover - guard against a silent clobber regression
        raise AssertionError("expected ConfigExistsError")
    # The user's edited file is left untouched.
    assert target.read_text() == "idle_gap = 5\n"


def test_write_starter_config_force_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text("idle_gap = 5\n")
    written = write_starter_config(target, force=True)
    assert written == target
    assert target.read_text(encoding="utf-8") == STARTER_CONFIG


def test_write_starter_config_uses_default_path(monkeypatch, tmp_path: Path) -> None:
    # With no explicit path, it honours $YAK_TRACKER_CONFIG via default_config_path.
    target = tmp_path / "yak" / "config.toml"
    monkeypatch.setenv("YAK_TRACKER_CONFIG", str(target))
    written = write_starter_config()
    assert written == target
    assert target.is_file()
