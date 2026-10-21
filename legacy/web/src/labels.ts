/** 화면에 쓰는 말. **여기 한 곳에서만 정한다.**
 *
 * 같은 상태를 헤더·드롭다운·결과 화면이 각자 번역하면 금세 어긋난다. 실제로
 * `escalated` 하나를 "도와주세요"·"사람 필요"·"escalated" 세 가지로 부르고
 * 있었다.
 */

/** 워크플로 상태 → 개발을 모르는 사람이 읽을 말. */
export const STATE_TEXT: Record<string, string> = {
  planned: "시작했습니다",
  implementing: "만드는 중",
  verifying: "확인 중",
  remediating: "다시 만드는 중",
  blocked: "확인해 주세요",
  accepted: "완료",
  escalated: "도와주세요",
};

/** 배지 색을 고르는 분류. */
export function stateTone(state: string): "good" | "warn" | "bad" | "busy" {
  if (state === "accepted") return "good";
  if (state === "blocked") return "warn";
  if (state === "escalated") return "bad";
  return "busy";
}

export const TEAM_TEXT: Record<string, string> = {
  planner: "기획팀",
  dev: "개발팀",
  qa: "QA팀",
  security: "보안팀",
};

/** 산출물 종류 → 사람 말. */
export const KIND_TEXT: Record<string, string> = {
  requirements: "무엇을 만들지 정리한 것",
  source_code: "만들어진 코드",
  test_report: "제대로 도는지 확인",
  security_report: "위험한 곳이 없는지 점검",
};

/** 네 단계. 화면 곳곳에서 같은 순서를 쓴다. */
export const STEPS = [
  { agent: "planner", label: "기획" },
  { agent: "dev", label: "개발" },
  { agent: "qa", label: "확인" },
  { agent: "security", label: "점검" },
] as const;

/** 지금 몇 번째 단계인가. 아직 시작 전이면 -1. */
export function stepIndex(state: string): number {
  switch (state) {
    case "planned":
      return 0;
    case "blocked":
      return 0; // 기획 직후에 멈춰 선 자리다.
    case "implementing":
    case "remediating":
      return 1;
    case "verifying":
      return 2;
    case "accepted":
      return STEPS.length;
    default:
      return -1;
  }
}

/** "5분 전" 처럼. 정확한 시각보다 이쪽이 읽기 쉽다. */
export function ago(iso: string | null, now: Date = new Date()): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now.getTime() - t) / 1000));
  if (s < 60) return "방금";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}분 전`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}시간 전`;
  return `${Math.round(h / 24)}일 전`;
}

/** 경과 시간을 "3분 12초" 로. 진행 중일 때 보여 준다. */
export function elapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}분 ${s % 60}초` : `${s}초`;
}

export type Block = { kind: "text" | "code"; body: string };

/** 에이전트가 쓴 설명을 글과 코드로 쪼갠다.
 *
 * 모델은 ```로 감싼 코드블록을 섞어 쓴다. 그대로 흘리면 화면에 백틱 세 개가
 * 그냥 글자로 보인다(실측: QA 보고서가 그렇게 나왔다). 마크다운 전부를
 * 처리할 이유는 없고 — 실제로 나오는 것은 코드블록뿐이다 — 그것만 가른다.
 */
export function splitBlocks(text: string): Block[] {
  const out: Block[] = [];
  const re = /```[a-zA-Z0-9_-]*\n?([\s\S]*?)```/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const before = text.slice(last, m.index).trim();
    if (before) out.push({ kind: "text", body: before });
    const code = m[1].replace(/\n+$/, "");
    if (code.trim()) out.push({ kind: "code", body: code });
    last = re.lastIndex;
  }
  const tail = text.slice(last).trim();
  if (tail) out.push({ kind: "text", body: tail });
  // 닫히지 않은 코드블록이 남으면 위 정규식이 통째로 글로 흘린다 — 그래도
  // 백틱은 지워 준다.
  return out.map((b) => (b.kind === "text" ? { ...b, body: b.body.replace(/```/g, "") } : b));
}
