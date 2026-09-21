import { describe, expect, it } from "vitest";
import {
  KIND_BY_AGENT, fetchArtifactFiles, listArtifacts, listRequirements,
  newRequirementId, submitRequirement,
} from "./artifacts";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status, headers: { "content-type": "application/json" },
  });
}

describe("산출물 조회", () => {
  it("목록은 동일 출처 /api 를 탄다", async () => {
    let url = "";
    await listArtifacts("REQ-1", (async (u: string) => {
      url = u;
      return json(200, []);
    }) as unknown as typeof fetch);
    expect(url).toBe("/api/requirements/REQ-1/artifacts");
  });

  it("내용 조회는 kind 와 version 을 경로에 넣고 인코딩한다", async () => {
    let url = "";
    await fetchArtifactFiles("R/1", "source_code", 3, (async (u: string) => {
      url = u;
      return json(200, { files: {} });
    }) as unknown as typeof fetch);
    expect(url).toBe("/api/requirements/R%2F1/artifacts/source_code/3");
  });

  it("404 는 사람이 읽을 문장이 된다 — 빈 목록으로 뭉개지 않는다", async () => {
    const out = await listArtifacts("REQ-nope", (async () =>
      json(404, { detail: "unknown requirement" })) as unknown as typeof fetch);
    expect(out.ok).toBe(false);
    if (!out.ok) expect(out.error).toContain("아직 없습니다");
  });

  it("연결 실패를 삼키지 않는다", async () => {
    const out = await listArtifacts("REQ-1", (async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch);
    expect(out.ok).toBe(false);
  });

  it("역할 → 산출물 종류 표가 서버와 같다", () => {
    // `llm_agent.roles.ROLES` 의 artifact_kind 와 어긋나면 클릭해도 늘 404 다.
    expect(KIND_BY_AGENT).toEqual({
      planner: "requirements",
      dev: "source_code",
      qa: "test_report",
      security: "security_report",
    });
  });
});

describe("요구사항 제출과 목록", () => {
  it("제출은 화면에서 만든 ID 로 POST 한다", async () => {
    let body: any = null;
    let method = "";
    const out = await submitRequirement("장바구니 합계를 내줘", (async (
      _u: string,
      init?: RequestInit,
    ) => {
      method = init?.method ?? "";
      body = JSON.parse(String(init?.body));
      return json(202, { accepted: "true" });
    }) as unknown as typeof fetch);

    expect(method).toBe("POST");
    expect(body.title).toBe("장바구니 합계를 내줘");
    expect(body.requirement_id).toMatch(/^REQ-\d{8}-\d{6}$/);
    // run_id 는 requirement_id 를 따라간다 — 서버가 요구하는 필드다.
    expect(body.run_id).toBe(`run-${body.requirement_id}`);
    expect(out.ok).toBe(true);
  });

  it("409 는 '이미 진행 중'으로 옮긴다 — 서버 고장이 아니다", async () => {
    const out = await submitRequirement("x", (async () =>
      json(409, { detail: "requirement already exists" })) as unknown as typeof fetch);
    expect(out.ok).toBe(false);
    if (!out.ok) expect(out.error).toContain("이미 진행 중");
  });

  it("ID 는 시간순으로 정렬된다", () => {
    const a = newRequirementId(new Date(2026, 0, 2, 3, 4, 5));
    const b = newRequirementId(new Date(2026, 0, 2, 3, 4, 6));
    expect(a).toBe("REQ-20260102-030405");
    expect(a < b).toBe(true);
  });

  it("목록은 /api/requirements 를 탄다", async () => {
    let url = "";
    await listRequirements((async (u: string) => {
      url = u;
      return json(200, []);
    }) as unknown as typeof fetch);
    expect(url).toBe("/api/requirements?limit=50");
  });
});
