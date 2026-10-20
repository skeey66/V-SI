import { describe, it, expect } from 'vitest'
import { createSseParser } from './sse.js'

// 파서에 chunk 들을 차례로 넣고, 나온 이벤트 목록을 돌려준다.
function run(chunks) {
  const out = []
  const parser = createSseParser((ev) => out.push(ev))
  for (const c of chunks) parser.feed(c)
  return out
}

describe('createSseParser', () => {
  it('이벤트 하나를 파싱한다', () => {
    const out = run(['event: gate\ndata: {"seq":1,"ok":true}\n\n'])
    expect(out).toEqual([{ event: 'gate', data: '{"seq":1,"ok":true}' }])
  })

  it('한 chunk 안의 여러 이벤트를 모두 꺼낸다', () => {
    const out = run([
      'event: token\ndata: {"seq":1}\n\nevent: token\ndata: {"seq":2}\n\nevent: done\ndata: {"seq":3}\n\n',
    ])
    expect(out.map((e) => e.event)).toEqual(['token', 'token', 'done'])
    expect(JSON.parse(out[2].data).seq).toBe(3)
  })

  it('줄 중간에서 잘린 chunk 를 이어 붙인다', () => {
    const out = run(['eve', 'nt: tok', 'en\nda', 'ta: {"seq":', '7,"text":"안녕"}\n', '\n'])
    expect(out).toEqual([{ event: 'token', data: '{"seq":7,"text":"안녕"}' }])
  })

  it('빈 줄이 오기 전에는 이벤트를 내보내지 않는다', () => {
    const out = []
    const p = createSseParser((ev) => out.push(ev))
    p.feed('event: gate\ndata: {}\n')
    expect(out).toHaveLength(0)
    p.feed('\n')
    expect(out).toHaveLength(1)
  })

  it('주석(: ping) 줄은 무시한다', () => {
    const out = run([': ping\n\n', 'event: rag\n: 중간 주석\ndata: {"seq":1}\n\n', ': ping\n\n'])
    expect(out).toEqual([{ event: 'rag', data: '{"seq":1}' }])
  })

  it('\\r\\n 줄바꿈도 처리한다', () => {
    const out = run(['event: done\r\ndata: {"seq":9}\r\n\r\n'])
    expect(out).toEqual([{ event: 'done', data: '{"seq":9}' }])
  })

  it('여러 data 줄은 \\n 으로 합친다, event 가 없으면 message', () => {
    const out = run(['data: a\ndata: b\n\n'])
    expect(out).toEqual([{ event: 'message', data: 'a\nb' }])
  })
})
