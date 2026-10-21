/** 산출물 조회.
 *
 * 목록과 내용이 나뉘어 있다: `files` 는 산출물 하나당 200KB 까지 가므로
 * (`llm_agent.loop.FILE_SNAPSHOT_TOTAL_CAP_BYTES`), 목록에 실으면 첫 로드가
 * 무거워진다. 목록은 파일 **이름만** 주고 내용은 사람이 그 팀을 고를 때 받는다.
 *
 * `approve.ts` 와 같은 길(`/api` → Vite 프록시 → 오케스트레이터)을 쓴다.
 */
export type ArtifactRow = {
  kind: string;
  version: number;
  agent: string;
  verdict: string | null;
  summary: string;
  file_names: string[];
  truncated: boolean;
};

export type ArtifactFiles = {
  kind: string;
  version: number;
  agent: string;
  summary: string;
  files: Record<string, string>;
};

export type Fetched<T> = { ok: true; value: T } | { ok: false; error: string };

async function getJson<T>(url: string, fetchImpl: typeof fetch): Promise<Fetched<T>> {
  let resp: Response;
  try {
    resp = await fetchImpl(url);
  } catch (e) {
    return { ok: false, error: `연결하지 못했습니다 (${String(e)})` };
  }
  if (resp.status === 404) return { ok: false, error: "아직 없습니다." };
  if (!resp.ok) return { ok: false, error: `서버가 ${resp.status} 로 응답했습니다.` };
  try {
    return { ok: true, value: (await resp.json()) as T };
  } catch {
    return { ok: false, error: "서버 응답을 읽지 못했습니다." };
  }
}

export function listArtifacts(
  requirementId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<Fetched<ArtifactRow[]>> {
  return getJson(`/api/requirements/${encodeURIComponent(requirementId)}/artifacts`, fetchImpl);
}

export function fetchArtifactFiles(
  requirementId: string,
  kind: string,
  version: number,
  fetchImpl: typeof fetch = fetch,
): Promise<Fetched<ArtifactFiles>> {
  return getJson(
    `/api/requirements/${encodeURIComponent(requirementId)}/artifacts/` +
      `${encodeURIComponent(kind)}/${version}`,
    fetchImpl,
  );
}

/** 에이전트 → 그 팀이 내는 산출물 종류. `llm_agent.roles.ROLES` 의 artifact_kind 와 같다. */
export const KIND_BY_AGENT: Record<string, string> = {
  planner: "requirements",
  dev: "source_code",
  qa: "test_report",
  security: "security_report",
};

export const TEAM_LABEL: Record<string, string> = {
  planner: "기획팀",
  dev: "개발팀",
  qa: "QA팀",
  security: "보안팀",
};


// ─────────────────────────────────────────────────────────────────────────────
// 요구사항 목록·상세·제출.
//
// 이벤트 게이트웨이는 **생중계만** 한다(스냅샷 없음). 새로고침하면 방금 끝난
// 실행도 화면에서 사라진다 — 그래서 "무엇이 만들어졌는지" 를 보려면 이 조회
// 경로가 반드시 있어야 한다. 이벤트 스트림은 *지금 벌어지는 일*, 이쪽은
// *남아 있는 것* 을 담당한다.
// ─────────────────────────────────────────────────────────────────────────────

export type RequirementRow = {
  requirement_id: string;
  title: string;
  state: string;
  revision: number;
  max_revisions: number;
  created_at: string | null;
};

export type TaskRow = {
  agent: string;
  revision: number;
  state: string;
  verdict: string | null;
  failure_class: string | null;
};

export type RequirementDetail = RequirementRow & { tasks: TaskRow[] };

export function listRequirements(
  fetchImpl: typeof fetch = fetch,
): Promise<Fetched<RequirementRow[]>> {
  return getJson("/api/requirements?limit=50", fetchImpl);
}

export function getRequirement(
  requirementId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<Fetched<RequirementDetail>> {
  return getJson(`/api/requirements/${encodeURIComponent(requirementId)}`, fetchImpl);
}

/** 요구사항 ID 를 화면에서 만든다.
 *
 * 서버가 만들어 주지 않으므로(제출자가 정한다) 여기서 만든다. 사람이 읽을 수
 * 있고 시간순으로 정렬되며 충돌하지 않을 만큼만 되면 된다. */
export function newRequirementId(now: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  const stamp =
    `${now.getFullYear()}${p(now.getMonth() + 1)}${p(now.getDate())}` +
    `-${p(now.getHours())}${p(now.getMinutes())}${p(now.getSeconds())}`;
  return `REQ-${stamp}`;
}

export async function submitRequirement(
  title: string,
  fetchImpl: typeof fetch = fetch,
): Promise<Fetched<{ requirement_id: string }>> {
  const requirementId = newRequirementId();
  let resp: Response;
  try {
    resp = await fetchImpl("/api/requirements", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        requirement_id: requirementId,
        title,
        run_id: `run-${requirementId}`,
      }),
    });
  } catch (e) {
    return { ok: false, error: `연결하지 못했습니다 (${String(e)})` };
  }
  if (resp.status === 409) return { ok: false, error: "같은 요청이 이미 진행 중입니다." };
  if (!resp.ok) return { ok: false, error: `서버가 ${resp.status} 로 응답했습니다.` };
  return { ok: true, value: { requirement_id: requirementId } };
}
