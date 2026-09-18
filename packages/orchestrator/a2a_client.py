from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from google.protobuf import struct_pb2

from a2a.client.client import Client, ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.helpers import get_data_parts, new_data_part
from a2a.types import (
    GetTaskRequest,
    Message,
    Role,
    SendMessageRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
)
from a2a.utils.constants import TransportProtocol

#: A2A Task가 더 이상 움직이지 않는 상태들. 푸시/폴링 양쪽에서 같은 기준을 쓴다.
TERMINAL_TASK_STATES: frozenset[str] = frozenset(
    {
        TaskState.Name(TaskState.TASK_STATE_COMPLETED),
        TaskState.Name(TaskState.TASK_STATE_FAILED),
        TaskState.Name(TaskState.TASK_STATE_CANCELED),
        TaskState.Name(TaskState.TASK_STATE_REJECTED),
    }
)

#: 멱등성 키를 싣는 A2A 메시지 메타데이터 키.
IDEMPOTENCY_METADATA_KEY = "vsi_idempotency_key"


@dataclass(frozen=True)
class TaskSnapshot:
    """에이전트가 보고하는 Task의 현재 모습(오케스트레이터 어휘로 정규화).

    푸시 콜백과 리컨실리에이션(Task 13)이 같은 타입을 보게 해서, 완료 처리 경로가
    "어떻게 알게 됐는가"에 따라 갈라지지 않도록 한다.
    """

    a2a_task_id: str
    state: str
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_TASK_STATES


def _payload_from_task(task: Task) -> dict[str, Any]:
    """Task의 아티팩트 데이터 파트를 하나의 payload dict로 합친다.

    스텁 에이전트는 아티팩트를 정확히 하나 만들지만, 여러 개가 와도 뒤의 것이
    앞의 것을 덮도록 정의해 두면 호출부가 분기하지 않아도 된다.
    """
    merged: dict[str, Any] = {}
    for artifact in task.artifacts:
        for data in get_data_parts(artifact.parts):
            if isinstance(data, dict):
                merged.update(data)
    return merged


class AgentClient:
    """에이전트 1기에 대한 A2A 클라이언트. 계측된 httpx를 주입받는다.

    전송은 HTTP+JSON만 사용한다 (gRPC·SSE 금지). 스트리밍은 쓰지 않고
    폴링을 선호하며, 완료 알림은 푸시 콜백(push_receiver)으로 받는다.
    """

    def __init__(
        self,
        base_url: str,
        httpx_client: httpx.AsyncClient,
        push_url: str,
        push_token: str,
    ) -> None:
        self.base_url = base_url
        self._config = ClientConfig(
            httpx_client=httpx_client,
            streaming=False,
            polling=True,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
            push_notification_config=TaskPushNotificationConfig(
                url=push_url, token=push_token
            ),
        )
        self._factory = ClientFactory(self._config)
        self._client: Client | None = None
        self._lock = asyncio.Lock()

    async def _ensure_client(self) -> Client:
        """AgentCard를 한 번만 받아오고 전송 계층을 재사용한다.

        카드 해석은 네트워크 왕복이므로 디스패치마다 반복하지 않는다. 락은 동시
        디스패치(qa·security)가 카드를 두 번 받아오는 것을 막는다.
        """
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                self._client = await self._factory.create_from_url(self.base_url)
        return self._client

    async def submit(self, payload: dict, idempotency_key: str) -> str:
        """작업을 제출하고 a2a_task_id를 반환한다.

        멱등성 키는 메시지 메타데이터로 전달한다. A2A는 새 Task에 대해 클라이언트가
        task_id를 정하는 것을 허용하지 않으므로(서버가 `TaskNotFoundError`를 던진다 —
        `DefaultRequestHandler._setup_message_execution` 참고), 상관 키는 우리가 발급한
        멱등성 키이고 a2a_task_id는 응답에서 받아 적는다.

        `polling=True`가 `configuration.return_immediately`를 세우므로 이 호출은
        에이전트 실행 완료를 기다리지 않고 Task가 생성되는 즉시 반환한다.
        """
        client = await self._ensure_client()
        metadata = struct_pb2.Struct()
        metadata.update({IDEMPOTENCY_METADATA_KEY: idempotency_key})
        message = Message(
            message_id=str(uuid.uuid4()),
            role=Role.ROLE_USER,
            parts=[new_data_part(payload)],
            metadata=metadata,
        )
        request = SendMessageRequest(message=message)
        async for response in client.send_message(request):
            if not response.HasField("task"):
                raise RuntimeError(
                    f"{self.base_url}: Task 대신 Message가 돌아왔다 — "
                    "스텁 에이전트는 항상 Task를 만들어야 한다"
                )
            return response.task.id
        raise RuntimeError(f"{self.base_url}: send_message가 아무 응답도 내지 않았다")

    async def get_task(self, a2a_task_id: str) -> TaskSnapshot:
        """에이전트가 보관 중인 Task를 읽어 정규화한 스냅샷으로 돌려준다.

        푸시는 최선 노력 전달이다 — 이 조회가 권위 있는 읽기이고, 디스패치 직후
        경합 구간과 Task 13의 리컨실리에이션이 모두 여기에 기댄다.
        """
        client = await self._ensure_client()
        task = await client.get_task(GetTaskRequest(id=a2a_task_id))
        return TaskSnapshot(
            a2a_task_id=task.id,
            state=TaskState.Name(task.status.state),
            payload=_payload_from_task(task),
        )
