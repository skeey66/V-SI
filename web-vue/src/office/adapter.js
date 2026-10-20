// 새 SSE 이벤트 → 3D 사무실 리듀서가 읽는 기존 이벤트(`legacyEvent.ts`) 변환.
//
// 사무실 코드는 기존 A2A 경로 화면에서 그대로 가져왔다. 그쪽 이벤트는
// "작업 제출(task_submitted) → 작업 완료(task_completed) → 상태 변경(state_changed)"
// 모양이라, LangGraph 노드 이벤트를 그 의미에 맞춰 옮긴다.
//
//   agent_start          → task_submitted   (자리에 앉아 타이핑, 직전 완료자가 서류를 들고 걸어옴)
//   gate                 → 기획의 task_completed (게이트 FAIL 이면 기획이 FAIL)
//   node_end(dev)        → 개발의 task_completed
//   verdict              → QA·보안의 task_completed (PASS/FAIL)
//   agent_start(dev, 2회차+) 앞에 revision_started  → 서류가 빨갛게(반려)
//   interrupt / 종료     → state_changed (손 들기, 방 조명)

/** SSE 이벤트 하나 → 기존 이벤트 0개 이상 ({event_type, payload}) */
export function toOfficeEvents(ev) {
  switch (ev.type) {
    case 'agent_start': {
      const submit = { event_type: 'task_submitted', payload: { agent: ev.agent } }
      if (ev.agent === 'dev' && ev.revision > 1) {
        return [{ event_type: 'revision_started', payload: { revision: ev.revision } }, submit]
      }
      return [submit]
    }
    case 'gate':
      return [{ event_type: 'task_completed', payload: { agent: 'planner', verdict: ev.ok ? 'PASS' : 'FAIL' } }]
    case 'node_end':
      if (ev.node === 'dev') return [{ event_type: 'task_completed', payload: { agent: 'dev' } }]
      if (ev.node === 'judge' && (ev.status === 'accepted' || ev.status === 'escalated')) {
        return [{ event_type: 'state_changed', payload: { to: ev.status } }]
      }
      return []
    case 'verdict':
      return [{ event_type: 'task_completed', payload: { agent: ev.agent, verdict: ev.verdict } }]
    case 'interrupt':
      return [{ event_type: 'state_changed', payload: { to: 'blocked' } }]
    case 'resumed':
      if (ev.approved === null || ev.approved === undefined) return []
      return [{ event_type: 'state_changed', payload: { to: ev.approved ? 'implementing' : 'escalated' } }]
    case 'done':
      return [{ event_type: 'state_changed', payload: { to: ev.status } }]
    case 'error':
      return [{ event_type: 'state_changed', payload: { to: 'escalated' } }]
    default:
      return []
  }
}

/** SSE 이벤트 목록 → 리듀서에 넣을 기존 이벤트 목록 (event_id 를 매긴다) */
export function toOfficeStream(events) {
  const out = []
  for (const ev of events) {
    for (const e of toOfficeEvents(ev)) {
      out.push({ event_id: out.length + 1, aggregate: 'task', aggregate_id: '', ...e })
    }
  }
  return out
}
