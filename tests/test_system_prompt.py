"""Keep the small-model prompt compact and aligned with implemented tools."""

from prompts.system_prompt import get_system_prompt


def test_prompt_is_compact_and_lists_every_specialized_tool():
    prompt = get_system_prompt()
    assert len(prompt) < 5_000
    for name in ("search_files", "web_search", "read_webpage", "holehe"):
        assert name in prompt


def test_prompt_allows_answers_and_requires_evidence_for_actions():
    prompt = get_system_prompt()
    assert "Answer questions directly" in prompt
    assert "never say an action succeeded unless" in prompt
    assert "Compound shell syntax may require explicit" in prompt
