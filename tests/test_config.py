from app import config


def test_defaults_when_no_file(tmp_path):
    settings = config.load(tmp_path / "missing.toml")
    assert settings["server"]["port"] == 4517
    assert settings["llm"]["provider"] == "deepseek"
    assert settings["llm"]["providers"]["deepseek"]["base_url"] == "https://api.deepseek.com"
    assert settings["llm"]["providers"]["deepseek"]["api_key_env"] == "DEEPSEEK_API_KEY"
    assert settings["llm"]["model"] == "deepseek-flash"
    assert settings["llm"]["temperature"] == 0.3
    assert settings["uploads"]["max_files_per_field"] == 5
    assert settings["agent"]["instructions"] == ["AGENTS.md", "prompts/operator_policy.md"]
    assert settings["agent"]["seed_history"] == "prompts/seed_history.json"
    assert settings["tools"]["max_steps"] == -1
    assert settings["tools"]["bash_output_bytes"] == 10 * 1024 * 1024
    assert settings["tools"]["read_max_lines"] == 2000
    assert settings["tools"]["http_timeout"] == 30


def test_file_overrides_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[server]\nport = 9999\n\n[tools]\nbash_timeout = 5\n\n[llm]\ntemperature = 1.2\n")

    settings = config.load(path)
    assert settings["server"]["port"] == 9999
    assert settings["tools"]["bash_timeout"] == 5
    assert settings["tools"]["output_chars"] == 20_000
    assert settings["llm"]["model"] == "deepseek-flash"
    assert settings["llm"]["temperature"] == 1.2


def test_provider_override(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[llm]\nprovider = "custom"\n\n'
        '[llm.providers.custom]\nbase_url = "http://10.0.0.9:8080/v1"\n'
        'api_key_env = "CUSTOM_KEY"\nthinking = false\n'
    )

    settings = config.load(path)
    assert settings["llm"]["provider"] == "custom"
    assert settings["llm"]["providers"]["custom"]["base_url"] == "http://10.0.0.9:8080/v1"
    assert settings["llm"]["providers"]["custom"]["thinking"] is False
