"""Regression tests for planner intent routing and plan grounding."""

from core.plannd import (
    MAX_PLAN_STEPS,
    filter_tool_steps,
    parse_steps,
    should_plan,
    validate_plan,
)


class TestPlannerIntent:
    def test_bare_email_does_not_plan(self):
        assert should_plan("person@example.com") is False

    def test_account_lookup_does_not_plan(self):
        assert should_plan(
            "search person@example.com for accounts linked to it"
        ) is False

    def test_question_does_not_plan(self):
        assert should_plan("How do I create a Python file?") is False

    def test_research_only_request_does_not_plan(self):
        assert should_plan("Research the latest Python release notes") is False

    def test_single_direct_edit_does_not_plan(self):
        assert should_plan("Fix the typo in README.md") is False

    def test_create_and_run_does_plan(self):
        assert should_plan("Create report.py and then run report.py") is True

    def test_complex_build_does_plan(self):
        assert should_plan(
            "Build a REST API application with authentication and also add tests"
        ) is True


class TestPlanParsingAndValidation:
    def test_no_plan_marker(self):
        assert parse_steps("NO_PLAN") == []

    def test_nonsense_first_step_is_not_automatically_kept(self):
        steps = filter_tool_steps([
            "Think carefully about the request",
            "Create app.py with the requested behavior",
            "Run: python app.py",
        ])
        assert steps == [
            "Create app.py with the requested behavior",
            "Run: python app.py",
        ]

    def test_rejects_invented_filename(self):
        prompt = "Create fibonacci.py and then run fibonacci.py"
        steps = [
            "Create fib.py with the requested behavior",
            "Run: python fib.py",
        ]
        assert validate_plan(prompt, steps) == []

    def test_requires_every_user_filename(self):
        prompt = "Create app.py and tests.py, then run tests.py"
        steps = [
            "Create app.py with the implementation",
            "Run: python app.py",
        ]
        assert validate_plan(prompt, steps) == []

    def test_duplicate_steps_are_removed(self):
        prompt = "Create app.py and then run app.py"
        steps = [
            "Create app.py with the implementation",
            "Create app.py with the complete requested implementation",
            "Run: python app.py",
            "Run: python app.py",
        ]
        result = validate_plan(prompt, steps)
        assert result == [
            "Create app.py with the complete requested implementation",
            "Run: python app.py",
        ]

    def test_duplicate_run_is_preserved_when_twice_requested(self):
        prompt = "Create app.py and then run app.py twice"
        steps = [
            "Create app.py with the implementation",
            "Run: python app.py",
            "Run: python app.py",
        ]
        result = validate_plan(prompt, steps)
        assert result.count("Run: python app.py") == 2

    def test_plan_is_capped(self):
        prompt = "Create app.py and then run app.py and verify every requirement"
        steps = ["Create app.py with the implementation", "Run: python app.py"]
        steps.extend(f"Verify requirement {index}" for index in range(20))

        result = validate_plan(prompt, steps)
        assert len(result) == MAX_PLAN_STEPS

    def test_non_software_prompt_rejects_even_plausible_steps(self):
        steps = ["Create search.py", "Run: python search.py"]
        assert validate_plan("search person@example.com for accounts", steps) == []
