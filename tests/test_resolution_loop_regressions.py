from pathlib import Path

from core.agent import (
    _explicit_local_code_search,
    _explicit_memory_request,
    _ground_successful_search_results,
    _local_search_pattern,
    _normalize_workspace_search_path,
)

def test_local_code_search_detection():
    assert _explicit_local_code_search(
        "locate any code that controls retries and escalation"
    )
    assert not _explicit_local_code_search(
        "search online for current retry documentation"
    )

def test_search_pattern_expands_network_terms():
    pattern = _local_search_pattern(
        "locate code for dns ip backdoor and scraping"
    )
    for term in ("dns", "getaddrinfo", "socket", "callback", "requests"):
        assert term in pattern

def test_absolute_model_paths_are_normalized(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "utils").mkdir()
    (tmp_path / "kuza2").write_text("#!/bin/sh\n")
    assert _normalize_workspace_search_path("/", tmp_path) == "."
    assert _normalize_workspace_search_path("/core", tmp_path) == "core"
    assert _normalize_workspace_search_path("/utils", tmp_path) == "utils"
    assert _normalize_workspace_search_path("/kuza2", tmp_path) == "."
    assert _normalize_workspace_search_path("/outside", tmp_path) == "."

def test_note_save_requires_explicit_request():
    assert _explicit_memory_request("remember this result for later")
    assert not _explicit_memory_request("locate code and report matches")

def test_search_summary_is_grounded():
    summary = _ground_successful_search_results([
        ("search_files", "core/net.py:12: socket.getaddrinfo(host, 443)")
    ])
    assert "real tool evidence" in summary
    assert "core/net.py" in summary
    assert "No files were changed" in summary

def test_no_evidence_is_incomplete():
    summary = _ground_successful_search_results([])
    assert summary.startswith("[INCOMPLETE]")
    assert "No files were changed" in summary

def test_only_one_refusal_layer_remains():
    source = Path("core/agent.py").read_text(encoding="utf-8")
    assert "KUZA_SAFE_INTENT_HOOK_V2" not in source
    assert "_kuza_refusal_retries" not in source
    assert "KUZA_RESOLUTION_LOOP_FIX_V3" in source

def test_success_learning_uses_successful_tools():
    source = Path("core/agent.py").read_text(encoding="utf-8")
    assert "successful_tools = []" in source
    assert "successful_search_results = []" in source
    assert "success=_outcome_success" in source
    assert "successful_tools," in source

def test_unsolicited_note_save_is_blocked():
    source = Path("core/agent.py").read_text(encoding="utf-8")
    assert 'name == "note_save"' in source
    assert "_explicit_memory_request(user_message)" in source
