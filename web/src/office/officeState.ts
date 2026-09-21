/** 이벤트 → 사무실에서 무슨 일이 벌어지는가.
 *
 * **이 파일에만 판단이 있다.** `OfficeScene.ts` 는 여기서 나온 상태를 그리기만
 * 한다 — WebGL 은 node 에서 못 돌려 테스트가 안 붙으므로, 값어치 있는 로직을
 * 전부 이쪽으로 빼 놓는 것이 이 분리의 목적이다.
 *
 * `useEventStream.ts` 의 `reduceEvents` 와 같은 모양이다(순수 함수, 이전 상태 +
 * 이벤트 하나 → 다음 상태). 두 리듀서가 같은 이벤트를 각자 줄인다: 저쪽은
 * 그래프·로그를, 이쪽은 사무실을.
 */
import type { VsiEvent } from "../useEventStream";

export const AGENT_IDS = ["planner", "dev", "qa", "security"] as const;
export type AgentId = (typeof AGENT_IDS)[number];

export function isAgentId(v: unknown): v is AgentId {
  return typeof v === "string" && (AGENT_IDS as readonly string[]).includes(v);
}

/** 캐릭터 한 명의 동작. 장면은 이 값만 보고 애니메이션을 고른다. */
export type AgentMotion =
  | "idle" // 자리에 앉아 대기
  | "typing" // 작업 중 — 팔이 두드리고 모니터가 밝다
  | "slumped" // 죽었다(task_failed) — 고개를 숙인다
  | "hand-up"; // 사람을 부른다

/** 서류를 들고 걸어가는 **사건**. 상태가 아니라 한 번 재생되고 끝난다.
 *
 * `useEventStream` 의 `reworkPulse` 와 같은 취급이다 — 장면이 `id` 를 기억해
 * 같은 걸음을 두 번 재생하지 않는다. 리듀서가 매번 새 객체를 만들어도 `id` 가
 * 같으면 무시된다. */
export type Handoff = {
  id: number;
  from: AgentId;
  to: AgentId;
  /** `pass` 는 다음 단계로 넘김, `reject` 는 환류(빨간 서류). */
  kind: "pass" | "reject";
};

/** 손을 든 사람. 누구인지 **모르면 null** 이다 — 추측해서 엉뚱한 캐릭터가
 * 손을 들면, 사람이 잘못된 자리를 들여다보게 된다. */
export type HandUp = {
  agent: AgentId;
  /** `warn` = 확인해 주세요(blocked), `fail` = 도와주세요(escalated). */
  tone: "warn" | "fail";
};

export type OfficeState = {
  motions: Record<AgentId, AgentMotion>;
  /** 모니터가 켜져 있는가. 작업 중인 자리만 밝다. */
  monitors: Record<AgentId, boolean>;
  handoff: Handoff | null;
  handUp: HandUp | null;
  /** 전체가 끝나면 방 조명이 바뀐다. */
  moodLight: "normal" | "accepted" | "stopped";
  /** 사람을 부른 이유. 손을 못 들어도 이건 남는다. */
  requirementState: string | null;
  /** 지금 몇 회차인가. 산출물을 고를 때 쓴다. */
  revision: number;

  // ── 아래는 다음 이벤트를 해석하기 위한 기억이다(장면은 안 본다) ──
  /** 직전에 일을 끝낸 에이전트. 다음 `task_submitted` 와 이어 붙여 "전달"을 만든다. */
  _lastCompleted: AgentId | null;
  /** 마지막으로 FAIL 로 끝난 에이전트. `blocked` 의 주인을 찾는 유일한 단서다. */
  _lastFailed: AgentId | null;
  /** 환류가 막 시작됐다 — 다음 dev 제출은 "전달"이 아니라 "반려"로 그린다. */
  _reworkPending: boolean;
  _handoffSeq: number;
};

export function initialOfficeState(): OfficeState {
  const motions = {} as Record<AgentId, AgentMotion>;
  const monitors = {} as Record<AgentId, boolean>;
  for (const a of AGENT_IDS) {
    motions[a] = "idle";
    monitors[a] = false;
  }
  return {
    motions,
    monitors,
    handoff: null,
    handUp: null,
    moodLight: "normal",
    requirementState: null,
    revision: 1,
    _lastCompleted: null,
    _lastFailed: null,
    _reworkPending: false,
    _handoffSeq: 0,
  };
}

export function reduceOffice(prev: OfficeState, e: VsiEvent): OfficeState {
  const motions = { ...prev.motions };
  const monitors = { ...prev.monitors };
  let handoff = prev.handoff;
  let handUp = prev.handUp;
  let moodLight = prev.moodLight;
  let requirementState = prev.requirementState;
  let revision = prev.revision;
  let lastCompleted = prev._lastCompleted;
  let lastFailed = prev._lastFailed;
  let reworkPending = prev._reworkPending;
  let handoffSeq = prev._handoffSeq;

  const agent = isAgentId(e.payload.agent) ? e.payload.agent : undefined;

  switch (e.event_type) {
    case "task_submitted": {
      if (!agent) break;
      motions[agent] = "typing";
      monitors[agent] = true;
      // 직전에 끝난 사람이 있으면 그 사람이 걸어와 넘겨준 것으로 그린다.
      // 환류 직후면 "반려"다 — 같은 걸음이지만 서류가 빨갛다.
      if (lastCompleted && lastCompleted !== agent) {
        handoffSeq += 1;
        handoff = {
          id: handoffSeq,
          from: lastCompleted,
          to: agent,
          kind: reworkPending ? "reject" : "pass",
        };
        // 걸어가는 동안은 타이핑하지 않는다. 도착하면 장면이 자리에 앉힌다.
        if (motions[lastCompleted] === "idle") motions[lastCompleted] = "idle";
      }
      reworkPending = false;
      lastCompleted = null;
      break;
    }

    case "task_completed": {
      if (!agent) break;
      motions[agent] = "idle";
      monitors[agent] = false;
      const verdict = e.payload.verdict;
      if (verdict === "FAIL") lastFailed = agent;
      lastCompleted = agent;
      break;
    }

    case "task_failed": {
      if (!agent) break;
      // 판정 FAIL(정상 완료)과 다르다 — 이쪽은 에이전트가 죽은 것이다.
      motions[agent] = "slumped";
      monitors[agent] = false;
      lastFailed = agent;
      lastCompleted = null;
      break;
    }

    case "revision_started": {
      const rev = e.payload.revision;
      if (typeof rev === "number") revision = rev;
      // 다음 제출을 "반려"로 그리라는 표시. 걸음 자체는 그 제출 때 만든다 —
      // 여기서 만들면 누가 받는지(dev)를 추측해야 한다.
      reworkPending = true;
      break;
    }

    case "state_changed": {
      const to = typeof e.payload.to === "string" ? e.payload.to : undefined;
      if (!to) break;
      requirementState = to;

      if (to === "blocked") {
        // 기획 게이트. payload 에 에이전트가 없어서, 직전에 FAIL 로 끝난
        // 에이전트를 주인으로 본다(게이트는 그 FAIL 바로 뒤에 온다).
        // 못 찾으면 **아무도 손을 들지 않는다** — 엉뚱한 자리를 들여다보게
        // 만드느니 방 조명만 바꾼다.
        moodLight = "stopped";
        handUp = lastFailed ? { agent: lastFailed, tone: "warn" } : null;
        if (lastFailed) motions[lastFailed] = "hand-up";
      } else if (to === "escalated") {
        moodLight = "stopped";
        // give_up 경로는 agent 를 실어 보낸다(Task 12). remediate 경로는 안 싣는다.
        const owner = isAgentId(e.payload.agent) ? e.payload.agent : lastFailed;
        handUp = owner ? { agent: owner, tone: "fail" } : null;
        if (owner) motions[owner] = "hand-up";
      } else if (to === "accepted") {
        moodLight = "accepted";
        handUp = null;
        for (const a of AGENT_IDS) {
          if (motions[a] === "hand-up") motions[a] = "idle";
          monitors[a] = false;
        }
      } else {
        // 다시 굴러가기 시작했다(예: 승인 뒤 implementing). 들었던 손을 내린다.
        moodLight = "normal";
        if (handUp) {
          if (motions[handUp.agent] === "hand-up") motions[handUp.agent] = "idle";
          handUp = null;
        }
      }
      break;
    }

    default:
      break;
  }

  return {
    motions,
    monitors,
    handoff,
    handUp,
    moodLight,
    requirementState,
    revision,
    _lastCompleted: lastCompleted,
    _lastFailed: lastFailed,
    _reworkPending: reworkPending,
    _handoffSeq: handoffSeq,
  };
}
