import { describe, expect, it } from 'vitest'
import { formatAgo, statusGroup } from './format.js'

describe('formatAgo', () => {
  const now = 1_000_000
  it('1분 안은 방금', () => expect(formatAgo(now - 30, now)).toBe('방금'))
  it('분 단위', () => expect(formatAgo(now - 180, now)).toBe('3분 전'))
  it('시간 단위', () => expect(formatAgo(now - 7200, now)).toBe('2시간 전'))
  it('값이 없으면 -', () => expect(formatAgo(null, now)).toBe('-'))
})

describe('statusGroup', () => {
  it('상태를 네 묶음으로 나눈다', () => {
    expect(statusGroup('accepted')).toBe('done')
    expect(statusGroup('blocked')).toBe('waiting')
    expect(statusGroup('escalated')).toBe('trouble')
    expect(statusGroup('remediating')).toBe('running')
  })
})
