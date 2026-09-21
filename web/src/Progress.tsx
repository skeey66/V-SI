import { useEffect, useState } from "react";
import { STATE_TEXT, STEPS, elapsed, stateTone, stepIndex } from "./labels";
import "./Progress.css";

/** 지금 어디까지 왔는지.
 *
 * 이게 없으면 요청을 넣고 **10분 넘게 아무 신호가 없다**(실측: 로컬 모델로
 * 요구사항 하나가 20분 걸렸다). 어느 탭에 있든 보이게 헤더 아래 둔다.
 */
export function Progress({
  state,
  revision,
  startedAt,
}: {
  state: string | null;
  revision: number;
  /** 진행 중일 때만 경과 시간을 센다. */
  startedAt: number | null;
}) {
  const [now, setNow] = useState(() => Date.now());
  const running = state !== null && state !== "accepted" && state !== "escalated";

  useEffect(() => {
    if (!running || startedAt === null) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running, startedAt]);

  if (!state) return null;
  const at = stepIndex(state);
  const tone = stateTone(state);

  return (
    <div className={`vsi-prog ${tone}`}>
      <ol className="vsi-prog-steps">
        {STEPS.map((s, i) => {
          const done = at > i;
          const here = at === i;
          return (
            <li
              key={s.agent}
              className={`vsi-prog-step ${done ? "done" : ""} ${here ? "here" : ""}`}
            >
              <span className="vsi-prog-dot">{done ? "✓" : i + 1}</span>
              <span className="vsi-prog-label">{s.label}</span>
            </li>
          );
        })}
      </ol>
      <div className="vsi-prog-now">
        <span className={`vsi-prog-badge ${tone}`}>{STATE_TEXT[state] ?? state}</span>
        {revision > 1 && <span className="vsi-prog-dim">{revision - 1}번 되돌려 고치는 중</span>}
        {running && startedAt !== null && (
          <span className="vsi-prog-dim">{elapsed((now - startedAt) / 1000)} 경과</span>
        )}
      </div>
    </div>
  );
}
