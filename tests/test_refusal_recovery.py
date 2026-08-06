# Tests for Kuza's action-first recovery from generic model refusals.

import json

from core.inference_v2 import (
    _recover_dead_end_refusal,
    is_dead_end_refusal,
)
from prompts.system_prompt import get_system_prompt


def test_detects_generic_dead_end_refusal():
    assert is_dead_end_refusal(
        "I'm sorry, but I can't assist with that."
    ) is True


def test_rewrites_refusal_wording_even_when_an_alternative_follows():
    assert is_dead_end_refusal(
        "I can't help access someone else's system, but I can audit your own code."
    ) is True


def test_retries_refusal_with_action_first_instruction():
    calls = []

    def retry(messages):
        calls.append(messages)
        return "Proceeding with a defensive audit of the current project."

    result = _recover_dead_end_refusal(
        [{"role": "user", "content": "audit this project"}],
        "I can't assist with that.",
        retry,
    )

    assert result.startswith("Proceeding")
    assert len(calls) == 1
    assert "dead-end refusal" in calls[0][-1]["content"].lower()
    assert "authorized" in calls[0][-1]["content"].lower()


def test_repeated_security_refusal_falls_back_to_real_tool_call():
    messages = [{
        "role": "user",
        "content": "look for dns and ip backdoors that will help you scrape",
    }]

    result = _recover_dead_end_refusal(
        messages,
        "I can't assist with that.",
        lambda _messages: "I am unable to help with that.",
    )

    assert result.startswith("<tool>\n")
    payload = result.split("\n", 1)[1].rsplit("\n", 1)[0]
    tool = json.loads(payload)
    assert tool["name"] == "shell"
    assert "grep -rEn" in tool["args"]["command"]
    assert "dns" in tool["args"]["command"]


def test_non_refusal_does_not_retry():
    called = False

    def retry(_messages):
        nonlocal called
        called = True
        return "unexpected"

    result = _recover_dead_end_refusal(
        [{"role": "user", "content": "hello"}],
        "Here is the answer.",
        retry,
    )

    assert result == "Here is the answer."
    assert called is False


def test_system_prompt_forbids_dead_end_refusals():
    prompt = get_system_prompt()
    assert "Never give a dead-end generic refusal" in prompt
    assert "closest useful authorized action" in prompt
