from app import config


def test_report_policy_is_in_every_prompt():
    """The report-as-you-go rule must load before the campaign prompt, for every mode."""
    names = [p.name for p in config.INSTRUCTION_FILES]
    assert "findings_report.md" in names
    # operator_policy first, then the report rule, so it applies to supervisors and solo agents
    assert names.index("findings_report.md") > names.index("operator_policy.md")


def test_report_policy_mandates_incremental_writing():
    text = (config.ROOT / "prompts" / "findings_report.md").read_text()
    assert "FINDINGS.md" in text
    assert "before you do anything else" in text
    assert "Do not wait for more findings" in text
    assert "Never create a duplicate section" in text
    assert "RETRACTED" in text
    assert "## Finding #1" in text


def test_report_policy_ships_a_fix_prompt_per_finding():
    text = (config.ROOT / "prompts" / "findings_report.md").read_text()
    flat = " ".join(text.split())
    assert "The fix prompt" in text
    assert "**Fix prompt**" in text
    assert "Root cause" in text
    assert "Required change" in text
    assert "Acceptance criteria" in text
    assert "Verify:" in text
    assert "never as a later cleanup" in flat
