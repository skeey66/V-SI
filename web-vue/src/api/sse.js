// 자체 SSE 파싱: EventSource 대신 fetch + ReadableStream 으로 직접 읽는다.
// (EventSource 는 헤더 지정/중단 제어가 불편하고, 동작 원리를 직접 보여주기 위함)
//
// SSE 형식 예:
//   event: token
//   data: {"seq": 3, "type": "token", ...}
//   (빈 줄)            <- 빈 줄이 이벤트 하나의 끝
//   : ping             <- ':' 로 시작하는 줄은 주석 (무시)

/**
 * 문자열 조각(chunk)을 받아 완성된 이벤트마다 onEvent({ event, data }) 를 호출하는 파서.
 * 네트워크 조각은 줄 중간에서 잘릴 수 있으므로, 마지막 미완성 줄은 buffer 에 남겨둔다.
 */
export function createSseParser(onEvent) {
  let buffer = '' // 아직 줄바꿈을 만나지 못한 나머지 텍스트
  let eventName = '' // 현재 이벤트의 event: 값
  let dataLines = [] // 현재 이벤트의 data: 줄들

  function dispatch() {
    // data 가 하나도 없으면 보낼 것이 없다 (SSE 규칙)
    if (dataLines.length > 0) {
      onEvent({ event: eventName || 'message', data: dataLines.join('\n') })
    }
    eventName = ''
    dataLines = []
  }

  function handleLine(line) {
    if (line === '') return dispatch() // 빈 줄 = 이벤트 경계
    if (line.startsWith(':')) return // 주석(ping) 무시

    const idx = line.indexOf(':')
    const field = idx === -1 ? line : line.slice(0, idx)
    let value = idx === -1 ? '' : line.slice(idx + 1)
    if (value.startsWith(' ')) value = value.slice(1) // "data: x" 의 공백 한 칸 제거

    if (field === 'event') eventName = value
    else if (field === 'data') dataLines.push(value)
    // id:, retry: 등은 이 프로젝트에서 쓰지 않으므로 무시 (seq 를 data 안에 담아 보냄)
  }

  return {
    feed(chunk) {
      buffer += chunk
      // \r\n 과 \n 모두 줄바꿈으로 처리
      const lines = buffer.split(/\r?\n/)
      buffer = lines.pop() // 마지막 조각은 줄이 끝나지 않았을 수 있으므로 보관
      for (const line of lines) handleLine(line)
    },
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/**
 * 요구사항 실행 이벤트 스트림을 연결한다.
 * - onEvent(obj): data JSON 을 파싱한 객체 ({ seq, type, ts, ... })
 * - onStatus(text): 연결 상태 표시용 ('연결됨', '재연결 중...' 등)
 * 끊기면 마지막으로 받은 seq 를 ?after= 로 붙여 이어받는다.
 * done / error 이벤트를 받으면 더 이상 재연결하지 않는다.
 * 반환값의 close() 로 중단(AbortController)한다.
 */
export function openEventStream(requirementId, { onEvent, onStatus = () => {} }) {
  const controller = new AbortController()
  let lastSeq = -1 // 지금까지 받은 마지막 seq
  let finished = false // done/error 를 받았는지

  async function connectOnce() {
    const query = lastSeq >= 0 ? `?after=${lastSeq}` : ''
    const res = await fetch(`/api/requirements/${requirementId}/events${query}`, {
      headers: { Accept: 'text/event-stream' },
      signal: controller.signal,
    })
    if (!res.ok || !res.body) throw new Error(`SSE 연결 실패 (HTTP ${res.status})`)
    onStatus('연결됨')

    const parser = createSseParser(({ data }) => {
      let obj
      try {
        obj = JSON.parse(data)
      } catch {
        return // 깨진 data 는 무시
      }
      if (typeof obj.seq === 'number') {
        if (obj.seq <= lastSeq) return // 재연결 시 중복 방지
        lastSeq = obj.seq
      }
      if (obj.type === 'done' || obj.type === 'error') finished = true
      onEvent(obj)
    })

    const reader = res.body.getReader()
    const decoder = new TextDecoder('utf-8')
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      // stream: true -> 한글처럼 여러 바이트 문자가 조각 경계에서 잘려도 안전하게 디코딩
      parser.feed(decoder.decode(value, { stream: true }))
    }
  }

  async function loop() {
    while (!finished && !controller.signal.aborted) {
      try {
        await connectOnce()
      } catch (e) {
        if (controller.signal.aborted) return
        onStatus(`연결 끊김: ${e.message}`)
      }
      if (finished || controller.signal.aborted) break
      onStatus('재연결 중...')
      await sleep(2000)
    }
    if (finished) onStatus('종료')
  }

  loop()
  return { close: () => controller.abort() }
}
