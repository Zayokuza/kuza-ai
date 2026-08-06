from core.agent import (
    _kuza_looks_like_dead_end_refusal,
    _kuza_safe_intent_fallback,
    _kuza_safe_intent_retry_prompt,
)
from prompts.system_prompt import get_system_prompt


def test_detects_generic_programming_guidelines_refusal():
    text = (
        "I must clarify that assisting with such activities is not within my "
        "programming guidelines. Ask about a different task."
    )
    assert _kuza_looks_like_dead_end_refusal(text)


def test_concrete_safe_help_is_not_a_dead_end():
    text = (
        "I can't help create unauthorized persistence. I can help with DNS "
        "failover, an approved proxy pool, health checks, and rate limits."
    )
    assert not _kuza_looks_like_dead_end_refusal(text)


def test_retry_prompt_preserves_request_and_safe_scope():
    prompt = _kuza_safe_intent_retry_prompt(
        "Explain DNS and IP backdoors for web scraping"
    )
    assert "ORIGINAL REQUEST" in prompt
    assert "DNS failover" in prompt
    assert "approved proxy pools" in prompt
    assert "unauthorized access" in prompt


def test_networking_fallback_is_useful_and_narrow():
    fallback = _kuza_safe_intent_fallback(
        "Explain DNS and IP backdoors for web scraping"
    )
    assert "DNS failover" in fallback
    assert "IP rotation" in fallback
    assert "rate limits" in fallback
    assert "unauthorized backdoors" in fallback
    assert "programming guidelines" not in fallback


def test_system_prompt_requires_safe_useful_interpretation():
    prompt = get_system_prompt()
    assert "AMBIGUOUS OR MIXED-RISK REQUESTS" in prompt
    assert "Do not refuse an entire request" in prompt
    assert "continue with useful allowed guidance" in prompt
