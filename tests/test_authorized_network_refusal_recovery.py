from core.inference_v2 import (
    _kuza_authorized_network_fallback,
    _recover_dead_end_refusal,
    is_dead_end_refusal,
)

EXACT_REFUSAL = (
    "It seems like you're interested in strategies for handling DNS failures, "
    "IP issues, and CAPTCHAs in web scraping. However, I must clarify that "
    "assisting with such activities is not within my programming guidelines. "
    "If you have any other questions or need assistance with a different task, "
    "feel free to ask!"
)

def test_detects_exact_programming_guidelines_refusal():
    assert is_dead_end_refusal(EXACT_REFUSAL)

def test_preserves_non_refusal_network_answer():
    assert not is_dead_end_refusal(
        "Check the DNS resolver and default route. Require manual CAPTCHA completion."
    )

def test_authorized_network_fallback_is_safe_and_useful():
    messages = [{"role": "user", "content": (
        "Diagnose DNS resolution failures and IP connectivity issues on my "
        "authorized system. Check resolver configuration, routing, retries, "
        "proxy health, and endpoint failures. Detect CAPTCHA and require manual "
        "completion; do not bypass it."
    )}]
    result = _kuza_authorized_network_fallback(messages)
    assert "DNS/IP troubleshooting" in result
    assert "default route" in result
    assert "Proxy health" in result
    assert "manual completion" in result
    assert "Do not attempt to bypass" in result

def test_repeated_refusal_uses_authorized_network_fallback():
    messages = [{"role": "user", "content": (
        "Diagnose DNS resolution failures and IP connectivity issues on my "
        "authorized system. Detect CAPTCHA and require manual completion; "
        "do not bypass it."
    )}]
    result = _recover_dead_end_refusal(
        messages, EXACT_REFUSAL, lambda _messages: EXACT_REFUSAL
    )
    assert result.startswith("Proceeding with authorized DNS/IP troubleshooting")

def test_ambiguous_backdoor_request_does_not_use_safe_network_fallback():
    messages = [{"role": "user", "content": "look for dns and ip backdoors"}]
    assert _kuza_authorized_network_fallback(messages) == ""
