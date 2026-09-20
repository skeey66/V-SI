from orchestrator.engine import build_feedback


def test_first_revision_has_no_feedback() -> None:
    assert build_feedback([], revision=1) == []


def test_feedback_comes_from_previous_revision_verifier_artifacts() -> None:
    artifacts = [
        {"revision": 1, "agent": "qa", "verdict": "FAIL", "summary": "test_add 실패"},
        {"revision": 1, "agent": "security", "verdict": "PASS", "summary": ""},
    ]
    out = build_feedback(artifacts, revision=2)
    assert {f["agent"] for f in out} == {"qa", "security"}
    assert next(f for f in out if f["agent"] == "qa")["summary"] == "test_add 실패"


def test_only_the_immediately_previous_revision_is_used() -> None:
    artifacts = [
        {"revision": 1, "agent": "qa", "verdict": "FAIL", "summary": "오래된 지적"},
        {"revision": 2, "agent": "qa", "verdict": "FAIL", "summary": "최근 지적"},
    ]
    out = build_feedback(artifacts, revision=3)
    assert [f["summary"] for f in out] == ["최근 지적"]


def test_non_verifier_artifacts_are_ignored() -> None:
    artifacts = [
        {"revision": 1, "agent": "dev", "verdict": None, "summary": "코드 썼다"},
        {"revision": 1, "agent": "qa", "verdict": "FAIL", "summary": "실패"},
    ]
    out = build_feedback(artifacts, revision=2)
    assert [f["agent"] for f in out] == ["qa"]
