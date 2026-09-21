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

  it("state_changed to=escalated는 이유를 기록한다 (remediate 경로 — agent/failure_class 없음)", () => {
    const s = reduceEvents(initialStreamState(), {
      event_id: 1, aggregate: "requirement", aggregate_id: "r1",
      event_type: "state_changed",
      payload: { to: "escalated", signal: "limit_exceeded", reason: "max_revisions_exceeded" },
    });
    expect(s.requirementState).toBe("escalated");
    expect(s.escalationReason).toBe("max_revisions_exceeded");
    expect(s.escalationAgent).toBeNull();
    expect(s.escalationFailureClass).toBeNull();
  });

  it("state_changed to=escalated는 give_up 경로(agent/failure_class 포함)도 그대로 보존한다", () => {
    const s = reduceEvents(initialStreamState(), {
      event_id: 1, aggregate: "requirement", aggregate_id: "r1",
      event_type: "state_changed",
      payload: {
        to: "escalated",
        signal: "limit_exceeded",
        reason: "retry_budget_exhausted",
        agent: "dev",
        failure_class: "timeout",
      },
    });
    expect(s.requirementState).toBe("escalated");
    expect(s.escalationReason).toBe("retry_budget_exhausted");
    expect(s.escalationAgent).toBe("dev");
    expect(s.escalationFailureClass).toBe("timeout");
  });

  it("tool_result 를 받으면 에이전트의 현재 동작을 기록한다", () => {
    const s = reduceEvents(initialStreamState(), {
      event_id: 1, aggregate: "requirement", aggregate_id: "REQ-1",
      event_type: "tool_result",
      payload: { agent: "dev", tool: "write_file", revision: 1, ok: true, detail: "calc.py 에 썼다" },
    });
    expect(s.activity.dev).toContain("write_file");
  });

  it("도구 이벤트는 워크플로 상태를 바꾸지 않는다", () => {
    // 자문 이벤트다 — 상태를 유도하면 안 된다 (스펙 §8.1)
    const before = reduceEvents(initialStreamState(), {
      event_id: 1, aggregate: "requirement", aggregate_id: "REQ-1",
      event_type: "state_changed", payload: { to: "implementing", signal: "plan_ready" },
    });
    const after = reduceEvents(before, {
      event_id: 2, aggregate: "requirement", aggregate_id: "REQ-1",
      event_type: "tool_result",
      payload: { agent: "qa", tool: "run_tests", revision: 1, ok: false, exit_code: 1, detail: "실패" },
    });
    expect(after.requirementState).toBe(before.requirementState);
    expect(after.lastFailedVerifier).toBe(before.lastFailedVerifier);
  });

  it("이미 본 도구 이벤트는 중복 반영하지 않는다", () => {
    const evt: VsiEvent = {
      event_id: 7, aggregate: "requirement", aggregate_id: "REQ-1",
      event_type: "tool_result",
      payload: { agent: "dev", tool: "read_file", revision: 1, ok: true, detail: "" },
    };
    const once = reduceEvents(initialStreamState(), evt);
    const twice = reduceEvents(once, evt);
    expect(twice.activity).toEqual(once.activity);
  });
});
