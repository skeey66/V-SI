import { useEffect, useState } from "react";
import {
  fetchArtifactFiles,
  getRequirement,
  listArtifacts,
  type ArtifactRow,
  type RequirementDetail,
} from "./artifacts";
import { KIND_TEXT, STATE_TEXT, STEPS, TEAM_TEXT, splitBlocks, stateTone } from "./labels";
import { downloadZip } from "./zip";
import "./Result.css";

/** 에이전트가 쓴 설명. 코드블록은 코드로 떼어 낸다 — 안 그러면 백틱 세 개가
 * 화면에 글자로 보인다. */
function Explain({ text }: { text: string }) {
  return (
    <>
      {splitBlocks(text).map((b, i) =>
        b.kind === "code" ? (
          <pre key={i} className="vsi-res-inline-code">{b.body}</pre>
        ) : (
          <p key={i} className="vsi-res-summary">{b.body}</p>
        ),
      )}
    </>
  );
}

/** 한 실행의 **결과물**. 사무실 탭이 "지금 벌어지는 일"이라면 여기는
 * "무엇이 남았는가"다.
 *
 * 이벤트 스트림으로는 이 화면을 만들 수 없다 — 게이트웨이는 생중계만 하므로
 * (연결 시점 이후, 스냅샷 없음) 새로고침하면 방금 끝난 실행도 사라진다.
 * 그래서 전부 조회 API 로 읽는다.
 */

export function Result({ requirementId }: { requirementId: string | null }) {
  const [detail, setDetail] = useState<RequirementDetail | null>(null);
  const [rows, setRows] = useState<ArtifactRow[] | null>(null);
  const [files, setFiles] = useState<Record<string, Record<string, string>>>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!requirementId) {
      setDetail(null);
      setRows(null);
      setFiles({});
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setFiles({});
    void (async () => {
      const [d, a] = await Promise.all([
        getRequirement(requirementId),
        listArtifacts(requirementId),
      ]);
      if (cancelled) return;
      setLoading(false);
      if (!d.ok) {
        setError(d.error);
        return;
      }
      setDetail(d.value);
      if (a.ok) setRows(a.value);
      else setError(a.error);
    })();
    return () => {
      cancelled = true;
    };
  }, [requirementId]);

  // 파일이 있는 산출물의 내용을 받아 둔다. 목록에는 이름만 오므로
  // (200KB 짜리를 첫 로드에 다 싣지 않으려고) 내용은 따로 받는다.
  useEffect(() => {
    if (!requirementId || !rows) return;
    let cancelled = false;
    void (async () => {
      for (const r of rows) {
        if (r.file_names.length === 0) continue;
        const key = `${r.kind}@${r.version}`;
        const out = await fetchArtifactFiles(requirementId, r.kind, r.version);
        if (cancelled) return;
        if (out.ok) setFiles((prev) => ({ ...prev, [key]: out.value.files }));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [requirementId, rows]);

  if (!requirementId) {
    return (
      <div className="vsi-result vsi-result-empty">
        위에서 요청을 하나 넣거나, 지난 실행을 골라 주세요.
      </div>
    );
  }
  if (loading) return <div className="vsi-result vsi-result-empty">불러오는 중…</div>;
  if (error) return <div className="vsi-result vsi-result-empty">{error}</div>;
  if (!detail) return null;

  // 마지막 회차가 실제 결과물이다. 이전 회차는 고치는 과정이다.
  const lastRev = detail.revision;
  const current = (rows ?? []).filter((r) => r.version === lastRev);
  // 단계 순서(기획 → 개발 → 확인 → 점검)로 세운다. DB 정렬은 kind 알파벳순이라
  // 보안이 QA 보다 위로 올라온다 — 사람이 읽는 순서와 다르다.
  const order = STEPS.map((s) => s.agent) as readonly string[];
  const byStep = (a: ArtifactRow, b: ArtifactRow) =>
    order.indexOf(a.agent) - order.indexOf(b.agent);
  const verifiers = current.filter((r) => r.verdict !== null).sort(byStep);
  const producers = current.filter((r) => r.file_names.length > 0).sort(byStep);
  const reworks = Math.max(0, lastRev - 1);

  // 내려받기는 **화면에 이미 온 것**으로 만든다 — 백엔드를 더 만들 이유가 없다.
  // 아직 다 안 받았으면 버튼을 내놓지 않는다(반쪽짜리 zip 을 주지 않는다).
  const wanted = producers.filter((r) => r.file_names.length > 0);
  const gathered = wanted.every((r) => files[`${r.kind}@${r.version}`]);
  const allFiles: Record<string, string> | null =
    wanted.length > 0 && gathered
      ? Object.assign({}, ...wanted.map((r) => files[`${r.kind}@${r.version}`]))
      : null;

  return (
    <div className="vsi-result">
      <section className="vsi-res-card">
        <h3 className="vsi-res-h">요청한 것</h3>
        <p className="vsi-res-title">{detail.title}</p>
        <div className="vsi-res-meta">
          <span className={`vsi-res-state ${stateTone(detail.state)}`}>
            {STATE_TEXT[detail.state] ?? detail.state}
          </span>
          <span>
            {reworks === 0 ? "한 번에 끝남" : `${reworks}번 되돌려 고침`} · 전체 {lastRev}회차
          </span>
          <span className="vsi-res-id">{detail.requirement_id}</span>
        </div>
      </section>

      <section className="vsi-res-card">
        <div className="vsi-res-headrow">
          <h3 className="vsi-res-h">만들어진 파일</h3>
          {allFiles !== null && (
            <button
              className="vsi-res-dl"
              onClick={() => downloadZip(`${detail.requirement_id}.zip`, allFiles)}
            >
              전부 내려받기 (.zip)
            </button>
          )}
        </div>
        {producers.length === 0 ? (
          <p className="vsi-res-dim">아직 파일이 없습니다.</p>
        ) : (
          producers.map((r) => {
            const bag = files[`${r.kind}@${r.version}`];
            return (
              <div key={r.kind} className="vsi-res-group">
                <div className="vsi-res-grouphead">
                  {KIND_TEXT[r.kind] ?? r.kind}
                  <span className="vsi-res-dim"> · {TEAM_TEXT[r.agent] ?? r.agent}</span>
                  {r.truncated && <span className="vsi-res-warn">일부 잘림</span>}
                </div>
                {r.file_names.map((name) => (
                  <div key={name} className="vsi-res-file">
                    <div className="vsi-res-filename">{name}</div>
                    <pre>{bag?.[name] ?? "불러오는 중…"}</pre>
                  </div>
                ))}
              </div>
            );
          })
        )}
      </section>

      <section className="vsi-res-card">
        <h3 className="vsi-res-h">검사 결과</h3>
        {verifiers.length === 0 ? (
          <p className="vsi-res-dim">아직 검사 전입니다.</p>
        ) : (
          verifiers.map((r) => (
            <div key={r.kind} className="vsi-res-check">
              <span className={`vsi-res-verdict ${r.verdict === "PASS" ? "pass" : "fail"}`}>
                {r.verdict === "PASS" ? "통과" : "실패"}
              </span>
              <div>
                <b>{KIND_TEXT[r.kind] ?? r.kind}</b>
                <span className="vsi-res-dim"> · {TEAM_TEXT[r.agent] ?? r.agent}</span>
                {r.summary && <Explain text={r.summary} />}
              </div>
            </div>
          ))
        )}
      </section>

      {reworks > 0 && (
        <section className="vsi-res-card">
          <h3 className="vsi-res-h">고친 과정</h3>
          <ol className="vsi-res-hist">
            {detail.tasks.map((t, i) => (
              <li key={i}>
                <span className="vsi-res-dim">{t.revision}회차</span>{" "}
                <b>{TEAM_TEXT[t.agent] ?? t.agent}</b>{" "}
                {t.verdict === "FAIL" && <span className="vsi-res-bad">되돌려 보냄</span>}
                {t.verdict === "PASS" && <span className="vsi-res-good">통과</span>}
                {t.failure_class && <span className="vsi-res-bad">멈춤 ({t.failure_class})</span>}
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}
