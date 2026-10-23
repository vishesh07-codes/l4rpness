from pathlib import Path

import pytest

from app import config, skills


def make_skill(root: Path, name: str, description: str = "Use when testing", body: str = "Do the thing."):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n")


@pytest.fixture()
def skill_roots(tmp_path, monkeypatch):
    builtin = tmp_path / "skills"
    custom = tmp_path / "data"
    monkeypatch.setattr(config, "SKILL_DIRS", [builtin])
    monkeypatch.setattr(config, "DATA_DIR", custom)
    return builtin, custom


def test_discover_parses_frontmatter(skill_roots):
    builtin, custom = skill_roots
    make_skill(builtin, "bac-hunting", "Use when hunting access control bugs", "Step one.")
    make_skill(custom / "skills", "my-skill", "Use when testing custom stuff", "Custom body.")

    entries = skills.discover()
    assert [entry["name"] for entry in entries] == ["bac-hunting", "my-skill"]
    assert entries[0]["editable"] is False
    assert entries[1]["editable"] is True


def test_discover_skips_malformed(skill_roots):
    builtin, _ = skill_roots
    bad = builtin / "bad" / "SKILL.md"
    bad.parent.mkdir(parents=True)
    bad.write_text("no frontmatter here")

    assert skills.discover() == []


def test_load_strips_frontmatter(skill_roots):
    builtin, _ = skill_roots
    make_skill(builtin, "demo", "Use when demo", "Body line one.\n\nBody line two.")

    loaded = skills.load("demo")
    assert loaded is not None
    assert loaded["body"].startswith("Body line one.")
    assert "---" not in loaded["body"]
    assert skills.load("missing") is None


def test_index_lists_names(skill_roots):
    builtin, _ = skill_roots
    make_skill(builtin, "alpha", "Use when alpha")
    make_skill(builtin, "beta", "Use when beta")

    block = skills.index()
    assert "<available_skills>" in block
    assert "- alpha: Use when alpha" in block
    assert "- beta: Use when beta" in block


def test_index_empty_without_skills(skill_roots):
    assert skills.index() == ""


def test_save_creates_custom_skill(skill_roots):
    skills.save("My-Skill", "Use when custom", "Do this.")

    entry = skills.load("my-skill")
    assert entry is not None
    assert entry["editable"] is True
    assert entry["body"] == "Do this."


def test_save_rejects_bad_slug(skill_roots):
    with pytest.raises(ValueError):
        skills.save("Bad Name!", "desc", "body")


def test_save_conflict_and_overwrite(skill_roots):
    skills.save("dup", "desc", "one")
    with pytest.raises(FileExistsError):
        skills.save("dup", "desc", "two")

    skills.save("dup", "desc", "two", overwrite=True)
    entry = skills.load("dup")
    assert entry is not None
    assert entry["body"] == "two"


def test_delete_removes_custom_skill(skill_roots):
    _, custom = skill_roots
    skills.save("temp", "Use when temp", "body")
    assert (custom / "skills" / "temp").is_dir()

    skills.delete("temp")
    assert skills.load("temp") is None
    assert not (custom / "skills" / "temp").exists()


def test_delete_missing_raises(skill_roots):
    with pytest.raises(FileNotFoundError):
        skills.delete("nope")


def test_delete_builtin_refused(skill_roots):
    builtin, _ = skill_roots
    make_skill(builtin, "core", "Use when core")

    with pytest.raises(PermissionError):
        skills.delete("core")
    assert skills.load("core") is not None
