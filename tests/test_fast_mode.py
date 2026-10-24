import asyncio

import pytest

from app import agent, config, db, routes, supervisor, tools


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "l4rpness.db")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(agent, "provider_key", lambda provider: "test-key")
    routes.RUNNING.clear()
    db.init()

    async def fake_models():
        return ["m"]

    monkeypatch.setattr(agent, "models", fake_models)
    yield
    routes.RUNNING.clear()
    for task in list(supervisor.SUPERVISORS.values()):
        task.cancel()


def _names(schemas):
    return [s["function"]["name"] for s in schemas]


def _fast(**extra):
    return {"mode": "continuous", "fast_mode": 1, **extra}


DELEGATION = {
    "spawn_hypothesis_generator",
    "hypothesis_list",
    "hypothesis_update",
    "generator_status",
    "spawn_hunter",
    "worker_status",
    "worker_stop",
}


def test_fast_schemas_have_no_subagents():
    names = set(_names(tools.schemas_for("fast", _fast())))
    assert {"bash", "read_file", "write_file", "write_todos"} <= names
    assert not names & DELEGATION
    # hunters file results, a solo agent keeps notes and findings files
    assert "hunt_report" not in names and "hunter_done" not in names
    assert "complete_engagement" not in names


def test_fast_goal_keeps_the_completion_tool():
    names = set(_names(tools.schemas_for("fast", _fast(mode="goal"))))
    assert "complete_engagement" in names
    assert not names & DELEGATION


def test_fast_kernel_gets_the_vm_tools():
    names = set(_names(tools.schemas_for("fast", _fast())))
    assert "vm_exec" not in names and "vm_start" not in names

    names = set(_names(tools.schemas_for("fast", _fast(kernel_enabled=1))))
    assert {"vm_exec", "vm_status", "vm_console", "vm_flag"} <= names
    assert {"vm_start", "vm_stop", "vm_restart", "vm_reset"} <= names


def test_fast_allow_list_blocks_subagents():
    allow = tools.allowed_for("fast", _fast())
    assert allow is not None and {"bash", "write_file"} <= allow
    assert not allow & DELEGATION

    out, ok, _ = asyncio.run(
        tools.execute("spawn_hunter", {"hypothesis_ids": [1]}, 1, allow=allow)
    )
    assert not ok and "not available" in out


def test_fast_refuses_spawning_even_without_an_allow_list():
    """A manual chat turn runs with allow=None; fast mode must still not spawn."""
    with db.db() as conn:
        conn.execute(
            "INSERT INTO campaigns (id, target, model, fast_mode) VALUES (1, 't', 'm', 1)"
        )
    out, ok, _ = asyncio.run(tools.execute("spawn_hunter", {"hypothesis_ids": [1]}, 1))
    assert not ok and "fast mode" in out

    out, ok, _ = asyncio.run(tools.execute("spawn_hypothesis_generator", {"focus": "x"}, 1))
    assert not ok and "fast mode" in out


def test_fast_prompt_is_the_solo_agent_brief():
    with db.db() as conn:
        conn.execute(
            "INSERT INTO campaigns (id, target, model, kernel_enabled) VALUES (1, 't', 'm', 1)"
        )
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (2, 't', 'm')")
        with_vm = conn.execute("SELECT * FROM campaigns WHERE id = 1").fetchone()
        without_vm = conn.execute("SELECT * FROM campaigns WHERE id = 2").fetchone()

    prompt = agent.build_prompt(with_vm, [], tool_kind="fast")
    # the delegation policy belongs to the supervisor, not to a solo agent
    assert "You are a supervisor" not in prompt
    assert "vm_exec" in prompt

    assert "vm_exec" not in agent.build_prompt(without_vm, [], tool_kind="fast")


def test_fast_loop_runs_without_delegation(monkeypatch):
    monkeypatch.setattr(supervisor, "DELEGATION_NUDGE_SECONDS", 0)
    chains: list[dict] = []

    async def fake_run(campaign_id, campaign, attachments, history, content, run, files=(), store_user=True, keep=False, tool_kind=None, include_attachments=True):
        chains.append({"prompt": history[-1]["content"], "tool_kind": tool_kind})
        with db.db() as conn:
            conn.execute(
                "INSERT INTO messages (campaign_id, role, content) VALUES (?, 'assistant', ?)",
                (campaign_id, f"reply {len(chains)}"),
            )
        return {"ok": True, "stopped": False, "empty": False, "error": ""}

    monkeypatch.setattr(routes, "_run", fake_run)

    with db.db() as conn:
        conn.execute(
            "INSERT INTO campaigns (id, target, model, fast_mode) VALUES (1, 'target.example', 'm', 1)"
        )

    async def drive():
        assert supervisor.start(1)
        task = supervisor.SUPERVISORS[1]
        for _ in range(200):
            if len(chains) >= 3:
                break
            await asyncio.sleep(0.01)
        supervisor.stop(1)
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(drive())

    assert len(chains) >= 3
    assert all(chain["tool_kind"] == "fast" for chain in chains)
    assert chains[0]["prompt"] == supervisor.FAST_BEGIN_PROMPT.format(
        target="target.example"
    )
    assert chains[1]["prompt"].startswith(supervisor.FAST_CONTINUE_PROMPT)
    for chain in chains:
        assert supervisor.DELEGATION_NUDGE not in chain["prompt"]
        assert "spawn_hypothesis_generator" not in chain["prompt"]
        assert "HYPOTHESES:" not in chain["prompt"]
