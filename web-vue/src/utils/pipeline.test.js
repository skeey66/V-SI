import { describe, it, expect } from 'vitest'
import { computeNodeStates } from './pipeline.js'

describe('computeNodeStates', () => {
  it('처음에는 모두 idle', () => {
    expect(Object.values(computeNodeStates([]))).toEqual(Array(6).fill('idle'))
  })

  it('정상 흐름: 모두 done', () => {
    const s = computeNodeStates([
      { type: 'agent_start', agent: 'planner' },
      { type: 'node_end', node: 'planner' },
      { type: 'gate', ok: true },
      { type: 'agent_start', agent: 'dev' },
      { type: 'node_end', node: 'dev' },
      { type: 'verdict', agent: 'qa', verdict: 'PASS' },
      { type: 'verdict', agent: 'security', verdict: 'PASS' },
      { type: 'node_end', node: 'judge', status: 'accepted' },
    ])
    expect(Object.values(s)).toEqual(Array(6).fill('done'))
  })

  it('반려 후 dev 재시작 시 qa/security/judge 초기화', () => {
    const s = computeNodeStates([
      { type: 'verdict', agent: 'qa', verdict: 'FAIL' },
      { type: 'node_end', node: 'judge', status: 'remediating' },
      { type: 'agent_start', agent: 'dev', revision: 2 },
    ])
    expect(s.dev).toBe('active')
    expect(s.qa).toBe('idle')
    expect(s.judge).toBe('idle')
  })

  it('interrupt 면 gate 는 wait', () => {
    expect(computeNodeStates([{ type: 'interrupt', detail: 'x' }]).gate).toBe('wait')
  })
})
