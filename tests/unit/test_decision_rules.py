from requirement_agent.application.decision_rules import next_action_for, review_required

LOW = {"quality_risk": "low", "change_risk": "low", "technical_impact_risk": "low"}
HIGH = {"quality_risk": "high", "change_risk": "medium", "technical_impact_risk": "low"}


def test_clean_independent_does_not_require_review() -> None:
    analysis = {"duplicate": False, "conflict": False}
    assert review_required(analysis, LOW) is False
    assert next_action_for(analysis, LOW) == "can_commit"


def test_duplicate_requires_review() -> None:
    analysis = {"duplicate": True, "conflict": False}
    assert review_required(analysis, LOW) is True
    assert next_action_for(analysis, LOW) == "manual_review"


def test_conflict_requires_review() -> None:
    analysis = {"duplicate": False, "conflict": True}
    assert next_action_for(analysis, LOW) == "manual_review"


def test_any_high_risk_requires_review() -> None:
    analysis = {"duplicate": False, "conflict": False}
    assert review_required(analysis, HIGH) is True
    assert next_action_for(analysis, HIGH) == "manual_review"
