import { describe, expect, it } from 'vitest'
import { toOfficeStream } from './adapter.js'
import { initialOfficeState, reduceOffice } from './officeState'

const run = (events) => toOfficeStream(events).reduce(reduceOffice, initialOfficeState())

describe('SSE → 3D 사무실', () => {
  it('개발이 시작하면 기획이 서류를 들고 개발에게 걸어간다', () => {
    const s = run([
      { type: 'agent_start', agent: 'planner', revision: 0 },
      { type: 'gate', ok: true },
      { type: 'agent_start', agent: 'dev', revision: 1 },
    ])
    expect(s.motions.dev).toBe('typing')
    expect(s.handoff).toMatchObject({ from: 'planner', to: 'dev', kind: 'pass' })
  })

  it('반려 후 2회차 개발은 빨간 서류(reject)를 받는다', () => {
    const s = run([
      { type: 'agent_start', agent: 'dev', revision: 1 },
      { type: 'node_end', node: 'dev' },
      { type: 'agent_start', agent: 'qa', revision: 1 },
      { type: 'verdict', agent: 'qa', verdict: 'FAIL' },
      { type: 'node_end', node: 'judge', status: 'remediating' },
      { type: 'agent_start', agent: 'dev', revision: 2 },
    ])
    expect(s.revision).toBe(2)
    expect(s.handoff).toMatchObject({ from: 'qa', to: 'dev', kind: 'reject' })
  })

  it('게이트가 막히면 기획이 손을 든다', () => {
    const s = run([
      { type: 'agent_start', agent: 'planner', revision: 0 },
      { type: 'gate', ok: false },
      { type: 'interrupt', detail: '빈 테스트' },
    ])
    expect(s.handUp).toEqual({ agent: 'planner', tone: 'warn' })
    expect(s.moodLight).toBe('stopped')
  })

  it('완료되면 방 조명이 바뀐다', () => {
    expect(run([{ type: 'done', status: 'accepted' }]).moodLight).toBe('accepted')
  })
})
