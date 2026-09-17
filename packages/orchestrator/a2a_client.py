from __future__ import annotations

import httpx

from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.types import TaskPushNotificationConfig
from a2a.utils.constants import TransportProtocol


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

    async def submit(self, payload: dict, idempotency_key: str) -> str:
        """작업을 제출하고 a2a_task_id를 반환한다.

        Task 10 통합 시 구현한다. 에이전트가 실제로 떠 있어야 ClientFactory가
        만드는 전송 계층(HTTP+JSON REST)의 실제 호출부를 검증할 수 있으므로,
        단위 테스트만으로는 이 메서드의 정확성을 확인할 수 없다. 계약은
        "a2a_task_id 문자열을 반환한다"이고, 멱등성 키는 메시지 메타데이터로
        전달한다.
        """
        raise NotImplementedError("Task 10 통합 시 구현")
