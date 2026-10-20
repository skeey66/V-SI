"""실행 중인 요구사항과 그 이벤트 기록.

그래프는 HTTP 요청과 분리된 백그라운드 태스크에서 돈다. 그래서 브라우저가 SSE
연결을 끊었다 다시 붙어도(`?after=<seq>`) 실행은 계속되고, 놓친 이벤트를 다시
받는다. 여러 화면이 같은 실행을 동시에 볼 수도 있다.

**한계:** 이벤트 기록은 이 프로세스 메모리에만 있다. API 를 재시작하면 기록은
사라진다 — 그래프 상태 자체는 체크포인터(Postgres 설정 시)에 남으므로 결과는
조회할 수 있지만, 진행 타임라인은 다시 볼 수 없다. 기존 경로(아웃박스 테이블 +
이벤트 게이트웨이)가 보장하던 것을 이 경로는 보장하지 않는다.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage
from langsmith import uuid7

TERMINAL_EVENTS = ("done", "error")


@dataclass
class Run:
    requirement_id: str
    title: str
    status: str = "planned"
    revision: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    events: list[dict] = field(default_factory=list)
    #: 이 요구사항의 LangSmith 루트 실행 ID (시작 1개 + 승인 재개마다 1개)
    run_ids: list[str] = field(default_factory=list)
    task: asyncio.Task | None = None
    started: float = field(default_factory=time.monotonic)
    _changed: asyncio.Condition = field(default_factory=asyncio.Condition)

    @property
    def finished(self) -> bool:
        return bool(self.events) and self.events[-1]["type"] in TERMINAL_EVENTS

    async def publish(self, type_: str, **fields: Any) -> None:
        event = {"seq": len(self.events) + 1, "type": type_, "ts": time.time(), **fields}
        self.events.append(event)
        self.updated_at = event["ts"]
        async with self._changed:
            self._changed.notify_all()

    async def subscribe(self, after: int = 0, heartbeat_s: float = 15.0) -> AsyncIterator[dict | None]:
        """`after` 다음 이벤트부터 내보낸다. `None` 은 하트비트(연결 유지용)."""
        cursor = after
        while True:
            while cursor < len(self.events):
                event = self.events[cursor]
                cursor += 1
                yield event
                if event["type"] in TERMINAL_EVENTS:
                    return
            async with self._changed:
                try:
                    await asyncio.wait_for(
                        self._changed.wait_for(lambda: cursor < len(self.events)), heartbeat_s
                    )
                except TimeoutError:
                    yield None

    def summary(self) -> dict:
        return {
            "requirement_id": self.requirement_id, "title": self.title, "status": self.status,
            "revision": self.revision, "created_at": self.created_at, "updated_at": self.updated_at,
        }


class RunRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}

    def add(self, run: Run) -> Run:
        self._runs[run.requirement_id] = run
        return run

    def get(self, requirement_id: str) -> Run | None:
        return self._runs.get(requirement_id)

    def all(self) -> list[Run]:
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)


def graph_config(run: Run, prompt_commit: str | None) -> dict:
    """LangSmith 에 남길 루트 실행 설정. `run_id` 를 미리 정해 피드백과 링크에 쓴다."""
    run_id = str(uuid7())
    run.run_ids.append(run_id)
    return {
        "configurable": {"thread_id": run.requirement_id},
        "run_id": run_id,
        "run_name": "vsi-graph",
        "tags": ["langgraph"],
        "metadata": {
            "requirement_id": run.requirement_id, "title": run.title,
            "impl": "langgraph", "prompt_commit": prompt_commit or "local",
        },
    }


async def drive(run: Run, graph, graph_input, config: dict) -> None:
    """그래프를 끝까지(또는 interrupt 까지) 돌리며 스트림을 이벤트로 옮긴다."""
    try:
        # subgraphs=True: 각 노드 안의 `create_agent` 는 하위 그래프다. 이걸 켜지 않으면
        # 하위 그래프의 LLM 토큰이 바깥 스트림으로 나오지 않는다 (실측: 0개).
        async for namespace, mode, chunk in graph.astream(
            graph_input, config, stream_mode=["updates", "messages", "custom"], subgraphs=True,
        ):
            if mode == "custom":
                await run.publish(chunk["type"], **{k: v for k, v in chunk.items() if k != "type"})
            elif mode == "messages":
                message, meta = chunk
                # 스트리밍 조각(AIMessageChunk 는 AIMessage 의 하위 타입)과 한 번에 온 응답 모두 중계한다.
                if isinstance(message, AIMessage) and isinstance(message.content, str) and message.content:
                    await run.publish("token", agent=meta.get("lc_agent_name") or meta.get("langgraph_node"),
                                      text=message.content)
            elif not namespace:  # 상태 변화는 최상위 그래프의 것만 본다
                await _publish_updates(run, chunk)

        snapshot = await graph.aget_state(config)
        values = snapshot.values
        run.status = values.get("status", run.status)
        run.revision = values.get("revision", run.revision)
        if snapshot.next:  # interrupt 로 멈췄다 — 승인을 기다린다
            run.status = "blocked"
            return
        await run.publish("done", status=run.status, revision=run.revision,
                          elapsed_s=round(time.monotonic() - run.started, 1))
    except Exception as exc:  # 그래프 오류는 화면에 보이게 남기고 끝낸다
        run.status = "error"
        await run.publish("error", message=f"{type(exc).__name__}: {exc}")


async def _publish_updates(run: Run, chunk: dict) -> None:
    for node, update in chunk.items():
        if node == "__interrupt__":
            value = update[0].value if update else {}
            run.status = "blocked"
            await run.publish("interrupt", detail=value.get("detail", ""))
            continue
        update = update or {}
        if "status" in update:
            run.status = update["status"]
        if "revision" in update:
            run.revision = update["revision"]
        if node == "gate" and "gate" in update:
            await run.publish("gate", **update["gate"])
        for verdict in (update.get("verdicts") or {}).values():
            await run.publish("verdict", revision=run.revision, **verdict)
        await run.publish("node_end", node=node, status=update.get("status"))
