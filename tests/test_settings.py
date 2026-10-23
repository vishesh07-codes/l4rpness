import os
import stat

import pytest

from app import config, settings

KEY_VARS = ["DEEPSEEK_API_KEY", "ROUTER9_API_KEY"]


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    real_data_dir = config.DATA_DIR
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    # forget whatever the real process env held so each test starts clean
    monkeypatch.setattr(config, "_ENV_BASELINE", {})
    for name in KEY_VARS:
        monkeypatch.delenv(name, raising=False)
    config.reload_keys()
    yield tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", real_data_dir)
    config.refresh_runtime()
    config.refresh_env()


def test_status_reports_missing_when_unset(isolated):
    rows = {row["provider"]: row for row in settings.status()}
    assert set(rows) == set(config.PROVIDERS)
    for row in rows.values():
        assert row["configured"] is False
        assert row["source"] == "missing"
        assert row["length"] == 0
        assert row["suffix"] is None
    assert rows["deepseek"]["env_var"] == "DEEPSEEK_API_KEY"


def test_save_round_trip(isolated):
    saved = settings.save({"DEEPSEEK_API_KEY": "sk-test-12345678"})
    row = {r["provider"]: r for r in saved}["deepseek"]
    assert row["configured"] is True
    assert row["source"] == "settings"
    assert row["suffix"] == "5678"
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-test-12345678"

    path = isolated / config.ENV_OVERLAY_NAME
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "DEEPSEEK_API_KEY=sk-test-12345678" in path.read_text()

    cleared = settings.save({"DEEPSEEK_API_KEY": ""})
    row = {r["provider"]: r for r in cleared}["deepseek"]
    assert row["configured"] is False
    assert row["source"] == "missing"
    assert "DEEPSEEK_API_KEY" not in os.environ
    assert "DEEPSEEK_API_KEY" not in path.read_text()


def test_save_rejects_unknown_var(isolated):
    with pytest.raises(ValueError):
        settings.save({"EVIL_VAR": "x"})
    with pytest.raises(ValueError):
        settings.save({"PATH": "/tmp"})


def test_overlay_wins_then_clear_restores_baseline(isolated, monkeypatch):
    monkeypatch.setattr(config, "_ENV_BASELINE", {"DEEPSEEK_API_KEY": "host-key"})
    monkeypatch.setenv("DEEPSEEK_API_KEY", "host-key")

    settings.save({"DEEPSEEK_API_KEY": "overlay-key"})
    assert os.environ["DEEPSEEK_API_KEY"] == "overlay-key"
    assert config.read_env_overlay()["DEEPSEEK_API_KEY"] == "overlay-key"
    row = {r["provider"]: r for r in settings.status()}["deepseek"]
    assert row["source"] == "settings"

    settings.save({"DEEPSEEK_API_KEY": ""})
    assert os.environ["DEEPSEEK_API_KEY"] == "host-key"
    row = {r["provider"]: r for r in settings.status()}["deepseek"]
    assert row["source"] == "environment"


def test_malformed_overlay_lines_ignored(isolated):
    path = isolated / config.ENV_OVERLAY_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# comment\n"
        "\n"
        "no-equals-here\n"
        "NOT_ALLOWED=zzz\n"
        "DEEPSEEK_API_KEY = spaced-key \n"
    )
    assert config.read_env_overlay() == {"DEEPSEEK_API_KEY": "spaced-key"}


def test_runtime_status_shape(isolated):
    data = settings.runtime_status()
    assert [s["id"] for s in data["sections"]] == [
        "model",
        "requests",
        "supervisor",
        "generators",
        "tools",
        "uploads",
    ]
    keys = [f["key"] for f in data["fields"]]
    assert "llm.request_timeout" in keys
    assert "tools.max_steps" in keys
    assert "uploads.max_bytes" in keys
    assert data["values"]["llm.request_timeout"] == config.REQUEST_TIMEOUT
    assert all(source == "file" for source in data["sources"].values())
    provider = next(f for f in data["fields"] if f["key"] == "llm.provider")
    assert set(provider["options"]) == set(config.PROVIDERS)


def test_save_runtime_round_trip(isolated):
    out = settings.save_runtime({"llm.request_timeout": "99", "llm.efforts": "low, max"})
    assert config.REQUEST_TIMEOUT == 99
    assert config.EFFORTS == ["low", "max"]
    assert out["sources"]["llm.request_timeout"] == "settings"
    assert out["values"]["supervisor.error_backoff_seconds"] == config.SUPERVISOR_BACKOFF
    path = isolated / config.SETTINGS_OVERLAY_NAME
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "request_timeout = 99" in path.read_text()

    settings.reset_runtime()
    assert not path.exists()
    assert config.REQUEST_TIMEOUT == config.load()["llm"]["request_timeout"]
    assert settings.runtime_status()["sources"]["llm.request_timeout"] == "file"


def test_save_runtime_rejects_bad_values(isolated):
    with pytest.raises(ValueError):
        settings.save_runtime({"nope.unknown": "1"})
    with pytest.raises(ValueError):
        settings.save_runtime({"llm.request_timeout": "abc"})
    with pytest.raises(ValueError):
        settings.save_runtime({"llm.request_timeout": "99999"})
    with pytest.raises(ValueError):
        settings.save_runtime({"llm.provider": "bogus"})
    with pytest.raises(ValueError):
        settings.save_runtime({"llm.model": "   "})
    with pytest.raises(ValueError):
        settings.save_runtime({"llm.efforts": "  , "})
    # nothing was persisted by the failed saves
    assert config.read_settings_overlay() == {}


def test_effort_validated_against_new_efforts(isolated):
    out = settings.save_runtime({"llm.efforts": "low, max", "llm.effort": "max"})
    assert out["values"]["llm.effort"] == "max"
    assert config.DEFAULT_EFFORT == "max"
    with pytest.raises(ValueError):
        settings.save_runtime({"llm.effort": "bogus"})


def test_malformed_settings_overlay_ignored(isolated):
    path = isolated / config.SETTINGS_OVERLAY_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this is not toml = = =\n")
    data = settings.runtime_status()
    assert data["sources"]["llm.request_timeout"] == "file"
    assert data["values"]["llm.request_timeout"] == config.REQUEST_TIMEOUT


def test_host_unmounted(isolated, tmp_path, monkeypatch):
    empty = tmp_path / "noroot"
    empty.mkdir()
    monkeypatch.setattr(config, "ROOT", empty)
    monkeypatch.setattr(settings, "_HOST_SIG", None)
    state = settings.host_state()
    assert state == {
        "mounted": False,
        "readable": False,
        "writable": False,
        "changed": False,
        "vars": {},
        "last_applied": {"vars": [], "at": None},
    }
    assert settings.reload_host_env() == []
    with pytest.raises(ValueError):
        settings.push_host_env({"DEEPSEEK_API_KEY": "x"})


def test_host_reload_applies_and_prunes_overlay(isolated, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(settings, "_HOST_SIG", None)
    (tmp_path / ".env").write_text("# comment\nDEEPSEEK_API_KEY=host-key-12345678\nOTHER=keep\n")
    # a stale overlay override conflicts with the host file
    settings.save({"DEEPSEEK_API_KEY": "overlay-stale-key"})
    assert os.environ["DEEPSEEK_API_KEY"] == "overlay-stale-key"

    state = settings.host_state()
    assert state["mounted"] is True
    assert state["changed"] is False
    assert state["vars"]["DEEPSEEK_API_KEY"]["suffix"] == "5678"

    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=host-key-12345678\n")
    assert settings.host_state()["changed"] is True

    changed = settings.reload_host_env()
    assert changed == ["DEEPSEEK_API_KEY"]
    assert os.environ["DEEPSEEK_API_KEY"] == "host-key-12345678"
    assert "DEEPSEEK_API_KEY" not in config.read_env_overlay()
    row = {r["provider"]: r for r in settings.status()}["deepseek"]
    assert row["source"] == "environment"
    # second reload: nothing changed
    assert settings.reload_host_env() == []
    assert settings.host_state()["changed"] is False


def test_host_push_preserves_file(isolated, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(settings, "_HOST_SIG", None)
    (tmp_path / ".env").write_text("# keep me\nOTHER=1\nDEEPSEEK_API_KEY=old-key\n")
    settings.save({"DEEPSEEK_API_KEY": "pushed-key-9999"})

    written = settings.push_host_env({"DEEPSEEK_API_KEY": "pushed-key-9999"})
    assert written == ["DEEPSEEK_API_KEY"]
    body = (tmp_path / ".env").read_text()
    assert "# keep me" in body
    assert "OTHER=1" in body
    assert "pushed-key-9999" in body
    assert "old-key" not in body
    # single source of truth again: the overlay copy is pruned
    assert "DEEPSEEK_API_KEY" not in config.read_env_overlay()
    assert os.environ["DEEPSEEK_API_KEY"] == "pushed-key-9999"
    row = {r["provider"]: r for r in settings.status()}["deepseek"]
    assert row["source"] == "environment"


def test_auto_apply_record(isolated, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(settings, "_HOST_SIG", None)
    monkeypatch.setattr(settings, "_LAST_AUTO_APPLIED", {"vars": [], "at": None})
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=auto-key-12345678\n")
    assert settings.host_state()["changed"] is False
    assert settings.host_state()["last_applied"] == {"vars": [], "at": None}

    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=auto-key-99999999\n")
    assert settings.host_state()["changed"] is True
    changed = settings.reload_host_env()
    settings.note_auto_applied(changed)
    state = settings.host_state()
    assert state["changed"] is False
    assert state["last_applied"]["vars"] == ["DEEPSEEK_API_KEY"]
    assert state["last_applied"]["at"] is not None
