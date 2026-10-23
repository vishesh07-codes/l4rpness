import pytest

from app import config, workspace


@pytest.fixture()
def work(tmp_path, monkeypatch):
    root = tmp_path / "work"
    monkeypatch.setattr(config, "WORK_DIR", root)
    return root


def test_tree_empty_when_missing(work):
    assert workspace.tree(7) == []


def test_tree_lists_dirs_and_files(work):
    (work / "7" / "src").mkdir(parents=True)
    (work / "7" / "src" / "app.py").write_text("print('hi')")
    (work / "7" / "README.md").write_text("# hi")

    files = {entry["path"]: entry for entry in workspace.tree(7)}
    assert files["src"]["type"] == "dir"
    assert files["src/app.py"]["type"] == "file"
    assert files["src/app.py"]["size"] == len("print('hi')")
    assert workspace.read_text(7, "src/app.py")["content"] == "print('hi')"


def test_traversal_rejected(work):
    (work / "7").mkdir(parents=True)
    with pytest.raises(ValueError):
        workspace.read_text(7, "../../etc/passwd")
    with pytest.raises(ValueError):
        workspace.raw_path(7, "../outside.txt")


def test_missing_file(work):
    (work / "7").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        workspace.read_text(7, "nope.txt")


def test_read_truncates_large(work, monkeypatch):
    monkeypatch.setattr(workspace, "TEXT_CAP", 4)
    (work / "7").mkdir(parents=True)
    (work / "7" / "big.txt").write_text("abcdefghij")

    out = workspace.read_text(7, "big.txt")
    assert out["truncated"] is True
    assert out["content"] == "abcd"
