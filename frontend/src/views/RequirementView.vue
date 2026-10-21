<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { getRequirement } from '../api/client.js'
import { openEventStream } from '../api/sse.js'
import { computeNodeStates } from '../utils/pipeline.js'
import { AGENT_LABELS, TERMINAL, formatSeconds } from '../utils/format.js'
import StatusBadge from '../components/StatusBadge.vue'
import OfficeView from '../components/OfficeView.vue'
import PipelineStrip from '../components/PipelineStrip.vue'
import EventTimeline from '../components/EventTimeline.vue'
import AgentOutput from '../components/AgentOutput.vue'
import ApprovalBox from '../components/ApprovalBox.vue'
import FileViewer from '../components/FileViewer.vue'
import FeedbackBox from '../components/FeedbackBox.vue'

const props = defineProps({ id: { type: String, required: true } })

const detail = ref(null) // GET /api/requirements/{id} 결과
const error = ref('')
const status = ref('') // 화면에 보여줄 현재 상태 (SSE 로 갱신)
const revision = ref(0)
const events = ref([]) // token 을 뺀 이벤트 (타임라인용)
const outputs = reactive({ planner: '', dev: '', qa: '', security: '' }) // 에이전트별 토큰 누적
const activeAgent = ref('')
const pending = ref(null) // 승인 대기 정보 { detail } 또는 null
const finished = ref(false) // done/error 를 받았는지
const streamStatus = ref('연결 중...')

// 경과 시간: run_started 의 ts 부터 지금까지 (끝나면 done.elapsed_s 로 고정)
const startTs = ref(null)
const now = ref(Date.now() / 1000)
const finalElapsed = ref(null)
const elapsed = computed(() => {
  if (finalElapsed.value !== null) return finalElapsed.value
  if (startTs.value === null) return null
  return Math.max(0, now.value - startTs.value)
})

const nodeStates = computed(() => computeNodeStates(events.value))

async function loadDetail() {
  try {
    detail.value = await getRequirement(props.id)
    status.value = detail.value.status
    revision.value = detail.value.revision
    pending.value = detail.value.pending_approval
    if (TERMINAL.includes(detail.value.status)) finished.value = true
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
}

// SSE 이벤트 하나를 받아 화면 상태를 갱신한다
function handleEvent(ev) {
  // token 은 양이 많으므로 타임라인에 넣지 않고 출력 패널에만 붙인다
  if (ev.type === 'token') {
    if (ev.agent in outputs) outputs[ev.agent] += ev.text
    return
  }
  events.value.push(ev)

  switch (ev.type) {
    case 'run_started':
      startTs.value = ev.ts
      break
    case 'agent_start':
      if (startTs.value === null) startTs.value = ev.ts
      activeAgent.value = ev.agent
      if (ev.revision) revision.value = ev.revision
      // 같은 에이전트가 다시 실행되면(수정 회차) 출력 사이에 구분선을 넣는다
      if (outputs[ev.agent]) outputs[ev.agent] += `\n\n──── ${ev.revision ?? ''}회차 ────\n`
      // 상세 상태는 node_end 로 오지만, 시작 시점에도 대략 맞춰준다
      if (ev.agent === 'dev') status.value = ev.revision > 1 ? 'remediating' : 'implementing'
      if (ev.agent === 'qa' || ev.agent === 'security') status.value = 'verifying'
      break
    case 'node_end':
      if (ev.status) status.value = ev.status
      break
    case 'verdict':
      if (ev.revision) revision.value = ev.revision
      break
    case 'interrupt':
      pending.value = { detail: ev.detail }
      status.value = 'blocked'
      break
    case 'resumed':
      pending.value = null
      break
    case 'done':
      finished.value = true
      activeAgent.value = ''
      status.value = ev.status
      revision.value = ev.revision
      finalElapsed.value = ev.elapsed_s
      loadDetail() // 최종 판정, LangSmith 링크 등을 다시 받아온다
      break
    case 'error':
      finished.value = true
      activeAgent.value = ''
      status.value = 'error'
      break
  }
}

let stream = null
let clock = null
onMounted(async () => {
  await loadDetail()
  stream = openEventStream(props.id, {
    onEvent: handleEvent,
    onStatus: (s) => (streamStatus.value = s),
  })
  clock = setInterval(() => (now.value = Date.now() / 1000), 1000)
})
onUnmounted(() => {
  stream?.close() // 페이지를 떠나면 SSE 연결 중단
  clearInterval(clock)
})

// 승인/중단 버튼을 누른 뒤
function onAnswered(approved) {
  pending.value = null
  if (!approved) status.value = 'escalated'
}

const verdictList = computed(() => Object.values(detail.value?.verdicts || {}))
const traceUrl = computed(() => detail.value?.langsmith_urls?.at(-1) || '')
</script>

<template>
  <div v-if="error" class="error-box">{{ error }}</div>

  <!-- 헤더 -->
  <section class="card header">
    <div class="row">
      <RouterLink to="/" class="muted">← 목록</RouterLink>
    </div>
    <h1>{{ detail?.title || '불러오는 중...' }}</h1>
    <div class="row">
      <StatusBadge :status="status" />
      <span>{{ revision }}회차</span>
      <span v-if="elapsed !== null" class="muted">
        소요 {{ formatSeconds(elapsed) }}{{ finished ? '' : ' (진행 중)' }}
      </span>
      <span class="muted stream">스트림: {{ streamStatus }}</span>
    </div>
  </section>

  <section class="card office-card">
    <OfficeView :events="events" :outputs="outputs" :finished="finished" />
    <PipelineStrip :states="nodeStates" />
  </section>

  <ApprovalBox v-if="pending" :requirement-id="id" :detail="pending.detail" @answered="onAnswered" />

  <div class="grid">
    <section class="card">
      <h2>실시간 타임라인</h2>
      <EventTimeline :events="events" />
    </section>
    <section class="card">
      <h2>에이전트 출력 <span v-if="activeAgent" class="muted">· {{ AGENT_LABELS[activeAgent] }} 작성 중</span></h2>
      <AgentOutput :outputs="outputs" :active="activeAgent" />
    </section>
  </div>

  <!-- 끝나면: 납품 검수서 (판정 · 결과물 · 검수 확인) -->
  <section v-if="finished" class="card deliverable">
    <header class="deliverable-head">
      <h2>납품 검수서</h2>
      <a v-if="traceUrl" :href="traceUrl" target="_blank" rel="noopener" class="trace">실행 기록 보기 (LangSmith)</a>
    </header>

    <div class="checks">
      <p v-if="verdictList.length === 0" class="muted">자동 검사 결과가 없습니다.</p>
      <details v-for="v in verdictList" :key="v.agent" class="check" :open="v.verdict !== 'PASS'">
        <summary>
          <span class="mark" :class="v.verdict === 'PASS' ? 'ok' : 'ng'">{{ v.verdict === 'PASS' ? '통과' : '불합격' }}</span>
          <b>{{ AGENT_LABELS[v.agent] || v.agent }} 검사</b>
          <span class="muted">{{ v.agent === 'qa' ? 'pytest' : 'bandit' }} 종료코드 {{ v.exit_code }}</span>
        </summary>
        <pre>{{ v.summary }}</pre>
      </details>
    </div>

    <div class="deliver-grid">
      <div class="deliver-files">
        <h3>결과물</h3>
        <FileViewer :requirement-id="id" />
      </div>
      <aside class="deliver-review">
        <h3>검수 확인</h3>
        <FeedbackBox :requirement-id="id" :trace-url="traceUrl" />
      </aside>
    </div>
  </section>
</template>

<style scoped>
.header h1 { font-size: 20px; margin: 8px 0; }
.stream { margin-left: auto; font-size: 12px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.grid .card { min-width: 0; }
.deliverable { padding: 0; overflow: hidden; }
.deliverable-head {
  display: flex; align-items: baseline; gap: 12px; padding: 18px 20px 14px;
  border-bottom: 1px solid var(--rule);
}
.deliverable-head h2 { margin: 0; font-size: 18px; }
.deliverable-head .trace { margin-left: auto; font-size: 13px; }
.checks { padding: 8px 20px; border-bottom: 1px solid var(--rule); }
.check { padding: 8px 0; }
.check + .check { border-top: 1px dashed var(--rule); }
.check summary { display: flex; align-items: center; gap: 10px; cursor: pointer; list-style: none; }
.check summary::-webkit-details-marker { display: none; }
.check pre { margin-top: 8px; max-height: 260px; }
.mark { font-size: 12px; font-weight: 600; padding: 1px 8px; border-radius: 4px; border: 1px solid; }
.mark.ok { color: var(--pass); border-color: #b9dcc8; background: var(--pass-weak); }
.mark.ng { color: var(--seal); border-color: #efc4bf; background: var(--seal-weak); }
.deliver-grid { display: grid; grid-template-columns: minmax(0, 1fr) 360px; }
.deliver-files { padding: 18px 20px 20px; min-width: 0; }
.deliver-review { padding: 18px 20px 20px; border-left: 1px solid var(--rule); background: var(--paper-2); }
.deliver-files h3, .deliver-review h3 { margin-bottom: 10px; }
@media (max-width: 1000px) {
  .deliver-grid { grid-template-columns: 1fr; }
  .deliver-review { border-left: 0; border-top: 1px solid var(--rule); }
}
@media (max-width: 1000px) { .grid { grid-template-columns: 1fr; } }
.office-card { display: flex; flex-direction: column; gap: 16px; }
</style>
