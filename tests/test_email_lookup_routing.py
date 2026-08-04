"""Regression tests for deterministic email account-lookup routing."""

import core.agent as agent
import main


def test_bare_email_routes_to_lookup():
    assert agent.detect_email_account_lookup("test@example.com") == "test@example.com"


def test_natural_account_lookup_routes_to_lookup():
    assert (
        agent.detect_email_account_lookup(
            "search test@example.com for any accounts linked to it"
        )
        == "test@example.com"
    )


def test_unrelated_email_and_implementation_request_do_not_route():
    assert agent.detect_email_account_lookup("email test@example.com tomorrow") is None
    assert (
        agent.detect_email_account_lookup(
            "create a script to search test@example.com for linked accounts"
        )
        is None
    )


def test_run_agent_calls_holehe_without_model_or_planner(monkeypatch):
    calls = []

    class LearningStub:
        def learn_from_message(self, message):
            calls.append(("learn", message))

    def fake_execute(tool):
        calls.append(("tool", tool))
        return "Email checked: test@example.com\n\nRegistered accounts found: 1\n✓ Example"

    monkeypatch.setattr(agent, "_get_learning", lambda: LearningStub())
    monkeypatch.setattr(agent, "execute_tool", fake_execute)

    response, history = agent.run_agent("test@example.com", [])

    assert calls[-1] == (
        "tool",
        {"name": "holehe", "args": {"email": "test@example.com", "only_used": True}},
    )
    assert "Registered accounts found: 1" in response
    assert history[-1] == {"role": "assistant", "content": response}


def test_main_bypasses_planner_for_email_lookup(monkeypatch):
    calls = []

    def fake_run_agent(prompt, history, **kwargs):
        calls.append((prompt, kwargs))
        return "lookup result", history

    monkeypatch.setattr(main, "run_agent", fake_run_agent)
    monkeypatch.setattr(
        main,
        "_try_daemon_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("planner was called")),
    )

    response, _ = main._run_with_plan(
        "find accounts linked to test@example.com",
        [],
        yolo=False,
        use_plan=False,
        no_plan=False,
    )

    assert response == "lookup result"
    assert calls[0][1]["no_plan"] is True
