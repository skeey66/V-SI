import { describe, expect, it } from "vitest";
import { initialOfficeState, reduceOffice, type OfficeState } from "./officeState";
import type { VsiEvent } from "../useEventStream";

let seq = 0;
function ev(event_type: string, payload: Record<string, unknown>, aggregate = "task"): VsiEvent {
  seq += 1;
  return { event_id: seq, aggregate, aggregate_id: "x", event_type, payload };
}
function feed(events: VsiEvent[], from: OfficeState = initialOfficeState()): OfficeState {
  return events.reduce(reduceOffice, from);
}

describe("작업 중 표시", () => {
  it("task_submitted 는 그 자리를 타이핑으로 만들고 모니터를 켠다", () => {
    const s = feed([ev("task_submitted", { agent: "dev" })]);
    expect(s.motions.dev).toBe("typing");
    expect(s.monitors.dev).toBe(true);
    expect(s.motions.qa).toBe("idle");
  });

  it("task_completed 는 자리에 앉히고 모니터를 끈다", () => {
    const s = feed([
      ev("task_submitted", { agent: "dev" }),
      ev("task_completed", { agent: "dev", verdict: null }),
    ]);
    expect(s.motions.dev).toBe("idle");
    expect(s.monitors.dev).toBe(false);
  });

  it("모르는 에이전트 이름은 무시한다", () => {
    // 게이트웨이가 새 역할을 싣기 시작해도 화면이 깨지지 않아야 한다.
    const s = feed([ev("task_submitted", { agent: "designer" })]);
    expect(s).toEqual(initialOfficeState());
  });

  it("tool_result 는 아무것도 바꾸지 않는다", () => {
    // 자문 이벤트다(스펙 §8.1) — 워크플로 권위가 없다.
    const before = feed([ev("task_submitted", { agent: "qa" })]);
    const after = reduceOffice(before, ev("tool_result", { agent: "qa", tool: "run_tests" }));
    expect(after.motions).toEqual(before.motions);
    expect(after.handoff).toBe(before.handoff);
  });
});

describe("서류 전달", () => {
  it("끝난 사람 → 다음 사람으로 걸어간다", () => {
    const s = feed([
      ev("task_submitted", { agent: "planner" }),
      ev("task_completed", { agent: "planner" }),
      ev("task_submitted", { agent: "dev" }),
    ]);
    expect(s.handoff).toMatchObject({ from: "planner", to: "dev", kind: "pass" });
  });

  it("환류 뒤의 전달은 반려(빨간 서류)다", () => {
    const s = feed([
      ev("task_submitted", { agent: "qa" }),
      ev("task_completed", { agent: "qa", verdict: "FAIL" }),
      ev("revision_started", { revision: 2 }, "requirement"),
      ev("task_submitted", { agent: "dev" }),
    ]);
    expect(s.handoff).toMatchObject({ from: "qa", to: "dev", kind: "reject" });
  });

  it("반려 표시는 한 번 쓰이고 사라진다", () => {
    let s = feed([
      ev("task_submitted", { agent: "qa" }),
      ev("task_completed", { agent: "qa", verdict: "FAIL" }),
      ev("revision_started", { revision: 2 }, "requirement"),
      ev("task_submitted", { agent: "dev" }),
      ev("task_completed", { agent: "dev" }),
      ev("task_submitted", { agent: "qa" }),
    ]);
    // 두 번째 걸음은 다시 평범한 전달이어야 한다.
    expect(s.handoff).toMatchObject({ from: "dev", to: "qa", kind: "pass" });
  });

  it("걸음마다 id 가 올라간다 — 장면이 같은 걸음을 두 번 재생하지 않게", () => {
    const a = feed([
      ev("task_submitted", { agent: "planner" }),
      ev("task_completed", { agent: "planner" }),
      ev("task_submitted", { agent: "dev" }),
    ]);
    const b = feed(
      [ev("task_completed", { agent: "dev" }), ev("task_submitted", { agent: "qa" })],
      a,
    );
    expect(b.handoff!.id).toBeGreaterThan(a.handoff!.id);
  });

  it("첫 제출에는 걸음이 없다 — 넘겨줄 사람이 없다", () => {
    const s = feed([ev("task_submitted", { agent: "planner" })]);
    expect(s.handoff).toBeNull();
  });

  it("같은 에이전트가 이어서 제출되면 걸음을 만들지 않는다", () => {
    // 재시도(같은 역할 2회차)는 누가 누구에게 건네는 그림이 아니다.
    const s = feed([
      ev("task_submitted", { agent: "dev" }),
      ev("task_completed", { agent: "dev" }),
      ev("task_submitted", { agent: "dev" }),
    ]);
    expect(s.handoff).toBeNull();
  });
});

describe("손을 드는 사람", () => {
  it("blocked 는 직전에 FAIL 로 끝난 자리가 손을 든다", () => {
    const s = feed([
      ev("task_submitted", { agent: "planner" }),
      ev("task_completed", { agent: "planner", verdict: "FAIL" }),
      ev("state_changed", { to: "blocked", signal: "approval_required" }, "requirement"),
    ]);
    expect(s.handUp).toEqual({ agent: "planner", tone: "warn" });
    expect(s.motions.planner).toBe("hand-up");
  });

  it("주인을 못 찾으면 아무도 손을 들지 않는다", () => {
    // 추측해서 엉뚱한 캐릭터가 손을 들면 사람이 잘못된 자리를 들여다본다.
    // 조명만 바꾸고 손은 들지 않는다.
    const s = feed([
      ev("state_changed", { to: "blocked", signal: "approval_required" }, "requirement"),
    ]);
    expect(s.handUp).toBeNull();
    expect(s.moodLight).toBe("stopped");
    expect(Object.values(s.motions).every((m) => m !== "hand-up")).toBe(true);
  });

  it("escalated 는 payload 의 agent 를 먼저 믿는다", () => {
    // give_up(재시도 예산 소진)은 agent 를 실어 보낸다(Task 12).
    const s = feed([
      ev("task_submitted", { agent: "qa" }),
      ev("task_completed", { agent: "qa", verdict: "FAIL" }),
      ev(
        "state_changed",
        { to: "escalated", agent: "dev", failure_class: "execution" },
        "requirement",
      ),
    ]);
    expect(s.handUp).toEqual({ agent: "dev", tone: "fail" });
  });

  it("escalated 에 agent 가 없으면 마지막 FAIL 로 돌아간다", () => {
    // remediate(회차 상한 초과)는 reason 만 싣는다.
    const s = feed([
      ev("task_submitted", { agent: "qa" }),
      ev("task_completed", { agent: "qa", verdict: "FAIL" }),
      ev("state_changed", { to: "escalated", reason: "max_revisions_exceeded" }, "requirement"),
    ]);
    expect(s.handUp).toEqual({ agent: "qa", tone: "fail" });
  });

  it("승인해서 다시 굴러가면 손을 내린다", () => {
    const s = feed([
      ev("task_submitted", { agent: "planner" }),
      ev("task_completed", { agent: "planner", verdict: "FAIL" }),
      ev("state_changed", { to: "blocked" }, "requirement"),
      ev("state_changed", { to: "implementing", signal: "approval_granted" }, "requirement"),
    ]);
    expect(s.handUp).toBeNull();
    expect(s.motions.planner).toBe("idle");
    expect(s.moodLight).toBe("normal");
  });
});

describe("죽은 에이전트와 마무리", () => {
  it("task_failed 는 판정 FAIL 과 다르게 그린다", () => {
    const s = feed([
      ev("task_submitted", { agent: "dev" }),
      ev("task_failed", { agent: "dev", failure_class: "execution" }),
    ]);
    expect(s.motions.dev).toBe("slumped");
    expect(s.monitors.dev).toBe(false);
  });

  it("죽은 뒤 재시도 제출은 걸음 없이 다시 앉혀 일하게 한다", () => {
    const s = feed([
      ev("task_submitted", { agent: "dev" }),
      ev("task_failed", { agent: "dev" }),
      ev("task_submitted", { agent: "dev" }),
    ]);
    expect(s.motions.dev).toBe("typing");
    expect(s.handoff).toBeNull();
  });

  it("accepted 면 조명이 바뀌고 모니터가 다 꺼진다", () => {
    const s = feed([
      ev("task_submitted", { agent: "qa" }),
      ev("state_changed", { to: "accepted" }, "requirement"),
    ]);
    expect(s.moodLight).toBe("accepted");
    expect(Object.values(s.monitors).every((m) => m === false)).toBe(true);
  });

  it("requirementState 를 그대로 들고 있는다", () => {
    const s = feed([ev("state_changed", { to: "verifying" }, "requirement")]);
    expect(s.requirementState).toBe("verifying");
  });
});
