"""OTel 계측 — 서비스 경계를 넘는 trace 연속성.

`a2a-sdk`의 `[telemetry]` extra는 `opentelemetry-api`/`-sdk`만 설치한다. HTTP
계측(서버 쪽 컨텍스트 추출, 클라이언트 쪽 컨텍스트 주입)은 포함하지 않으므로
trace context가 서비스 경계를 저절로 넘지 않는다. 이 모듈이 그 둘을 직접
붙인다:

- `instrument_app`: FastAPI 서버 측. 들어오는 `traceparent` 헤더를 추출해
  요청 처리 코루틴의 현재 span으로 세운다.
- `instrumented_client`: httpx 클라이언트 측. 나가는 요청에 현재 span의
  `traceparent`를 주입한다.

두 계측이 함께 있어야 A→B HTTP 호출 한 번이 같은 trace_id를 공유한다. 어느 한
쪽만 있으면(예: 서버만 계측) 그 경계에서 trace가 끊긴다.

호출 순서가 중요한 지점 둘:

1. `instrument_app(app)`은 A2A 라우트가 전부 붙은 **뒤에** 불러야 한다
   (`create_agent_app`이 SDK 라우트를 붙이고 나서 호출). 순서가 바뀌면 SDK가
   부착한 라우트는 계측되지 않는다.
2. `setup_tracing()`은 `instrument_app`/`instrumented_client`보다 **먼저**
   호출해야 한다. 계측 라이브러리는 계측 시점에 `trace.get_tracer_provider()`로
   전역 provider를 얻어 고정한다 — 나중에 `setup_tracing()`을 불러도 이미
   붙은 계측은 그 전의 (기본 no-op) provider를 계속 쓴다.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup_tracing(service_name: str, endpoint: str | None = None) -> None:
    """전역 TracerProvider를 세팅한다. `endpoint`가 없으면 익스포트 없이(테스트용) 진행한다.

    `endpoint`는 Jaeger all-in-one의 OTLP/HTTP 수신 주소다
    (`VSI_OTLP_ENDPOINT`, 예: `http://jaeger:4318/v1/traces`). 별도 Collector를
    두지 않는다 — Jaeger가 OTLP를 직접 받는다.
    """
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)


def instrument_app(app: FastAPI) -> None:
    """서버 측 컨텍스트 추출. 라우트를 모두 붙인 뒤 호출해야 한다."""
    FastAPIInstrumentor.instrument_app(app)


def instrumented_client(
    timeout_s: float, transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """클라이언트 측 컨텍스트 주입. `ClientConfig.httpx_client`/에이전트 push client로 쓴다.

    `transport`는 테스트 전용 확장이다(컨트롤러 재정 A) — 운영 코드는 기본값
    `None`을 써서 지금까지와 동일하게 실제 네트워크로 나간다. 테스트는
    `httpx.ASGITransport(app=app)`를 넘겨 프로세스 경계 없이 계측만 검증한다.
    `HTTPXClientInstrumentor().instrument()`는 클라이언트 생성 **전에** 호출해야
    이번에 만드는 인스턴스에도 계측이 걸린다.
    """
    HTTPXClientInstrumentor().instrument()
    return httpx.AsyncClient(timeout=timeout_s, transport=transport)
