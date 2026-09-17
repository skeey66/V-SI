import { useEffect, useRef, useState } from "react";

/**
 * 게이트웨이가 보내는 메시지 그대로다(services/event_gateway). payload는
 * 이벤트 종류별로 다른 필드를 싣는다 — Task 16 브리프의 표를 참고.
 */
export type VsiEvent = {
  event_id: number;
  aggregate: string;
  aggregate_id: string;
  event_type: string;
  payload: Record<string, unknown>;
};

export type ResultKind = "PASS" | "FAIL" | "DONE" | "ERROR";

export type AgentResult = {
  kind: ResultKind;
  /** verdict가 아니면(task_failed) failure_class, 있으면 verdict 자체. */
  detail?: string;
};

export type ReworkPulse = {
  revision: number;
  /** 이 회차를 촉발한 검증 에이전트. 못 찾으면 undefined(추측하지 않는다). */
  from?: string;
};

export type StreamState = {
  events: VsiEvent[];
  /** at-least-once 재전달 방어용 — event_id로만 중복을 가른다. */
  seenIds: Set<number>;
  activeAgents: Set<string>;
  lastResult: Partial<Record<string, AgentResult>>;
  revision: number;
  requirementState: string | null;
  reworkPulse: ReworkPulse | null;
  escalationReason: string | null;
  /** revision_started가 참조할, 가장 최근에 FAIL을 낸 검증 에이전트. */
  lastFailedVerifier?: string;
};

const MAX_EVENTS = 200;

export function initialStreamState(): StreamState {
  return {
    events: [],
    seenIds: new Set<number>(),
    activeAgents: new Set<string>(),
    lastResult: {},
    revision: 1,
    requirementState: null,
    reworkPulse: null,
    escalationReason: null,
    lastFailedVerifier: undefined,
  };
}

export function reduceEvents(state: StreamState, e: VsiEvent): StreamState {
  // 게이트웨이는 at-least-once로 배달한다(전달 성공 후에 발행 표시를 하므로,
  // 그 사이 게이트웨이가 죽으면 재기동 후 같은 이벤트가 다시 온다). event_id는
  // 아웃박스 시퀀스라 전역적으로 유일하다 — 그것만으로 중복을 가른다.
  if (state.seenIds.has(e.event_id)) {
    return state;
  }

  const seenIds = new Set(state.seenIds);
  seenIds.add(e.event_id);
  const activeAgents = new Set(state.activeAgents);
  const lastResult = { ...state.lastResult };
  let revision = state.revision;
  let requirementState = state.requirementState;
  let reworkPulse = state.reworkPulse;
  let escalationReason = state.escalationReason;
  let lastFailedVerifier = state.lastFailedVerifier;

  const agent = typeof e.payload.agent === "string" ? e.payload.agent : undefined;

  switch (e.event_type) {
    case "task_submitted": {
      if (agent) {
        activeAgents.add(agent);
        delete lastResult[agent];
      }
      break;
    }
    case "task_completed": {
      if (agent) {
        activeAgents.delete(agent);
        const verdict = e.payload.verdict as string | undefined;
        if (verdict === "PASS" || verdict === "FAIL") {
          lastResult[agent] = { kind: verdict, detail: verdict };
          if (verdict === "FAIL") lastFailedVerifier = agent;
        } else {
          lastResult[agent] = { kind: "DONE" };
        }
      }
      break;
    }
    case "task_failed": {
      if (agent) {
        activeAgents.delete(agent);
        const failureClass = e.payload.failure_class as string | undefined;
        lastResult[agent] = { kind: "ERROR", detail: failureClass };
      }
      break;
    }
    case "revision_started": {
      const rev = e.payload.revision as number | undefined;
      if (typeof rev === "number") {
        revision = rev;
        reworkPulse = { revision: rev, from: lastFailedVerifier };
      }
      break;
    }
    case "state_changed": {
      const to = e.payload.to as string | undefined;
      if (to) requirementState = to;
      if (to === "escalated") {
        escalationReason = (e.payload.reason as string | undefined) ?? null;
      }
      break;
    }
    default:
      break;
  }

  const events = [...state.events, e];
  if (events.length > MAX_EVENTS) events.splice(0, events.length - MAX_EVENTS);

  return {
    events,
    seenIds,
    activeAgents,
    lastResult,
    revision,
    requirementState,
    reworkPulse,
    escalationReason,
    lastFailedVerifier,
  };
}

export type ConnectionStatus = "connecting" | "open" | "closed";

export type EventStreamResult = StreamState & { status: ConnectionStatus };

/**
 * WebSocket에 붙어 이벤트를 리듀서에 흘려보낸다. 스냅샷/백필이 없으므로
 * (Task 15 보고서 참고) 이 훅이 아는 것은 "연결한 뒤로 본 것"뿐이다 —
 * 새로고침하면 activeAgents 등은 빈 상태에서 다시 시작한다. 그 사실을
 * 화면에서 숨기지 않고 `status`로 노출해 AgentGraph가 "이벤트 대기 중"
 * 안내를 보여줄 수 있게 한다.
 */
export function useEventStream(url: string): EventStreamResult {
  const [state, setState] = useState<StreamState>(initialStreamState);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => {
    let cancelled = false;
    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (cancelled) return;
      setStatus("connecting");
      ws = new WebSocket(url);
      ws.onopen = () => {
        if (!cancelled) setStatus("open");
      };
      ws.onmessage = (m) => {
        const parsed = JSON.parse(m.data) as VsiEvent;
        setState((s) => reduceEvents(s, parsed));
      };
      ws.onclose = () => {
        if (cancelled) return;
        setStatus("closed");
        // 게이트웨이는 재기동해도 유실 없이 이어서 보낸다(아웃박스 커서) —
        // 클라이언트도 계속 재연결을 시도해야 그 보장을 실제로 누린다.
        retryTimer = setTimeout(connect, 1500);
      };
      ws.onerror = () => {
        ws?.close();
      };
    };

    connect();
    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      ws?.close();
    };
  }, [url]);

  return { ...state, status };
}
