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
        <span class="time">{{ formatTime(ev.ts) }}</span>
        <div class="body">
          <template v-if="ev.type === 'run_started'">🚀 실행 시작: {{ ev.title }}</template>

          <template v-else-if="ev.type === 'agent_start'">
            ▶ <b>{{ label(ev.agent) }}</b> 에이전트 시작
          </template>

          <template v-else-if="ev.type === 'tool_call'">
            🔧 {{ label(ev.agent) }} → <code>{{ ev.tool }}</code>
            <span class="muted">{{ shortArgs(ev.args) }}</span>
          </template>

          <template v-else-if="ev.type === 'tool_result'">
            ↩ <code>{{ ev.tool }}</code>
            <span :class="ev.ok ? 'pass' : 'fail'">{{ ev.ok ? '성공' : '실패' }}</span>
            <span v-if="ev.exit_code !== undefined && ev.exit_code !== null" class="muted">
              (exit {{ ev.exit_code }})
            </span>
          </template>

          <template v-else-if="ev.type === 'node_end'">
            <span class="muted">■ {{ label(ev.node) }} 노드 종료</span>
            <span v-if="ev.status" class="muted"> → {{ STATUS_LABELS[ev.status] || ev.status }}</span>
          </template>

          <template v-else-if="ev.type === 'gate'">
            🚦 게이트: <span :class="ev.ok ? 'pass' : 'fail'">{{ ev.ok ? '통과' : '차단' }}</span>
            <span class="muted"> {{ ev.detail }}</span>
          </template>

          <!-- 판정은 요약이 길 수 있으므로 펼쳐보기 -->
          <details v-else-if="ev.type === 'verdict'">
            <summary>
              ⚖ <b>{{ label(ev.agent) }}</b> 판정
              <span :class="ev.verdict === 'PASS' ? 'pass' : 'fail'">{{ ev.verdict }}</span>
              <span class="muted"> (exit {{ ev.exit_code }}, {{ ev.revision }}회차)</span>
            </summary>
            <pre>{{ ev.summary }}</pre>
          </details>

          <details v-else-if="ev.type === 'rag'">
            <summary>📚 {{ label(ev.agent) }} 지식 검색 · {{ COLLECTION_LABELS[ev.collection] || ev.collection || '보안 조치' }}: {{ ev.hits?.length || 0 }}건</summary>
            <div class="muted">질의: {{ ev.query }}</div>
            <ul>
              <li v-for="h in ev.hits" :key="h.id">
                {{ h.title }} <span class="muted">({{ Number(h.score).toFixed(2) }} · {{ sourceLabel(h.source) }})</span>
              </li>
            </ul>
          </details>

          <template v-else-if="ev.type === 'interrupt'">
            ✋ <b>사람 승인 필요</b>: {{ ev.detail }}
          </template>

          <template v-else-if="ev.type === 'resumed'">
            ▶ 재개: {{ ev.approved ? '승인됨' : '중단됨' }}
          </template>

          <template v-else-if="ev.type === 'done'">
            🏁 <b>종료</b>: {{ STATUS_LABELS[ev.status] || ev.status }} ·
            {{ ev.revision }}회차 · {{ formatSeconds(ev.elapsed_s) }}
          </template>

          <template v-else-if="ev.type === 'error'">
            <span class="fail">❌ 오류: {{ ev.message }}</span>
          </template>

          <template v-else>{{ ev.type }}</template>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.timeline { max-height: 560px; overflow-y: auto; }
.item { display: flex; gap: 10px; padding: 5px 0; border-bottom: 1px dashed var(--border); }
.time { color: var(--muted); font-size: 12px; white-space: nowrap; font-family: var(--mono); padding-top: 1px; }
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
