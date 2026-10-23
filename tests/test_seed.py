import json

from app import config, seed


def write(path, data):
    path.write_text(json.dumps(data))
    return path


def test_substitutes_target(tmp_path, monkeypatch):
    path = write(tmp_path / "seed.json", [
        {"role": "user", "content": "Begin {{target}}."},
        {
            "role": "assistant",
            "content": "Initialized {{target}}.",
            "parts": [{"type": "tool", "args": {"cmd": "echo {{target}}"}}],
        },
    ])
    monkeypatch.setattr(config, "SEED_HISTORY", path)

    messages = seed.messages("example.com")
    assert messages[0]["content"] == "Begin example.com."
    assert messages[1]["parts"][0]["args"]["cmd"] == "echo example.com"


def test_disabled_when_unset(monkeypatch):
    monkeypatch.setattr(config, "SEED_HISTORY", None)
    assert seed.messages("x") == []


def test_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SEED_HISTORY", tmp_path / "missing.json")
    assert seed.messages("x") == []


def test_invalid_json(monkeypatch, tmp_path):
    path = tmp_path / "seed.json"
    path.write_text("{not json")
    monkeypatch.setattr(config, "SEED_HISTORY", path)
    assert seed.messages("x") == []


def test_non_list_ignored(monkeypatch, tmp_path):
    path = write(tmp_path / "seed.json", {"role": "user"})
    monkeypatch.setattr(config, "SEED_HISTORY", path)
    assert seed.messages("x") == []


def test_non_dict_entries_skipped(monkeypatch, tmp_path):
    path = write(tmp_path / "seed.json", ["nope", {"role": "user", "content": "hi"}])
    monkeypatch.setattr(config, "SEED_HISTORY", path)
    assert seed.messages("x") == [{"role": "user", "content": "hi"}]
