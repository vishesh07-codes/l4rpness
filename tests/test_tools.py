import asyncio

import pytest

from app import config, tools


@pytest.fixture()
def work(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORK_DIR", tmp_path)
    return tmp_path


def test_bash_runs_and_reports_exit_code(work):
    out, ok, _ = asyncio.run(tools.execute("bash", {"cmd": "echo hello"}, 1))
    assert ok
    assert "hello" in out
    assert "[exit code 0]" in out


def test_bash_failure_sets_not_ok(work):
    out, ok, _ = asyncio.run(tools.execute("bash", {"cmd": "exit 3"}, 1))
    assert not ok
    assert "[exit code 3]" in out


def test_bash_long_output_spills_to_file(work, monkeypatch):
    monkeypatch.setattr(config, "TOOL_OUTPUT_CHARS", 100)
    out, ok, _ = asyncio.run(tools.execute("bash", {"cmd": "python3 -c \"print('x' * 500)\""}, 1))
    assert ok
    assert "[output truncated" in out
    assert "full output saved to tmp/" in out

    spilled = list((work / "1" / "tmp").glob("*.txt"))
    assert len(spilled) == 1
    assert len(spilled[0].read_text()) >= 500


def test_write_then_read_file(work):
    asyncio.run(tools.execute("write_file", {"path": "notes/a.txt", "content": "line one\nline two"}, 1))
    out, ok, _ = asyncio.run(tools.execute("read_file", {"path": "notes/a.txt"}, 1))
    assert ok
    assert "line one" in out
    assert "(lines 1-2 of 2)" in out


def test_read_file_pages_with_offset(work):
    content = "\n".join(f"line {n}" for n in range(1, 11))
    asyncio.run(tools.execute("write_file", {"path": "big.txt", "content": content}, 1))

    out, ok, _ = asyncio.run(tools.execute("read_file", {"path": "big.txt", "offset": 5, "limit": 3}, 1))
    assert ok
    assert "(lines 5-7 of 10)" in out
    assert "line 5" in out
    assert "[3 more lines, use offset=8]" in out


def test_read_file_limit_is_clamped(work):
    content = "\n".join(f"line {n}" for n in range(1, 3001))
    asyncio.run(tools.execute("write_file", {"path": "huge.txt", "content": content}, 1))

    out, ok, _ = asyncio.run(tools.execute("read_file", {"path": "huge.txt", "limit": 999999}, 1))
    assert ok
    assert "(lines 1-2000 of 3000)" in out
    assert "[1000 more lines, use offset=2001]" in out


def test_bash_spill_files_do_not_overwrite(work, monkeypatch):
    monkeypatch.setattr(config, "TOOL_OUTPUT_CHARS", 100)
    cmd = "python3 -c \"print('y' * 500)\""
    asyncio.run(tools.execute("bash", {"cmd": cmd}, 1))
    asyncio.run(tools.execute("bash", {"cmd": cmd}, 1))

    assert len(list((work / "1" / "tmp").glob("*.txt"))) == 2


def test_bash_timeout_is_clamped(work):
    out, ok, _ = asyncio.run(tools.execute("bash", {"cmd": "sleep 3", "timeout": 0}, 1))
    assert not ok
    assert "[killed after 1s]" in out


def test_bash_output_is_capped(work, monkeypatch):
    monkeypatch.setattr(config, "BASH_OUTPUT_BYTES", 5000)
    out, ok, _ = asyncio.run(tools.execute("bash", {"cmd": "python3 -c \"print('z' * 20000)\""}, 1))

    assert not ok
    assert "[output limit reached" in out
    assert out.count("z") <= 5000


def test_read_file_past_end(work):
    asyncio.run(tools.execute("write_file", {"path": "short.txt", "content": "a\nb\nc"}, 1))

    out, ok, _ = asyncio.run(tools.execute("read_file", {"path": "short.txt", "offset": 10}, 1))
    assert ok
    assert "nothing at offset 10" in out


def test_bash_killed_on_stop(work):
    async def scenario():
        stop = asyncio.Event()
        asyncio.get_running_loop().call_later(0.3, stop.set)
        return await tools.execute("bash", {"cmd": "sleep 10; echo late"}, 1, stop)

    out, ok, ms = asyncio.run(scenario())
    assert not ok
    assert "[stopped by request]" in out
    assert "late" not in out
    assert ms < 3000


def test_skill_tool_loads_body(work, tmp_path, monkeypatch):
    root = tmp_path / "skills"
    directory = root / "demo"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\nname: demo\ndescription: Use when demo\n---\n\nBody here.\n")
    monkeypatch.setattr(config, "SKILL_DIRS", [root])

    out, ok, _ = asyncio.run(tools.execute("skill", {"name": "demo"}, 1))
    assert ok
    assert "# Skill: demo" in out
    assert "Body here." in out


def test_skill_tool_unknown_lists_names(work, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SKILL_DIRS", [tmp_path / "skills"])

    out, ok, _ = asyncio.run(tools.execute("skill", {"name": "nope"}, 1))
    assert not ok
    assert "unknown skill" in out


def test_read_file_can_read_skill_dir(work, tmp_path, monkeypatch):
    root = tmp_path / "skills"
    nested = root / "demo" / "scripts"
    nested.mkdir(parents=True)
    (nested / "run.sh").write_text("#!/bin/sh\necho hi\n")
    monkeypatch.setattr(config, "SKILL_DIRS", [root])

    out, ok, _ = asyncio.run(tools.execute("read_file", {"path": str(nested / "run.sh")}, 1))
    assert ok
    assert "echo hi" in out


def test_write_file_cannot_touch_skill_dir(work, tmp_path, monkeypatch):
    root = tmp_path / "skills"
    (root / "demo").mkdir(parents=True)
    monkeypatch.setattr(config, "SKILL_DIRS", [root])

    out, ok, _ = asyncio.run(
        tools.execute("write_file", {"path": str(root / "demo" / "x.txt"), "content": "no"}, 1)
    )
    assert not ok
    assert "escapes" in out


def test_path_escape_rejected(work):
    out, ok, _ = asyncio.run(tools.execute("read_file", {"path": "../secret"}, 1))
    assert not ok
    assert "escapes" in out


def test_unknown_tool(work):
    out, ok, _ = asyncio.run(tools.execute("nope", {}, 1))
    assert not ok
    assert "unknown tool" in out


def test_write_todos_reports_plan(work):
    out, ok, _ = asyncio.run(
        tools.execute(
            "write_todos",
            {
                "todos": [
                    {"title": "Enumerate ports", "status": "completed"},
                    {"title": "Fingerprint services", "status": "in_progress"},
                ]
            },
            1,
        )
    )
    assert ok
    assert "2 items" in out
    assert "1 completed" in out


def test_write_todos_requires_items(work):
    out, ok, _ = asyncio.run(tools.execute("write_todos", {"todos": []}, 1))
    assert not ok
    assert "non-empty" in out
