<script setup>
// RAG 지식 묶음 이름 (mcp_servers/knowledge_server/index.py 의 COLLECTIONS)
const COLLECTION_LABELS = {
  testdesign: '테스트 설계', securecoding: '시큐어 코딩', qa: '테스트 실패 진단', security: '보안 조치',
}
// 출처 URL → 짧은 이름
function sourceLabel(src) {
  if (!src || src.startsWith('V-SI')) return '사내 가이드'
  if (src.includes('OWASP')) return 'OWASP'
  if (src.includes('pytest')) return 'pytest 문서'
  if (src.includes('cpython')) return 'Python 문서'
  if (src.includes('bandit')) return 'bandit 문서'
  if (src.includes('cwe.mitre')) return 'MITRE CWE'
  return src.split('/')[2] || src
}
import { ref, watch, nextTick } from 'vue'
import { AGENT_LABELS, formatTime, formatSeconds, STATUS_LABELS } from '../utils/format.js'

// events: token 을 제외한 SSE 이벤트 배열 (시간순)
const props = defineProps({ events: { type: Array, required: true } })

const label = (agent) => AGENT_LABELS[agent] || agent

// 이벤트 종류 → 짧은 라벨
const KIND = {
  run_started: '접수', agent_start: '착수', tool_call: '도구', tool_result: '결과', node_end: '단계',
  gate: '점검', verdict: '판정', rag: '검색', interrupt: '승인', resumed: '재개', done: '종료', error: '오류',
}
const kindClass = (ev) => {
  if (ev.type === 'verdict') return ev.verdict === 'PASS' ? 'k-ok' : 'k-ng'
  if (ev.type === 'tool_result' && ev.ok === false) return 'k-ng'
  if (ev.type === 'gate') return ev.ok ? 'k-ok' : 'k-ng'
  if (ev.type === 'done') return ev.status === 'accepted' ? 'k-ok' : 'k-ng'
  if (ev.type === 'error') return 'k-ng'
  if (ev.type === 'interrupt') return 'k-wait'
  if (ev.type === 'rag') return 'k-rag'
  return ''
}
// 3D 사무실의 팀 색과 같다
const TEAM_COLOR = { planner: '#d4805c', dev: '#6f9fd8', qa: '#d8b64a', security: '#79ad86' }
// 같은 날 실행이라 시각만 보여준다
const clock = (ts) => formatTime(ts).split(' ').pop()
// tool_call 의 args 는 길 수 있으므로 한 줄로 줄여서 보여준다
const shortArgs = (args) => {
  const s = typeof args === 'string' ? args : JSON.stringify(args)
  return s && s.length > 120 ? s.slice(0, 120) + '…' : s
}

// 새 이벤트가 오면 목록 맨 아래로 스크롤
const box = ref(null)
watch(
  () => props.events.length,
  async () => {
    await nextTick()
    if (box.value) box.value.scrollTop = box.value.scrollHeight
  },
)
</script>

<template>
  <div ref="box" class="timeline">
    <p v-if="events.length === 0" class="muted">이벤트를 기다리는 중...</p>

    <template v-for="ev in events" :key="ev.seq">
      <!-- 개발 에이전트가 시작될 때마다 회차 구분선 -->
      <div v-if="ev.type === 'agent_start' && ev.agent === 'dev'" class="divider">
        {{ ev.revision ?? '' }}회차
      </div>

      <div class="item" :class="ev.type">
        <span class="time">{{ clock(ev.ts) }}</span>
        <span class="kind" :class="kindClass(ev)">{{ KIND[ev.type] || ev.type }}</span>
        <div class="body">
          <span v-if="ev.agent" class="who" :style="{ '--team': TEAM_COLOR[ev.agent] }">{{ label(ev.agent) }}</span>

          <template v-if="ev.type === 'run_started'">{{ ev.title }}</template>

          <template v-else-if="ev.type === 'agent_start'">작업을 시작했습니다</template>

          <template v-else-if="ev.type === 'tool_call'">
            <code>{{ ev.tool }}</code> <span class="muted args">{{ shortArgs(ev.args) }}</span>
          </template>

          <template v-else-if="ev.type === 'tool_result'">
            <code>{{ ev.tool }}</code>
            <span :class="ev.ok ? 'pass' : 'fail'">{{ ev.ok ? '성공' : '실패' }}</span>
            <span v-if="ev.exit_code !== undefined && ev.exit_code !== null" class="muted">
              종료코드 {{ ev.exit_code }}
            </span>
          </template>

          <template v-else-if="ev.type === 'node_end'">
            <span class="muted">{{ label(ev.node) }} 단계 끝</span>
            <span v-if="ev.status" class="muted"> · {{ STATUS_LABELS[ev.status] || ev.status }}</span>
          </template>

          <details v-else-if="ev.type === 'gate'">
            <summary>
              인수 테스트 점검 <span :class="ev.ok ? 'pass' : 'fail'">{{ ev.ok ? '통과' : '차단' }}</span>
            </summary>
            <pre>{{ ev.detail }}</pre>
          </details>

          <!-- 판정은 요약이 길 수 있으므로 펼쳐보기 -->
          <details v-else-if="ev.type === 'verdict'">
            <summary>
              <span :class="ev.verdict === 'PASS' ? 'pass' : 'fail'">{{ ev.verdict === 'PASS' ? '합격' : '불합격' }}</span>
              <span class="muted"> 종료코드 {{ ev.exit_code }} · {{ ev.revision }}회차</span>
            </summary>
            <pre>{{ ev.summary }}</pre>
          </details>

          <details v-else-if="ev.type === 'rag'">
            <summary>
              {{ COLLECTION_LABELS[ev.collection] || ev.collection || '보안 조치' }} 문서 {{ ev.hits?.length || 0 }}건
            </summary>
            <div class="muted">질의: {{ ev.query }}</div>
            <ul>
              <li v-for="h in ev.hits" :key="h.id">
                {{ h.title }} <span class="muted">({{ Number(h.score).toFixed(2) }} · {{ sourceLabel(h.source) }})</span>
              </li>
            </ul>
          </details>

          <template v-else-if="ev.type === 'interrupt'"><b>사람 승인이 필요합니다.</b> {{ ev.detail }}</template>

          <template v-else-if="ev.type === 'resumed'">{{ ev.approved ? '승인되어 이어서 진행합니다' : '중단했습니다' }}</template>

          <template v-else-if="ev.type === 'done'">
            <b>{{ STATUS_LABELS[ev.status] || ev.status }}</b>
            <span class="muted"> · {{ ev.revision }}회차 · {{ formatSeconds(ev.elapsed_s) }}</span>
          </template>

          <template v-else-if="ev.type === 'error'"><span class="fail">{{ ev.message }}</span></template>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.timeline { max-height: 560px; overflow-y: auto; }
.item { display: flex; gap: 10px; align-items: baseline; padding: 6px 0; border-bottom: 1px dashed var(--border); }
.time { color: var(--muted); font-size: 12px; white-space: nowrap; font-family: var(--mono); }
.kind {
  flex-shrink: 0; width: 34px; text-align: center; font-size: 11.5px; font-weight: 600;
  color: var(--ink-2); background: var(--paper-2); border: 1px solid var(--rule); border-radius: 4px;
}
.kind.k-ok { color: var(--pass); background: var(--pass-weak); border-color: #b9dcc8; }
.kind.k-ng { color: var(--seal); background: var(--seal-weak); border-color: #efc4bf; }
.kind.k-wait { color: var(--hold); background: var(--hold-weak); border-color: #ecd3a6; }
.kind.k-rag { color: var(--pen); background: var(--pen-weak); border-color: #c9d3f6; }
.who { font-weight: 600; margin-right: 6px; }
.who::before {
  content: ''; display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  background: var(--team); margin-right: 5px; vertical-align: 1px;
}
.args { font-size: 12px; }
.body { flex: 1; min-width: 0; word-break: break-word; }
.body pre { margin-top: 6px; }
.body ul { margin: 4px 0; padding-left: 18px; }
.divider {
  margin: 10px 0 4px; font-size: 12px; font-weight: 700; color: var(--primary);
  border-bottom: 2px solid var(--primary-weak);
}
.item.interrupt { background: var(--warn-weak); }
.item.error { background: var(--fail-weak); }
summary { cursor: pointer; }
</style>
