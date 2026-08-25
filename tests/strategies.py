from hypothesis import strategies as st
from hypothesis.strategies import one_of, none, booleans, text, integers, lists, dictionaries

from app.models import (
    TestOutcome, TestPriority, TestType, RiskLevel, FailureType,
    TestCaseStatus, ApprovalStatus, HealStatus, TestResult, TestCase
)


def test_result_strategy():
    """Build TestResult with drawn TestOutcome and TestPriority values."""
    from app.models import TestResult, TestOutcome, TestPriority, FailureType
    from app.database import SessionLocal
    from app.services.ai_brain import AIBrainService

    outcome = st.sampled_from(list(TestOutcome))
    failure_type = one_of(none(), st.sampled_from(list(FailureType)))

    return st.builds(
        TestResult,
        id=text(min_size=8, max_size=8, alphabet="0123456789abcdef"),
        test_case_id=text(min_size=8, max_size=8, alphabet="0123456789abcdef"),
        outcome=outcome,
        failure_step=one_of(none(), text(max_size=20)),
        failure_message=one_of(none(), text(max_size=200)),
        failure_type=failure_type,
        screenshot_url=one_of(none(), text(max_size=200)),
        duration_ms=integers(min_value=100, max_value=60000),
        robot_id=one_of(none(), text(min_size=4, max_size=20)),
        heal_attempt_id=one_of(none(), text(min_size=8, max_size=8, alphabet="0123456789abcdef")),
    )


def test_case_strategy():
    """Build dicts with 0–20 steps, arbitrary string fields (empty, Unicode, XML special chars)."""
    step_strategy = dictionaries(
        keys=st.sampled_from(["action", "selector", "value", "assertion"]),
        values=text(max_size=500, alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_ -/:.<>\"'"),
        min_size=1,
        max_size=4
    )
    steps = lists(step_strategy, min_size=0, max_size=20)
    return st.builds(
        lambda s: {
            "id": "x",
            "title": "t",
            "steps": s
        },
        steps
    )


def step_strategy():
    """Build step dicts with arbitrary action/selector/value/assertion strings."""
    return dictionaries(
        keys=st.sampled_from(["action", "selector", "value", "assertion"]),
        values=text(max_size=100, alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_ -/:.<>\"'"),
        min_size=1,
        max_size=2
    )


def valid_json_tests_strategy():
    """Build JSON strings in the {"tests": [...]} shape, optionally wrapped in triple-backtick fences."""
    import json as json_module

    # Build step dict strategy
    step = dictionaries(
        keys=st.sampled_from(["action", "selector", "value", "assertion"]),
        values=text(max_size=100),
        min_size=1,
        max_size=4
    )

    # Build test_case dict strategy
    test_case = dictionaries(
        keys=st.sampled_from(["id", "title", "type", "priority", "steps", "expected_result", "risk_level", "risk_rationale"]),
        values=one_of(
            text(max_size=200),
            lists(step, max_size=5),
            st.sampled_from(["functional", "regression", "edge_case", "integration"]),
            st.sampled_from(["p0_critical", "p1_high", "p2_medium", "p3_low"]),
            st.sampled_from(["high", "medium", "low"])
        ),
        min_size=2,
        max_size=8
    )
    tests_list = lists(test_case, min_size=1, max_size=5)
    
    # Build the base dict structure
    def build_base(tests):
        return {"tests": tests}
    
    # Serialize to JSON string
    def serialize_to_json(data):
        return json_module.dumps(data)

    base_strategy = st.builds(build_base, tests_list)
    base_str_strategy = st.builds(serialize_to_json, base_strategy)

    # Optional triple-backtick fence
    return one_of(
        base_str_strategy,
        st.builds(lambda s: f"```json\n{s}\n```", base_str_strategy),
        st.builds(lambda s: f"```\n{s}\n```", base_str_strategy)
    )


def test_case_schema_strategy():
    """Build TestCaseSchema objects with drawn enum values."""
    from app.schemas.test_schemas import TestCaseSchema, TestStepSchema, AITestResponse
    from app.models import TestType, TestPriority, RiskLevel

    step = st.builds(
        TestStepSchema,
        action=text(max_size=200),
        selector=one_of(none(), text(max_size=200)),
        value=one_of(none(), text(max_size=200)),
        assertion=one_of(none(), text(max_size=200))
    )
    steps = lists(step, min_size=1, max_size=5)

    return st.builds(
        TestCaseSchema,
        id=text(min_size=4, max_size=20),
        title=text(max_size=200),
        type=st.sampled_from(list(TestType)),
        priority=st.sampled_from(list(TestPriority)),
        steps=steps,
        expected_result=one_of(none(), text(max_size=500)),
        risk_level=st.sampled_from(list(RiskLevel)),
        risk_rationale=one_of(none(), text(max_size=500))
    )