import httpx
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent_runtime.telemetry import instrument_app, instrumented_client


async def test_trace_context_crosses_http_boundary():
    """서버(FastAPI)·클라이언트(httpx) 계측이 trace_id를 유지한 채 경계를 넘는지 검증한다.

    컨트롤러 재정 A: 브리프의 `client._transport = transport` 직접 대입은
    private httpx 속성이라 버전에 취약하다. 대신 `httpx.AsyncClient(transport=...)`로
    생성 시점에 전송을 주입하고, `HTTPXClientInstrumentor().instrument()`는
    (client 생성 전인) `instrumented_client` 내부에서 먼저 호출되게 한다.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict:
        return {"ok": True}

    instrument_app(app)
    transport = httpx.ASGITransport(app=app)
    async with instrumented_client(timeout_s=5, transport=transport) as client:
        with trace.get_tracer("test").start_as_current_span("caller"):
            await client.get("http://t/ping")

    spans = exporter.get_finished_spans()
    trace_ids = {s.context.trace_id for s in spans}
    assert len(spans) >= 2, "클라이언트/서버 span이 모두 필요하다"
    assert len(trace_ids) == 1, "서비스 경계를 넘어 trace_id가 유지되어야 한다"
