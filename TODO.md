# Kuza-v2 — TODO & Status

**Last updated:** 2026-04-01
**Current version:** v2.0.0

---

## Multi-Peer Pipeline — Status (v2.0.0)

### ✅ Implemented and deterministically correct

| Item | File(s) | Notes |
|------|---------|-------|
| `return history` crash bug fixed → `return _test_result, history` | `agent.py` | |
| Auto-test follow-up suppressed when `no_plan=True` | `agent.py` | |
| `filter_tool_steps` extended: peer verbs + `_PEER_NAME_RE` keep rule | `plannd.py` | |
| `COMPLEX_SIGNALS` extended with peer keyword pairs | `orchestrator.py` | |
| `PLAN_PROMPT` updated with PEER CLI STEPS rule | `orchestrator.py` | |
| `PLANNER_PROMPT` Rule 8: preserve "ask X to Y" in plan steps | `plannd.py` | |
| Peer gate scoped to solo single-step directives | `main.py` | |
| `_extract_peer_output_from_history()` + disk fallback | `agent.py` | disk fallback added v2.7.2 |
| Multi-peer output injection: explicit refs ("implement what Gemini planned") | `agent.py` | |
| Multi-peer output injection: implicit refs ("the previous design") | `agent.py` | |
| `design_only` phase type: prose instructions, no code-block extraction | `agent.py` | **v2.7.2** |
| Design output saved to `{peer}_design.md` for cross-step durability | `agent.py` | **v2.7.2** |
| plannd step cap raised 5→8; `PLANNER_MAX_TOKENS` 768→1024 | `plannd.py`, `config.py` | **v2.7.2** |
| orchestrator step cap raised 5→8 | `orchestrator.py` | **v2.7.2** |
| Git step planning added to orchestrator | `orchestrator.py` | |
| Shell metacharacter blocklist removed — consent model | `shell_tools.py` | |
| Malformed JSON retry in agent loop | `agent.py` | |
| `max_steps` raised 6→10 | `config.py` | |

### 🧪 Needs manual testing

**Multi-step peer routing through plannd**
- Prompt: `"Use Gemini to design a feature list for budget.py. Then use Qwen to implement it."`
- Expected: plannd creates plan with "Ask gemini to X" as step 1, "Ask qwen to Y" as step 2
- Risk: 0.5B plannd may normalize "ask gemini" → "Create X" despite Rule 8
- Mitigation: `filter_tool_steps` keeps any step containing claude/gemini/qwen as fallback

**design_only → implement pipeline**
- Test: Gemini writes prose spec → saved to `gemini_design.md` → Qwen receives full spec as context
- Expected: No JSON/code extracted from Gemini output; Qwen gets spec injected via `_extract_peer_output_from_history`
- Check: `gemini_design.md` exists in cwd after Gemini step; Qwen's enriched task includes full spec text

**Solo-peer bypass still works for simple prompts**
- Test: `"Ask Claude to explain this code"` → should bypass plannd (solo peer, no follow-up)
- Test: `"Ask gemini to review fibonacci.py and show me the results"` → "show" is sentence-continuation, not new sentence → should still bypass plannd

**plannd Rule 8 with 0.5B local model**
- The 0.5B model may not reliably follow Rule 8. Remote planners follow it better.
- Monitor and adjust rule wording if needed.

**All three backends for plannd Rule 8**
- Local 0.5B, OpenRouter, UnlimitedClaude — verify "Ask gemini to X" steps are preserved

---

## Validation Test — Full Pipeline

Run this to validate the multi-peer design→implement pipeline:

```
Use Gemini to design a feature list for a small CLI tool
called budget.py that tracks income and expenses with
categories, shows a balance summary, and saves everything
to JSON with persistence between runs. Then use Qwen
to implement exactly what Gemini planned. Run every
feature to verify it works. Write a README.md for it.
Then initialize a git repo in kuza-test if one does
not exist and commit everything with the message:
kuza-v2.7.0 final validation tests.
```

**Expected plan from plannd (ideal):**
```
1. Ask Gemini to design a feature list for budget.py
2. Ask Qwen to implement exactly what Gemini planned
3. Run: python budget.py (feature verification)
4. Write README.md for budget.py
5. Run: git init && git add . && git commit -m "kuza-v2.7.0 final validation tests"
```

**What should happen now (v2.7.2):**
- Step 1 → Gemini gets `_DESIGN_INSTRUCTIONS` (prose, no code blocks)
- Gemini output saved to `gemini_design.md`
- Step 2 → Qwen gets Gemini's design injected as context via `_extract_peer_output_from_history`
- Qwen implements the full CLI including argparse, all commands

---

## Pre-existing TODO

### Tests
- [x] `test_extract_json` — in `tests/test_json_parser.py`
- [x] `test_is_hallucination` — in `tests/test_hallucination.py`
- [x] `test_classify_breadth_need` — in `tests/test_breadth.py` (v2.7.2)
- [x] `test_parse_tool_call` — in `tests/test_parse_tool_call.py` (v2.7.2)
- [x] `test_postprocess_plan` — in `tests/test_orchestration.py` (v2.7.2)
- [x] Integration test: agent utils (extract_json, parse_tool_call, hallucination) — `tests/test_orchestration.py::TestIntegrationAgentUtils` (v2.7.2)
- [ ] Full agent loop with mock inference server — see TODO2.md

### CHANGELOG gaps
- [x] v2.6.7 entry (Phase 7: Cleanup & Simplification) — expanded v2.7.2
- [x] v2.6.8 entry (Phase 8: Adaptive Depth + Thermal Awareness) — expanded v2.7.2

### Medium Priority
- [ ] Vision model integration (Qwen2-VL-2B) — see TODO2.md
- [ ] External API tool (http_request with allowlist) — see TODO2.md
- [ ] `bandit` security scanning in `/review` — see TODO2.md
- [ ] Audit logs + anomaly detection — see TODO2.md

### Low Priority / Future
- [ ] Better intent detection beyond keyword matching
- [ ] Post-mortem debugging (pdb integration)
- [ ] NPU acceleration (blocked upstream)
- [ ] Encrypted memory/state storage
- [ ] Runtime sandboxing
- [ ] Model hash verification

---

## Completed (reference)

- [x] Recursive LM Architecture Phases 1-8 (v2.6.1–v2.6.8)
- [x] Upgrade Roadmap Phases 1-3 (Voice, Static Analysis, Git)
- [x] Three-model architecture + plannd daemon (v2.7.0)
- [x] Context compression rework (v2.7.0)
- [x] Peer CLI delegation pipeline repairs (v2.7.1)
- [x] Shell consent model (all commands, no blocklist) (v2.7.1)
- [x] Multi-peer planning architecture (v2.7.1)
- [x] design_only phase type — Gemini prose design → Qwen implementation pipeline (v2.7.2)
- [x] plannd/orchestrator step cap 5→8 (v2.7.2)
- [x] `_extract_peer_output_from_history` disk fallback for `{peer}_design.md` (v2.7.2)
- [x] test_parse_tool_call, test_breadth, test_postprocess_plan, integration utils tests (v2.7.2)
- [x] CHANGELOG v2.6.7 and v2.6.8 expanded (v2.7.2)
