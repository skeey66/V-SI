import { describe, expect, it } from "vitest";
import { approveRequirement } from "./approve";
import { reduceEvents, initialStreamState } from "./useEventStream";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("approveRequirement", () => {
  it("동일 출처 /api 경로로 POST 한다", async () => {
    let seenUrl = "";
    let seenMethod = "";
    const out = await approveRequirement("REQ-1", (async (url: string, init?: RequestInit) => {
      seenUrl = url;
      seenMethod = init?.method ?? "";
      return jsonResponse(200, { granted: "true", state: "implementing" });
    }) as unknown as typeof fetch);

    // `localhost:8000` 을 직접 부르면 CORS 에 막힌다 — Vite 프록시를 타야 한다.
    expect(seenUrl).toBe("/api/requirements/REQ-1/approve");
    expect(seenMethod).toBe("POST");
    expect(out).toEqual({ ok: true, granted: true, state: "implementing" });
  });

  it("요구사항 ID 를 URL 인코딩한다", async () => {
    let seenUrl = "";
    await approveRequirement("REQ/../etc", (async (url: string) => {
      seenUrl = url;
      return jsonResponse(200, { granted: "true", state: "implementing" });
    }) as unknown as typeof fetch);
    expect(seenUrl).toBe("/api/requirements/REQ%2F..%2Fetc/approve");
  });

  it("이미 풀려 있으면 오류가 아니라 granted=false 다", async () => {
    // 더블클릭·새로고침 뒤 재전송은 버튼의 일상이다. 서버가 409 가 아니라
    // 200 + granted:false 로 답하고, 화면도 그것을 오류로 취급하지 않는다.
    const out = await approveRequirement("REQ-1", (async () =>
      jsonResponse(200, { granted: "false", state: "implementing" })) as unknown as typeof fetch);
    expect(out).toEqual({ ok: true, granted: false, state: "implementing" });
  });

  it("없는 요구사항은 사람이 읽을 문장으로 바꾼다", async () => {
    const out = await approveRequirement("REQ-NOPE", (async () =>
      jsonResponse(404, { detail: "unknown requirement" })) as unknown as typeof fetch);
    expect(out.ok).toBe(false);
    if (!out.ok) expect(out.error).toContain("찾지 못했습니다");
  });

  it("연결 실패도 삼키지 않고 문장으로 돌려준다", async () => {
    const out = await approveRequirement("REQ-1", (async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch);
    expect(out.ok).toBe(false);
    if (!out.ok) expect(out.error).toContain("연결하지 못했습니다");
  });

  it("5xx 를 성공으로 오해하지 않는다", async () => {
    const out = await approveRequirement("REQ-1", (async () =>
      jsonResponse(500, {})) as unknown as typeof fetch);
    expect(out.ok).toBe(false);
  });
});

describe("blocked 상태가 스트림을 타고 온다", () => {
  it("state_changed 가 blocked 를 싣는다", () => {
    const s = reduceEvents(initialStreamState(), {
      event_id: 1,
      aggregate: "requirement",
      aggregate_id: "REQ-1",
      event_type: "state_changed",
      payload: { to: "blocked", signal: "approval_required" },
    });
    expect(s.requirementState).toBe("blocked");
  });

  it("승인 뒤 implementing 이 오면 blocked 가 걷힌다", () => {
    // 화면은 승인 성공을 보고 스스로 상태를 고치지 않는다 — 이 전이가
    // 도착해야 패널이 사라진다. 그래서 이 경로가 살아 있어야 한다.
    let s = reduceEvents(initialStreamState(), {
      event_id: 1,
      aggregate: "requirement",
      aggregate_id: "REQ-1",
      event_type: "state_changed",
      payload: { to: "blocked", signal: "approval_required" },
    });
    s = reduceEvents(s, {
      event_id: 2,
      aggregate: "requirement",
      aggregate_id: "REQ-1",
      event_type: "state_changed",
      payload: { to: "implementing", signal: "approval_granted" },
    });
    expect(s.requirementState).toBe("implementing");
  });
});
