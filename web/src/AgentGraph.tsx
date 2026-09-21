import { useEffect, useRef, useState } from "react";
import type { StreamState, VsiEvent } from "./useEventStream";
import "./AgentGraph.css";

type NodeId = "orchestrator" | "planner" | "dev" | "qa" | "security";

const NODES: Record<NodeId, { x: number; y: number; label: string }> = {
  orchestrator: { x: 230, y: 38, label: "orchestrator" },
  planner: { x: 100, y: 150, label: "planner" },
  dev: { x: 230, y: 150, label: "dev" },
  qa: { x: 165, y: 258, label: "qa" },
  security: { x: 295, y: 258, label: "security" },
};

const AGENT_IDS: NodeId[] = ["planner", "dev", "qa", "security"];

const REWORK_PULSE_MS = 4200;

function labelForEvent(e: VsiEvent): { type: string; agent: string; detail: string; tone: string } {
  const agent = typeof e.payload.agent === "string" ? e.payload.agent : "";
  switch (e.event_type) {
    case "task_submitted":
      return { type: "submit", agent, detail: `rev ${String(e.payload.revision ?? "")}`, tone: "" };
    case "task_completed": {
      const verdict = e.payload.verdict as string | undefined;
      return {
        type: "complete",
        agent,
        detail: verdict ?? "done",
        tone: verdict === "FAIL" ? "fail" : verdict === "PASS" ? "pass" : "",
      };
    }
    case "task_failed":
      return {
        type: "failed",
        agent,
        detail: String(e.payload.failure_class ?? "error"),
        tone: "error",
      };
    case "revision_started":
      return { type: "revision", agent: "", detail: `rev ${String(e.payload.revision)}`, tone: "rework" };
    case "state_changed": {
      const to = e.payload.to as string | undefined;
      return { type: "state", agent: "", detail: to ?? "", tone: to === "escalated" ? "fail" : "" };
    }
    default:
      return { type: e.event_type, agent, detail: "", tone: "" };
  }
}

/** 노드 그래프 + 이벤트 로그.
 *
 * `useEventStream` 을 **직접 부르지 않는다** — 스트림은 `App` 이 하나만 열어
 * 두 탭에 내려준다. 탭마다 연결하면 WebSocket 이 둘이 되고, 게이트웨이는
 * 생중계만 하므로(스냅샷 없음) 두 탭이 서로 다른 구간을 보게 된다. */
export function AgentGraph({ stream }: { stream: StreamState }) {
  const {
    events,
    activeAgents,
    lastResult,
    activity,
    revision,
    reworkPulse,
    escalationReason,
    escalationAgent,
    escalationFailureClass,
  } = stream;

  // 환류 화살표는 순간의 사건이다 — revision_started가 찍힐 때만 잠깐 그린다.
  // (그 자체가 계속 반복되는 정보라 상시 표시하면 오히려 노이즈가 된다.)
  const [showPulse, setShowPulse] = useState(false);
  const lastPulseRevision = useRef<number | null>(null);
  useEffect(() => {
    if (!reworkPulse) return;
    if (lastPulseRevision.current === reworkPulse.revision) return;
    lastPulseRevision.current = reworkPulse.revision;
    setShowPulse(true);
    const t = setTimeout(() => setShowPulse(false), REWORK_PULSE_MS);
    return () => clearTimeout(t);
  }, [reworkPulse]);

  const hasSeenAnything = events.length > 0;

  return (
      <div className="vsi-layout">
        <div className="vsi-canvas-wrap">
          <svg viewBox="0 0 460 300" width="100%" role="img" aria-label="에이전트 상태 그래프">
            <defs>
              <marker id="reworkArrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
                <path d="M0,0 L6,3 L0,6 Z" fill="var(--c-rework)" />
              </marker>
            </defs>

            {AGENT_IDS.map((id) => {
              const active = activeAgents.has(id);
              const n = NODES[id];
              const o = NODES.orchestrator;
              return (
                <line
                  key={`spoke-${id}`}
                  x1={o.x}
                  y1={o.y}
                  x2={n.x}
                  y2={n.y}
                  stroke={active ? "var(--line-strong)" : "var(--line)"}
                  strokeWidth={active ? 2.5 : 1}
                />
              );
            })}

            {showPulse && reworkPulse?.from && NODES[reworkPulse.from as NodeId] && (
              <path
                d={reworkArcPath(NODES[reworkPulse.from as NodeId], NODES.dev)}
                fill="none"
                stroke="var(--c-rework)"
                strokeWidth={2.5}
                strokeDasharray="6 5"
                markerEnd="url(#reworkArrow)"
                className="vsi-rework-arc"
              />
            )}

            <AgentNode id="orchestrator" active={false} result={undefined} activity={undefined} />
            {AGENT_IDS.map((id) => (
              <AgentNode
                key={id}
                id={id}
                active={activeAgents.has(id)}
                result={lastResult[id]}
                activity={activeAgents.has(id) ? activity[id] : undefined}
              />
            ))}

            {revision > 1 && (
              <g transform={`translate(${NODES.dev.x + 26}, ${NODES.dev.y - 26})`}>
                <rect x={-16} y={-9} width={32} height={16} rx={8} fill="var(--c-rework)" />
                <text x={0} y={3} textAnchor="middle" className="vsi-badge-text">
                  rev{revision}
                </text>
              </g>
            )}
          </svg>

          {!hasSeenAnything && (
            <div className="vsi-empty">
              <div className="vsi-empty-title">이벤트 대기 중</div>
              <div className="vsi-empty-sub">
                게이트웨이는 연결 시점 이후의 이벤트만 보낸다(스냅샷 없음). 이미 진행 중인
                실행이 있어도 새로고침 직후에는 여기 아무것도 보이지 않는 것이 정상이다 —
                <code>POST /requirements</code>로 새 실행을 시작하면 노드가 움직인다.
              </div>
            </div>
          )}
        </div>

        <div className="vsi-panel">
          <p className="vsi-panel-title">event log</p>
          <ol className="vsi-log" reversed>
            {events
              .slice(-40)
              .slice()
              .reverse()
              .map((e) => {
                const l = labelForEvent(e);
                return (
                  <li key={e.event_id} className="vsi-log-row">
                    <span className="vsi-log-type">{l.type}</span>
                    <span className="vsi-log-agent">{l.agent}</span>
                    <span className={`vsi-log-detail ${l.tone}`}>{l.detail}</span>
                  </li>
                );
              })}
          </ol>
          {escalationReason && (
            <p className="vsi-empty-sub" style={{ marginTop: 10 }}>
              escalated: {escalationReason}
              {/* give_up(재시도 예산 소진)은 agent/failure_class를 함께 싣는다 —
                  remediate(회차 상한 초과)는 reason뿐이다. 있을 때만 붙여
                  두 원인을 구분한다(Task 12). */}
              {escalationAgent && <> · agent: {escalationAgent}</>}
              {escalationFailureClass && <> · failure_class: {escalationFailureClass}</>}
            </p>
          )}
        </div>
      </div>
  );
}

function reworkArcPath(from: { x: number; y: number }, to: { x: number; y: number }) {
  const midX = (from.x + to.x) / 2 - 60;
  const midY = (from.y + to.y) / 2;
  return `M ${from.x} ${from.y} Q ${midX} ${midY} ${to.x} ${to.y}`;
}

function AgentNode({
  id,
  active,
  result,
  activity,
}: {
  id: NodeId;
  active: boolean;
  result: { kind: string; detail?: string } | undefined;
  /** tool_result에서 온 현재 도구 이름 — 자문 정보라 상태에는 영향이 없다. */
  activity: string | undefined;
}) {
  const n = NODES[id];
  const ringColor =
    result?.kind === "PASS"
      ? "var(--c-pass)"
      : result?.kind === "FAIL"
        ? "var(--c-fail)"
        : result?.kind === "ERROR"
          ? "var(--c-error)"
          : "var(--line-strong)";
  const radius = id === "orchestrator" ? 22 : 20;

  return (
    <g className={active ? "vsi-node-active" : undefined}>
      {active && (
        <circle
          cx={n.x}
          cy={n.y}
          r={radius + 6}
          fill="none"
          stroke={`var(--c-${id})`}
          strokeWidth={1.5}
          opacity={0.45}
          className="vsi-node-glow"
        />
      )}
      <circle cx={n.x} cy={n.y} r={radius} fill="var(--panel-2)" stroke={ringColor} strokeWidth={2.5} />
      <circle cx={n.x} cy={n.y} r={radius - 6} fill={`var(--c-${id})`} className="vsi-node-core" />
      <text x={n.x} y={n.y + radius + 14} textAnchor="middle" className="vsi-node-label">
        {n.label}
      </text>
      {activity ? (
        <text x={n.x} y={n.y + radius + 25} textAnchor="middle" className="vsi-node-activity">
          {activity}
        </text>
      ) : (
        result?.detail && (
          <text x={n.x} y={n.y + radius + 25} textAnchor="middle" className="vsi-node-sub">
            {result.detail}
          </text>
        )
      )}
    </g>
  );
}
