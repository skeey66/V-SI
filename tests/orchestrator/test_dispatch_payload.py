import os

from sqlalchemy.ext.asyncio import async_sessionmaker

from orchestrator.a2a_client import TaskSnapshot
from orchestrator.db import make_engine
from orchestrator.engine import TASK_COMPLETED, WorkflowEngine, build_feedback
from orchestrator.models import Artifact, WorkflowRequirement, WorkflowTask
from orchestrator.policy import TimeoutConfig
from orchestrator.workflow import RequirementState

TEST_DB_URL = os.environ.get(
    "VSI_TEST_DATABASE_URL", "postgresql+asyncpg://vsi:vsi@localhost:55432/vsi"
)


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


class _RecordingAgentClient:
    """`submit()`에 실제로 들어온 페이로드를 붙잡아 두는 이중.

    `tests/orchestrator/test_engine_retry.py`의 `FakeAgentClient`와 같은 자리를
    쓴다 — 다만 여기서는 실패를 주입하지 않는다. 목적은 제출 재시도 정책이
    아니라 `dispatch_agent`가 조립한 페이로드 자체를 검사하는 것이다.
    """

    def __init__(self, a2a_id: str = "a2a-dispatch-payload") -> None:
        self._a2a_id = a2a_id
        self.payloads: list[dict] = []

    async def submit(self, payload: dict, key: str) -> str:
        self.payloads.append(payload)
        return self._a2a_id

    async def get_task(self, a2a_task_id: str) -> TaskSnapshot:
        """`dispatch_agent`의 디스패치 직후 안전망(`refresh_task`)이 부른다.

        아직 끝나지 않았다고만 답한다 — 이 테스트가 보는 것은 페이로드 조립이지
        완료 처리 경로가 아니다(`test_engine_retry.py`의 `FakeAgentClient`와 동일).
        """
        return TaskSnapshot(a2a_task_id=a2a_task_id, state="TASK_STATE_WORKING")


async def test_dispatch_agent_sends_real_verifier_feedback_from_previous_revision(
    session,
) -> None:
    """`build_feedback`이 옳아도 `dispatch_agent`가 실제 DB에서 그 입력을 올바르게
    조립하지 못하면 소용없다 — 리뷰 라운드 1: 잘못된 컬럼이나 뒤바뀐 dict 키는
    이 경로를 지나지 않으면 어떤 테스트도 못 잡는다. `Artifact.content`는
    `loop.py`가 검증자 결과로 실제로 만드는 모양 그대로 심는다.
    """
    requirement_id = "REQ-DISPATCH-PAYLOAD-1"
    session.add(
        WorkflowRequirement(
            requirement_id=requirement_id,
            title="장바구니",
            state=RequirementState.REMEDIATING.value,
            revision=2,
            run_id=f"run-{requirement_id}",
        )
    )
    qa_task = WorkflowTask(
        requirement_id=requirement_id,
        agent="qa",
        revision=1,
        idempotency_key=f"{requirement_id}:qa:1:1",
        state=TASK_COMPLETED,
        verdict="FAIL",
        attempt=1,
    )
    session.add(qa_task)
    await session.flush()
    session.add(
        Artifact(
            requirement_id=requirement_id,
            producer_task=qa_task.task_id,
            kind="test_report",
            version=1,
            content={
                "kind": "test_report",
                "agent": "qa",
                "exit_code": 1,
                "verdict": "FAIL",
                "summary": "test_add가 3을 기대했는데 5가 나왔다",
            },
            sha256="x" * 64,
        )
    )
    await session.commit()

    db = make_engine(TEST_DB_URL)
    maker = async_sessionmaker(db, expire_on_commit=False)
    client = _RecordingAgentClient()
    workflow = WorkflowEngine(maker, {"dev": client}, TimeoutConfig.from_env({}))
    try:
        await workflow.dispatch_agent(requirement_id, "dev")
    finally:
        await db.dispose()

    assert len(client.payloads) == 1
    payload = client.payloads[0]
    assert payload["requirement_id"] == requirement_id
    assert payload["title"] == "장바구니"
    assert payload["revision"] == 2
    feedback = payload["feedback"]
    assert len(feedback) == 1
    assert feedback[0]["agent"] == "qa"
    assert feedback[0]["verdict"] == "FAIL"
    assert feedback[0]["summary"] == "test_add가 3을 기대했는데 5가 나왔다"
