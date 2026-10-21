// SSE 이벤트 목록을 보고 파이프라인 각 노드의 상태를 계산한다.
// 상태: idle(대기) / active(실행 중) / wait(사람 승인 대기) / done(완료) / fail(실패)

export const PIPELINE_NODES = ['planner', 'gate', 'dev', 'qa', 'security', 'judge']

export function computeNodeStates(events) {
  const s = Object.fromEntries(PIPELINE_NODES.map((n) => [n, 'idle']))
  const set = (node, state) => {
    if (node in s) s[node] = state
  }

  for (const ev of events) {
    switch (ev.type) {
      case 'agent_start':
        // dev 가 다시 시작되면(수정 회차) 뒤쪽 노드들은 다시 대기 상태로 돌린다
        if (ev.agent === 'dev') ['qa', 'security', 'judge'].forEach((n) => set(n, 'idle'))
        set(ev.agent, 'active')
        // qa/security 가 시작됐다는 것은 dev 가 끝났다는 뜻
        if (ev.agent === 'qa' || ev.agent === 'security') {
          if (s.dev === 'active') set('dev', 'done')
        }
        break
      case 'gate':
        set('gate', ev.ok ? 'done' : 'fail')
        break
      case 'interrupt':
        set('gate', 'wait')
        break
      case 'resumed':
        set('gate', ev.approved ? 'done' : 'fail')
        break
      case 'verdict':
        set(ev.agent, ev.verdict === 'PASS' ? 'done' : 'fail')
        break
      case 'node_end':
        if (ev.node === 'judge') {
          // judge 결과: accepted 면 통과, remediating/escalated 면 반려
          if (ev.status === 'accepted') set('judge', 'done')
          else if (ev.status) set('judge', 'fail')
          else set('judge', 'done')
        } else if (s[ev.node] === 'active' || s[ev.node] === 'idle') {
          // 이미 fail/done 으로 정해졌으면(verdict, gate) 덮어쓰지 않는다
          set(ev.node, 'done')
        }
        break
      case 'done':
        if (ev.status === 'accepted') set('judge', 'done')
        else if (ev.status === 'escalated') set('judge', 'fail')
        break
      case 'error':
        // 실행 중이던 노드를 실패로 표시
        for (const n of PIPELINE_NODES) if (s[n] === 'active') s[n] = 'fail'
        break
    }
  }
  return s
}
