import { useEffect, useMemo, useRef, useState } from "react";
import { OfficeScene } from "./OfficeScene";
import { initialOfficeState, reduceOffice, type AgentId, type OfficeState } from "./officeState";
import {
  KIND_BY_AGENT, TEAM_LABEL, fetchArtifactFiles, type ArtifactFiles,
} from "../artifacts";
import type { VsiEvent } from "../useEventStream";
import "./Office.css";

/** 3D 사무실.
 *
 * React 는 `<canvas>` 를 놓고 장면을 **소유**하기만 한다 — 렌더 루프는
 * `OfficeScene` 안의 rAF 이고 React 의 렌더 주기와 무관하다.
 *
 * 이벤트는 `officeState.ts` 의 순수 리듀서를 거쳐 상태가 된 뒤 `apply()` 로
 * 내려간다. 그래서 "무엇을 그릴지"는 전부 테스트되고, 장면은 받은 대로만 그린다.
 */
export function Office({
  events,
  requirementId,
}: {
  events: VsiEvent[];
  requirementId: string | null;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const sceneRef = useRef<OfficeScene | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 이벤트 목록 전체를 접어 현재 사무실 상태를 만든다.
  //
  // 매번 처음부터 접는 이유: `useEventStream` 이 이미 이벤트 배열을 상한
  // (MAX_EVENTS)까지만 들고 있고, 그 정도를 접는 비용은 무시할 만하다. 증분
  // 상태를 따로 들고 다니면 탭을 오갈 때 두 벌이 어긋난다.
  const state: OfficeState = useMemo(
    () => events.reduce(reduceOffice, initialOfficeState()),
    [events],
  );

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let scene: OfficeScene;
    try {
      scene = new OfficeScene(host);
    } catch (e) {
      // WebGL 이 없거나 컨텍스트를 못 얻는 환경이 있다. 흰 화면 대신 이유를 보인다.
      setError(String(e));
      return;
    }
    sceneRef.current = scene;
    scene.start();
    setReady(true);
    return () => {
      scene.dispose();
      sceneRef.current = null;
      setReady(false);
    };
  }, []);

  useEffect(() => {
    if (ready) sceneRef.current?.apply(state);
  }, [ready, state]);

  // ── 캐릭터를 클릭하면 그 팀의 산출물을 연다 ──
  //
  // 어느 회차를 볼 것인가: **가장 최근 것**이다. 화면이 지금 보여주는 것은
  // 진행 중인 실행이고, 사람이 궁금한 것은 "지금 이 팀이 무엇을 냈는가"다.
  // 회차 고르기는 `revision` 이 화면에 뜨는 지금도 할 수 있지만, 그건 다음
  // 단계의 일이다.
  const [picked, setPicked] = useState<AgentId | null>(null);
  const [artifact, setArtifact] = useState<ArtifactFiles | null>(null);
  const [artifactError, setArtifactError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const revision = Math.max(1, state.revision);

  useEffect(() => {
    if (!ready) return;
    const scene = sceneRef.current;
    if (!scene) return;
    scene.onPick = (agent) => setPicked(agent);
    return () => {
      if (scene) scene.onPick = null;
    };
  }, [ready]);

  useEffect(() => {
    if (!picked || !requirementId) return;
    let cancelled = false;
    setLoading(true);
    setArtifact(null);
    setArtifactError(null);
    void (async () => {
      const out = await fetchArtifactFiles(requirementId, KIND_BY_AGENT[picked], revision);
      if (cancelled) return;
      setLoading(false);
      if (out.ok) setArtifact(out.value);
      else setArtifactError(out.error);
    })();
    return () => {
      cancelled = true;
    };
  }, [picked, requirementId, revision]);

  return (
    <div className="vsi-office">
      <div className="vsi-office-canvas" ref={hostRef} />
      {error && (
        <div className="vsi-office-error">
          3D 화면을 열지 못했습니다. 그래프 탭은 그대로 쓸 수 있습니다.
          <div className="vsi-office-error-detail">{error}</div>
        </div>
      )}
      {picked && (
        <aside className="vsi-art">
          <div className="vsi-art-head">
            <b>{TEAM_LABEL[picked] ?? picked}</b>
            <span className="vsi-art-rev">{revision}회차</span>
            <button className="vsi-art-close" onClick={() => setPicked(null)} aria-label="닫기">
              ×
            </button>
          </div>
          {loading && <p className="vsi-art-msg">불러오는 중…</p>}
          {artifactError && <p className="vsi-art-msg">{artifactError}</p>}
          {artifact && (
            <div className="vsi-art-body">
              {artifact.summary && <p className="vsi-art-summary">{artifact.summary}</p>}
              {Object.keys(artifact.files).length === 0 ? (
                <p className="vsi-art-msg">이 팀은 파일을 쓰지 않습니다 — 검사만 합니다.</p>
              ) : (
                Object.entries(artifact.files).map(([name, content]) => (
                  <div key={name} className="vsi-art-file">
                    <div className="vsi-art-file-name">{name}</div>
                    <pre>{content}</pre>
                  </div>
                ))
              )}
            </div>
          )}
        </aside>
      )}

      {!error && (
        <div className="vsi-office-hint">
          캐릭터를 클릭하면 산출물 · 드래그로 둘러보기 · 휠로 줌
          <button
            className="vsi-office-reset"
            onClick={() => sceneRef.current?.resetCamera()}
          >
            시점 초기화
          </button>
        </div>
      )}
    </div>
  );
}
