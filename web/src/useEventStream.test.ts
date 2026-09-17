import { describe, expect, it } from "vitest";
import { reduceEvents, initialStreamState } from "./useEventStream";
import type { VsiEvent } from "./useEventStream";

describe("reduceEvents", () => {
  it("task_submitted가 에이전트를 활성으로 만든다", () => {
    const s = reduceEvents(initialStreamState(), {
      event_id: 1, aggregate: "task", aggregate_id: "t1",
      event_type: "task_submitted", payload: { agent: "dev" },
    });
    expect(s.activeAgents.has("dev")).toBe(true);
  });

  it("task_completed가 에이전트를 비활성으로 만든다", () => {
    let s = reduceEvents(initialStreamState(), {
      event_id: 1, aggregate: "task", aggregate_id: "t1",
      event_type: "task_submitted", payload: { agent: "qa" },
    });
    s = reduceEvents(s, {
      event_id: 2, aggregate: "task", aggregate_id: "t1",
      event_type: "task_completed", payload: { agent: "qa", verdict: "FAIL" },
    });
    expect(s.activeAgents.has("qa")).toBe(false);
    expect(s.events).toHaveLength(2);
  });

  it("task_failed가 에이전트를 비활성으로 만들고 실패 원인을 기록한다", () => {
    let s = reduceEvents(initialStreamState(), {
      event_id: 1, aggregate: "task", aggregate_id: "t1",
      event_type: "task_submitted", payload: { agent: "dev" },
    });
    s = reduceEvents(s, {
      event_id: 2, aggregate: "task", aggregate_id: "t1",
      event_type: "task_failed",
      payload: { agent: "dev", failure_class: "timeout", attempts: 3 },
    });
    expect(s.activeAgents.has("dev")).toBe(false);
    expect(s.lastResult.dev).toEqual({ kind: "ERROR", detail: "timeout" });
  });

  it("동일 event_id 재전달은 중복 반영되지 않는다 (at-least-once dedupe)", () => {
    const submitted: VsiEvent = {
      event_id: 7, aggregate: "task", aggregate_id: "t1",
      event_type: "task_submitted", payload: { agent: "dev" },
    };
    const completed: VsiEvent = {
      event_id: 8, aggregate: "task", aggregate_id: "t1",
      event_type: "task_completed", payload: { agent: "dev" },
    };
    let s = reduceEvents(initialStreamState(), submitted);
    s = reduceEvents(s, completed);
    // 게이트웨이가 재기동 후 같은 event_id로 task_submitted를 재전송한다.
    s = reduceEvents(s, submitted);

    expect(s.events).toHaveLength(2);
    // 재전송된 task_submitted가 이미 끝난 에이전트를 다시 활성화하면 안 된다.
    expect(s.activeAgents.has("dev")).toBe(false);
  });

  it("revision_started는 회차를 올리고, 직전 실패한 검증 에이전트를 환류 출처로 기록한다", () => {
    let s = reduceEvents(initialStreamState(), {
      event_id: 1, aggregate: "task", aggregate_id: "t1",
      event_type: "task_completed", payload: { agent: "qa", verdict: "FAIL" },
    });
    s = reduceEvents(s, {
      event_id: 2, aggregate: "requirement", aggregate_id: "r1",
      event_type: "revision_started", payload: { revision: 2 },
    });
    expect(s.revision).toBe(2);
    expect(s.reworkPulse).toEqual({ revision: 2, from: "qa" });
  });

  it("state_changed to=escalated는 이유를 기록한다", () => {
    const s = reduceEvents(initialStreamState(), {
      event_id: 1, aggregate: "requirement", aggregate_id: "r1",
      event_type: "state_changed",
      payload: { to: "escalated", signal: "limit_exceeded", reason: "max_revisions_exceeded" },
    });
    expect(s.requirementState).toBe("escalated");
    expect(s.escalationReason).toBe("max_revisions_exceeded");
  });
});
