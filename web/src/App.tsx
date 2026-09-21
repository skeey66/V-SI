import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { AgentGraph } from "./AgentGraph";
import { Result } from "./Result";
import { approveRequirement } from "./approve";
import { listRequirements, submitRequirement, type RequirementRow } from "./artifacts";
import { Progress } from "./Progress";
import { STATE_TEXT, ago, stateTone } from "./labels";
import { useEventStream } from "./useEventStream";
import "./AgentGraph.css";

const GATEWAY_WS_URL = "ws://localhost:8100/ws";

/** 3D 사무실은 three.js(약 600KB)를 끌고 온다. 지연 로딩해서 **별도 청크로
 * 떨어뜨린다** — 그래프만 보는 사람이 그 용량을 받지 않는다. */
const Office = lazy(() => import("./office/Office").then((m) => ({ default: m.Office })));

type Tab = "office" | "result" | "graph";

export function App() {
  // **스트림은 여기 하나뿐이다.** 탭마다 연결하면 WebSocket 이 둘이 되고,
  // 게이트웨이는 생중계만 하므로(스냅샷 없음) 두 탭이 서로 다른 구간을 본다.
  // 탭을 오갈 때 연결이 끊겼다 붙으면 그 사이 이벤트도 통째로 사라진다.
  const stream = useEventStream(GATEWAY_WS_URL);
  const { events, revision, requirementState, status } = stream;

  const [tab, setTab] = useState<Tab>("office");

  // 스트림에서 본 가장 최근 요구사항. **생중계로만 알 수 있는 값이다** —
  // 새로고침하면 사라지므로, 아래 `picked` 가 그 자리를 메운다.
  const streamedId = (() => {
    for (let i = events.length - 1; i >= 0; i -= 1) {
      if (events[i].aggregate === "requirement") return events[i].aggregate_id;
    }
    return null;
  })();

  // ── 요청 입력 + 지난 실행 고르기 ──
  //
  // 게이트웨이는 생중계만 한다(스냅샷 없음). 그래서 "무엇이 만들어졌는지" 를
  // 보려면 목록을 따로 읽어야 한다 — 이것이 없으면 새로고침 한 번에 방금
  // 끝난 실행조차 확인할 수 없다.
  const [runs, setRuns] = useState<RequirementRow[]>([]);
  const [picked, setPicked] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  /** 이번 실행이 시작된 시각. 경과 시간을 세는 데 쓴다 — 생중계로만 알 수 있다. */
  const [startedAt, setStartedAt] = useState<number | null>(null);

  const refreshRuns = useCallback(async () => {
    const out = await listRequirements();
    if (out.ok) setRuns(out.value);
  }, []);

  useEffect(() => {
    void refreshRuns();
  }, [refreshRuns]);

  // 실행이 끝나거나 막히면 목록을 다시 읽어 상태 표시를 맞춘다.
  useEffect(() => {
    if (requirementState) void refreshRuns();
  }, [requirementState, refreshRuns]);

  // 생중계 중인 실행이 있으면 그쪽을 본다 — 사람이 직접 고른 게 아니라면.
  const latestRequirementId = picked ?? streamedId;

  async function onSubmit() {
    const title = draft.trim();
    if (!title || submitting) return;
    setSubmitting(true);
    setAskError(null);
    const out = await submitRequirement(title);
    setSubmitting(false);
    if (!out.ok) {
      setAskError(out.error);
      return;
    }
    setDraft("");
    setAsking(false);
    setPicked(null); // 새로 넣은 것은 생중계로 따라간다.
    setStartedAt(Date.now());
    setTab("office"); // 요청하자마자 일하는 모습을 보여 준다.
    void refreshRuns();
  }

  // 기획 게이트 승인. `blocked` 는 리컨실러가 건드리지 않는 상태라(사람만
  // 푼다) 이 버튼이 유일한 출구다.
  //
  // 승인 뒤에 `requirementState` 를 여기서 고치지 않는다 — 오케스트레이터가
  // `blocked -> implementing` 을 기록하면 그 `state_changed` 가 스트림으로
  // 돌아와 배너가 저절로 사라진다. 화면이 서버를 앞질러 가면 승인이 실제로는
  // 반영되지 않은 경우에 화면만 진행된 것처럼 보인다.
  const [approving, setApproving] = useState(false);
  const [approveError, setApproveError] = useState<string | null>(null);
  const isBlocked = requirementState === "blocked";

  useEffect(() => {
    if (!isBlocked) {
      setApproveError(null);
      setApproving(false);
    }
  }, [isBlocked]);

  async function onApprove() {
    if (!latestRequirementId || approving) return;
    setApproving(true);
    setApproveError(null);
    const out = await approveRequirement(latestRequirementId);
    if (!out.ok) {
      setApproveError(out.error);
      setApproving(false);
      return;
    }
    if (!out.granted) {
      // 이미 풀려 있었다(더블클릭·새로고침 뒤 재전송). 오류가 아니다.
      setApproving(false);
    }
    // granted 면 버튼을 비활성인 채로 둔다 — 곧 도착할 state_changed 가
    // 배너를 통째로 걷어 간다.
  }

  return (
    <div className="vsi-root">
      <header className="vsi-header">
        <div className="vsi-title">
          V-SI
          <span className="vsi-dim">{latestRequirementId ? `· ${latestRequirementId}` : ""}</span>
        </div>

        <div className="vsi-tabs" role="tablist">
          <button
            role="tab"
            aria-selected={tab === "office"}
            className={`vsi-tab ${tab === "office" ? "on" : ""}`}
            onClick={() => setTab("office")}
          >
            사무실
          </button>
          <button
            role="tab"
            aria-selected={tab === "result"}
            className={`vsi-tab ${tab === "result" ? "on" : ""}`}
            onClick={() => setTab("result")}
          >
            결과물
          </button>
          <button
            role="tab"
            aria-selected={tab === "graph"}
            className={`vsi-tab ${tab === "graph" ? "on" : ""}`}
            onClick={() => setTab("graph")}
          >
            그래프
          </button>
        </div>

        <div className="vsi-status-row">
          <span>
            <span className={`vsi-status-dot ${status}`} />
            {status === "open" ? "연결됨" : status === "connecting" ? "연결 중…" : "다시 연결 중…"}
          </span>
          {requirementState && (
            <span className={`vsi-state-pill ${stateTone(requirementState)}`}>
              {STATE_TEXT[requirementState] ?? requirementState}
            </span>
          )}
        </div>
      </header>

      {isBlocked && latestRequirementId && (
        <div className="vsi-gate">
          <div className="vsi-gate-main">
            <div className="vsi-gate-title">확인해 주세요</div>
            <p className="vsi-gate-body">
              기획이 적은 확인 항목 중에 <b>아무것도 검사하지 않는 것</b>이 있습니다. 이대로
              개발을 시작하면 무엇을 만들어도 통과해 버립니다.
            </p>
            {approveError && <p className="vsi-gate-error">{approveError}</p>}
          </div>
          <div className="vsi-gate-actions">
            <button className="vsi-gate-btn" onClick={onApprove} disabled={approving}>
              {approving ? "보내는 중…" : "그대로 진행"}
            </button>
            <span className="vsi-gate-hint">개발부터 이어집니다 — 처음부터 다시 하지 않습니다.</span>
          </div>
        </div>
      )}

      {asking ? (
        <div className="vsi-ask">
          <textarea
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="무엇이 필요한지 평소 말로 적어 주세요. 예: 장바구니 금액을 합치고 5만원 이상이면 배송비를 빼 주세요"
          />
          <div className="vsi-ask-side">
            <button onClick={onSubmit} disabled={submitting || !draft.trim()}>
              {submitting ? "보내는 중…" : "만들어 주세요"}
            </button>
            <button className="vsi-ask-cancel" onClick={() => setAsking(false)}>
              취소
            </button>
          </div>
        </div>
      ) : (
        <div className="vsi-bar">
          <button className="vsi-bar-new" onClick={() => setAsking(true)}>
            + 새 요청
          </button>
          <select
            className="vsi-bar-pick"
            value={picked ?? ""}
            onChange={(e) => setPicked(e.target.value || null)}
          >
            <option value="">
              {streamedId ? `지금 보는 중 · ${streamedId}` : "지난 실행 고르기"}
            </option>
            {runs.map((r) => (
              <option key={r.requirement_id} value={r.requirement_id}>
                [{STATE_TEXT[r.state] ?? r.state}] {r.title.slice(0, 40)}
                {r.title.length > 40 ? "…" : ""} · {ago(r.created_at)}
              </option>
            ))}
          </select>
        </div>
      )}
      {askError && <p className="vsi-ask-error">{askError}</p>}

      <Progress state={requirementState} revision={revision} startedAt={startedAt} />

      {tab === "office" ? (
        <Suspense fallback={<div className="vsi-office-loading">사무실을 여는 중…</div>}>
          <Office events={events} requirementId={latestRequirementId} />
        </Suspense>
      ) : tab === "result" ? (
        <Result requirementId={latestRequirementId} />
      ) : (
        <AgentGraph stream={stream} />
      )}
    </div>
  );
}
