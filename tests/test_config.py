from app import config


def test_defaults_when_no_file(tmp_path):
    settings = config.load(tmp_path / "missing.toml")
    assert settings["server"]["port"] == 4517
    assert settings["deepseek"]["model"] == "deepseek-flash"
    assert settings["deepseek"]["temperature"] == 0.3
    assert settings["uploads"]["max_files_per_field"] == 5
    assert settings["tools"]["max_steps"] == -1
    assert settings["tools"]["bash_output_bytes"] == 10 * 1024 * 1024
    assert settings["tools"]["read_max_lines"] == 2000
    assert settings["tools"]["http_timeout"] == 30


def test_file_overrides_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[server]\nport = 9999\n\n[tools]\nbash_timeout = 5\n\n[deepseek]\ntemperature = 1.2\n")

    settings = config.load(path)
    assert settings["server"]["port"] == 9999
    assert settings["tools"]["bash_timeout"] == 5
    assert settings["tools"]["output_chars"] == 20_000
    assert settings["deepseek"]["model"] == "deepseek-flash"
    assert settings["deepseek"]["temperature"] == 1.2


def test_pricing_defaults_to_zero(tmp_path):
    settings = config.load(tmp_path / "missing.toml")
    assert settings["pricing"] == {"input": 0.0, "cached_input": 0.0, "output": 0.0}


def test_cost_usd_uses_configured_prices(monkeypatch):
    monkeypatch.setattr(config, "PRICE_INPUT", 1.0)
    monkeypatch.setattr(config, "PRICE_CACHED_INPUT", 0.5)
    monkeypatch.setattr(config, "PRICE_OUTPUT", 2.0)

    assert config.cost_usd(1_000_000, 0, 0) == 1.0
    assert config.cost_usd(1_000_000, 1_000_000, 0) == 0.5
    assert config.cost_usd(0, 0, 1_000_000) == 2.0
