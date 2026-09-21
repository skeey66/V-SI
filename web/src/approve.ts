/** 기획 게이트 승인 호출.
 *
 * 화면은 성공 여부만 알면 된다 — **상태는 이벤트 스트림이 알려준다.** 승인이
 * 받아들여지면 오케스트레이터가 `blocked -> implementing` 전이를 기록하고,
 * 그 `state_changed` 가 게이트웨이를 통해 돌아와 화면이 스스로 바뀐다. 여기서
 * 낙관적으로 로컬 상태를 고치지 않는 이유가 그것이다: 화면이 서버를 앞질러
 * 가면, 승인이 실제로는 반영되지 않은 경우에 화면만 진행된 것처럼 보인다.
 */
export type ApproveOutcome =
  | { ok: true; granted: boolean; state: string }
  | { ok: false; error: string };

export async function approveRequirement(
  requirementId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ApproveOutcome> {
  let resp: Response;
  try {
    resp = await fetchImpl(`/api/requirements/${encodeURIComponent(requirementId)}/approve`, {
      method: "POST",
    });
  } catch (e) {
    // 네트워크가 끊겼거나 개발 서버가 죽었다. 사람이 읽을 문장으로 바꾼다.
    return { ok: false, error: `연결하지 못했습니다 (${String(e)})` };
  }
  if (resp.status === 404) {
    return { ok: false, error: "그 요구사항을 찾지 못했습니다." };
  }
  if (!resp.ok) {
    return { ok: false, error: `서버가 ${resp.status} 로 응답했습니다.` };
  }
  let body: { granted?: string; state?: string };
  try {
    body = await resp.json();
  } catch {
    return { ok: false, error: "서버 응답을 읽지 못했습니다." };
  }
  return { ok: true, granted: body.granted === "true", state: body.state ?? "" };
}
