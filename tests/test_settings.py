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


CONFIG_TOML = (
    "[server]\n"
    'host = "0.0.0.0"\n'
    "port = 4517\n"
    "\n"
    "[llm]\n"
    'provider = "deepseek"\n'
    'model = "deepseek-flash"\n'
    "# the default reasoning budget\n"
    'effort = "high"\n'
    'efforts = ["low", "high", "max"]\n'
    "request_timeout = 180\n"
    "temperature = 0.3 # ignored while thinking mode is on\n"
    "\n"
    "[llm.providers.deepseek]\n"
    'base_url = "https://api.deepseek.com"\n'
    'api_key_env = "DEEPSEEK_API_KEY"\n'
    "thinking = true\n"
    "\n"
    '[llm.providers."9router"]\n'
    'base_url = "http://host.docker.internal:20128/v1"\n'
    'api_key_env = "ROUTER9_API_KEY"\n'
    "thinking = false\n"
    "\n"
    "[tools]\n"
    "max_steps = -1\n"
    "bash_timeout = 120\n"
)


@pytest.fixture()
def host_config(tmp_path, monkeypatch, isolated):
    """A temp root with a config.toml, plus a clean sig baseline. Live
    settings are recomputed from the real files before ROOT goes back, so the
    refreshed SETTINGS never leak into later tests."""
    real_root = config.ROOT
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(settings, "_HOST_CONFIG_SIG", None)
    (tmp_path / "config.toml").write_text(CONFIG_TOML)
    # the live settings must start from this file, not the real repo config
    config.refresh_runtime()
    yield tmp_path
    monkeypatch.setattr(config, "ROOT", real_root)
    config.refresh_runtime()


def test_host_config_unmounted(isolated, tmp_path, monkeypatch):
    real_root = config.ROOT
    empty = tmp_path / "noroot"
    empty.mkdir()
    monkeypatch.setattr(config, "ROOT", empty)
    monkeypatch.setattr(settings, "_HOST_CONFIG_SIG", None)
    monkeypatch.setattr(settings, "_LAST_CONFIG_APPLIED", {"keys": [], "at": None})
    assert settings.host_config_state() == {
        "mounted": False,
        "readable": False,
        "writable": False,
        "changed": False,
        "keys": {},
        "last_applied": {"keys": [], "at": None},
    }
    assert settings.reload_host_config() == []
    with pytest.raises(ValueError):
        settings.push_host_config({"llm.request_timeout": "99"})
    # refresh the live settings against the real root in fixture teardown
    monkeypatch.setattr(config, "ROOT", real_root)


def test_host_config_push_preserves_file(host_config):
    # an overlay override that the push supersedes
    settings.save_runtime({"llm.request_timeout": "99"})
    assert config.REQUEST_TIMEOUT == 99

    written = settings.push_host_config({"llm.request_timeout": "42", "llm.temperature": "0.7"})
    assert written == ["llm.request_timeout", "llm.temperature"]
    body = (host_config / "config.toml").read_text()
    assert "# the default reasoning budget" in body
    assert "port = 4517" in body
    assert "request_timeout = 42" in body
    assert "request_timeout = 180" not in body
    assert "temperature = 0.7 # ignored while thinking mode is on" in body
    # the file is the source of truth again: overlay copies are gone
    assert config.read_settings_overlay() == {}
    assert config.REQUEST_TIMEOUT == 42
    assert config.TEMPERATURE == 0.7
    assert settings.runtime_status()["sources"]["llm.request_timeout"] == "file"
    assert settings.host_config_state()["changed"] is False


def test_host_config_push_adds_missing_keys(host_config):
    written = settings.push_host_config({"tools.http_timeout": "75", "uploads.max_bytes": "2048"})
    assert written == ["tools.http_timeout", "uploads.max_bytes"]
    body = (host_config / "config.toml").read_text()
    # the new tools key lands inside [tools], the new section at the end
    tools_block = body.split("[tools]", 1)[1]
    assert "http_timeout = 75" in tools_block
    assert "bash_timeout = 120" in tools_block
    assert "\n[uploads]\nmax_bytes = 2048\n" in body
    assert config.HTTP_TIMEOUT == 75
    assert config.MAX_UPLOAD_BYTES == 2048


def test_host_config_push_collapses_multiline_values(host_config):
    (host_config / "config.toml").write_text(
        "[llm]\n"
        "efforts = [\n"
        '  "low",\n'
        '  "high",\n'
        "]\n"
    )
    written = settings.push_host_config({"llm.efforts": "low, max"})
    assert written == ["llm.efforts"]
    assert (host_config / "config.toml").read_text() == '[llm]\nefforts = [ "low", "max" ]\n'
    assert config.EFFORTS == ["low", "max"]


def test_host_config_push_handles_section_comments(host_config):
    (host_config / "config.toml").write_text("[llm] # tuning\nrequest_timeout = 180\n")
    written = settings.push_host_config({"llm.request_timeout": "77"})
    assert written == ["llm.request_timeout"]
    assert (host_config / "config.toml").read_text() == "[llm] # tuning\nrequest_timeout = 77\n"
    assert config.REQUEST_TIMEOUT == 77


def test_host_config_reload_applies_and_prunes_overlay(host_config):
    settings.save_runtime({"llm.request_timeout": "99", "tools.http_timeout": "45"})
    assert config.REQUEST_TIMEOUT == 99
    assert settings.host_config_state()["changed"] is False

    (host_config / "config.toml").write_text(
        CONFIG_TOML.replace("request_timeout = 180", "request_timeout = 240")
    )
    assert settings.host_config_state()["changed"] is True

    changed = settings.reload_host_config()
    # the file defines request_timeout, so it beats the stale overlay copy;
    # http_timeout is not in the file, so the override survives
    assert changed == ["llm.request_timeout"]
    assert config.REQUEST_TIMEOUT == 240
    assert config.HTTP_TIMEOUT == 45
    assert config.read_settings_overlay() == {"tools": {"http_timeout": 45}}
    assert settings.host_config_state()["changed"] is False
    # second reload: nothing changed
    assert settings.reload_host_config() == []


def test_host_config_auto_apply_record(host_config, monkeypatch):
    monkeypatch.setattr(settings, "_LAST_CONFIG_APPLIED", {"keys": [], "at": None})
    assert settings.host_config_state()["changed"] is False
    assert settings.host_config_state()["last_applied"] == {"keys": [], "at": None}

    (host_config / "config.toml").write_text(
        CONFIG_TOML.replace("request_timeout = 180", "request_timeout = 200")
    )
    assert settings.host_config_state()["changed"] is True
    changed = settings.reload_host_config()
    settings.note_config_auto_applied(changed)
    state = settings.host_config_state()
    assert state["changed"] is False
    assert state["last_applied"]["keys"] == ["llm.request_timeout"]
    assert state["last_applied"]["at"] is not None


def test_endpoints_status(host_config):
    data = settings.endpoints_status()
    assert data["mounted"] is True
    assert data["writable"] is True
    entries = {entry["name"]: entry for entry in data["endpoints"]}
    assert set(entries) == {"deepseek", "9router"}
    assert entries["deepseek"]["default"] is True
    assert entries["deepseek"]["thinking"] is True
    assert entries["9router"]["default"] is False
    assert entries["9router"]["base_url"] == "http://host.docker.internal:20128/v1"
    assert entries["9router"]["key_configured"] is False


def test_save_endpoint_adds_new(host_config):
    out = settings.save_endpoint(
        {
            "name": "local",
            "base_url": "http://127.0.0.1:8080/v1",
            "api_key_env": "LOCAL_API_KEY",
            "thinking": True,
        }
    )
    entries = {entry["name"]: entry for entry in out["endpoints"]}
    assert entries["local"]["base_url"] == "http://127.0.0.1:8080/v1"
    assert entries["local"]["thinking"] is True
    assert entries["local"]["default"] is False
    body = (host_config / "config.toml").read_text()
    assert "[llm.providers.local]" in body
    assert 'base_url = "http://127.0.0.1:8080/v1"' in body
    assert config.PROVIDERS["local"]["api_key_env"] == "LOCAL_API_KEY"
    # the new endpoint's key var becomes manageable in the keys section
    assert "LOCAL_API_KEY" in settings.allowed_vars()


def test_save_endpoint_updates_quoted_section(host_config):
    settings.save_endpoint(
        {
            "name": "9router",
            "base_url": "http://10.0.0.5:20128/v1",
            "api_key_env": "ROUTER9_API_KEY",
            "thinking": True,
        }
    )
    body = (host_config / "config.toml").read_text()
    # replaced in place — the quoted section is not duplicated
    assert body.count("9router") == 1
    assert 'base_url = "http://10.0.0.5:20128/v1"' in body
    assert "host.docker.internal" not in body
    assert config.PROVIDERS["9router"]["base_url"] == "http://10.0.0.5:20128/v1"
    assert config.PROVIDERS["9router"]["thinking"] is True


def test_save_endpoint_validation(host_config):
    with pytest.raises(ValueError):
        settings.save_endpoint({"name": "bad name", "base_url": "http://x", "api_key_env": "K"})
    with pytest.raises(ValueError):
        settings.save_endpoint({"name": "ok", "base_url": "ftp://x", "api_key_env": "K"})
    with pytest.raises(ValueError):
        settings.save_endpoint({"name": "ok", "base_url": "http://x", "api_key_env": "lower"})
    assert set(config.PROVIDERS) == {"deepseek", "9router"}


def test_delete_endpoint(host_config):
    out = settings.delete_endpoint("9router")
    assert {entry["name"] for entry in out["endpoints"]} == {"deepseek"}
    body = (host_config / "config.toml").read_text()
    assert "9router" not in body
    assert "[llm.providers.deepseek]" in body
    with pytest.raises(ValueError):
        settings.delete_endpoint("deepseek")  # the default endpoint is protected
    with pytest.raises(ValueError):
        settings.delete_endpoint("nope")


def test_endpoint_edits_need_writable(host_config):
    path = host_config / "config.toml"
    path.chmod(0o444)
    try:
        with pytest.raises(ValueError):
            settings.save_endpoint(
                {"name": "x", "base_url": "http://x:1/v1", "api_key_env": "X_KEY"}
            )
        with pytest.raises(ValueError):
            settings.delete_endpoint("9router")
    finally:
        path.chmod(0o644)


def test_reload_reports_endpoint_changes(host_config):
    assert settings.host_config_state()["changed"] is False
    edited = CONFIG_TOML.replace(
        'base_url = "http://host.docker.internal:20128/v1"',
        'base_url = "http://edited.internal:20128/v1"',
    )
    (host_config / "config.toml").write_text(edited)
    assert settings.host_config_state()["changed"] is True
    changed = settings.reload_host_config()
    assert changed == ["llm.providers.9router"]
    assert config.PROVIDERS["9router"]["base_url"] == "http://edited.internal:20128/v1"
