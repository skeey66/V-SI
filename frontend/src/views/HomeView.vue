<script setup>
// 메인 화면 — 작업 지시서(요청 접수) · 작업 현황 · 처리 순서 · 작업 대장
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { createRequirement, listRequirements } from '../api/client.js'
import StatusBadge from '../components/StatusBadge.vue'
import { formatAgo, formatSeconds, statusGroup } from '../utils/format.js'

const router = useRouter()

// 결재란 — 요청 하나를 네 팀이 차례로 처리한다 (3D 사무실의 팀 색과 같다)
const TEAMS = [
  { id: 'planner', name: '기획', color: '#d4805c' },
  { id: 'dev', name: '개발', color: '#6f9fd8' },
  { id: 'qa', name: 'QA', color: '#d8b64a' },
  { id: 'security', name: '보안', color: '#79ad86' },
]

const STEPS = [
  { team: '기획팀', color: '#d4805c', title: '명세와 인수 테스트 작성', body: '요청을 읽고 무엇을 만들지 정한 뒤, 완성 여부를 판단할 테스트를 먼저 씁니다.' },
  { team: '개발팀', color: '#6f9fd8', title: '테스트를 통과하는 구현', body: '인수 테스트를 목표로 코드를 씁니다. 테스트 파일은 고칠 수 없습니다.' },
  { team: 'QA팀 · 보안팀', color: '#d8b64a', title: '실제 실행으로 검사', body: 'pytest 와 bandit 을 돌려 합격을 정합니다. 불합격이면 사유를 붙여 개발팀에 돌려보냅니다.' },
  { team: '요청자', color: '#c2352b', title: '납품 검수', body: '결과물을 확인하고 승인하거나 보완을 요청합니다. 검수 의견은 관리자 화면에 모입니다.' },
]

const EXAMPLES = [
  '장바구니 상품 금액을 모두 합치고, 합계가 5만원 이상이면 배송비 3000원을 빼주세요',
  '문자열이 회문인지 판별하는 함수 (대소문자와 공백은 무시)',
  '정수 리스트에서 두 번째로 큰 값을 돌려주는 함수, 값이 부족하면 None',
]
const MAX_LEN = 500 // 서버 제한과 같다

const title = ref('')
const submitting = ref(false)
const items = ref(null) // null = 불러오는 중
const error = ref('')
const listError = ref('')
const now = ref(Date.now() / 1000)
const input = ref(null)

const canSubmit = computed(() => title.value.trim().length >= 2 && !submitting.value)

// 작업 현황: 상태를 네 묶음으로 센다
const counts = computed(() => {
  const c = { running: 0, waiting: 0, done: 0, trouble: 0 }
  for (const r of items.value || []) c[statusGroup(r.status)]++
  return c
})
// 납품 완료 건의 평균 처리 시간 (접수 → 마지막 갱신)
const avgDone = computed(() => {
  const done = (items.value || []).filter((r) => r.status === 'accepted')
  if (!done.length) return null
  return done.reduce((s, r) => s + (r.updated_at - r.created_at), 0) / done.length
})

async function load() {
  try {
    items.value = await listRequirements()
    listError.value = ''
  } catch (e) {
    listError.value = `작업 대장을 불러오지 못했습니다. 백엔드(8200)가 떠 있는지 확인하세요. (${e.message})`
    if (items.value === null) items.value = []
  }
}

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  error.value = ''
  try {
    const res = await createRequirement(title.value.trim())
    router.push(`/requirements/${res.requirement_id}`)
  } catch (e) {
    error.value = `접수하지 못했습니다. ${e.message}`
    submitting.value = false
  }
}

function useExample(ex) {
  title.value = ex
  input.value?.focus()
}

// 진행 중인 작업의 상태가 바뀌므로 5초마다 다시 불러온다
let timer = null
onMounted(() => {
  load()
  timer = setInterval(() => {
    now.value = Date.now() / 1000
    load()
  }, 5000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="home">
    <!-- 작업 지시서 -->
    <section class="order" aria-labelledby="order-title">
      <header class="order-head">
        <div>
          <h1 id="order-title">작업 지시서</h1>
          <p class="lead">만들 기능을 적어 주시면 네 팀이 명세부터 검수까지 맡아 처리합니다.</p>
        </div>
        <table class="approval-line" aria-label="결재란">
          <tr>
            <th rowspan="2" class="approval-label">결<br />재</th>
            <th v-for="t in TEAMS" :key="t.id" :style="{ '--team': t.color }">{{ t.name }}</th>
          </tr>
          <tr><td v-for="t in TEAMS" :key="t.id" /></tr>
        </table>
      </header>

      <label for="order-body" class="field-label">요청 내용</label>
      <textarea
        id="order-body" ref="input" v-model="title" rows="4" :maxlength="MAX_LEN"
        placeholder="예) 주문 목록에서 취소된 주문을 빼고 총 매출을 계산하는 함수"
        @keydown.ctrl.enter="submit" @keydown.meta.enter="submit"
      />
      <div class="order-foot">
        <div class="examples">
          <span class="muted">이런 걸 맡길 수 있어요</span>
          <button v-for="ex in EXAMPLES" :key="ex" class="example" @click="useExample(ex)">{{ ex }}</button>
        </div>
        <div class="send">
          <span class="muted count">{{ title.length }} / {{ MAX_LEN }}</span>
          <button class="primary send-btn" :disabled="!canSubmit" @click="submit">
            {{ submitting ? '접수 중…' : '작업 맡기기' }}
          </button>
          <span class="muted hint">⌘ / Ctrl + Enter</span>
        </div>
      </div>
      <div v-if="error" class="error-box" role="alert">{{ error }}</div>
    </section>

    <!-- 작업 현황 -->
    <aside class="status" aria-label="작업 현황">
      <h2>작업 현황</h2>
      <dl>
        <div><dt>진행 중</dt><dd>{{ counts.running }}</dd></div>
        <div><dt>승인 대기</dt><dd :class="{ warn: counts.waiting }">{{ counts.waiting }}</dd></div>
        <div><dt>납품 완료</dt><dd>{{ counts.done }}</dd></div>
        <div><dt>보완 필요</dt><dd :class="{ bad: counts.trouble }">{{ counts.trouble }}</dd></div>
      </dl>
      <p class="avg">
        <span class="muted">평균 처리 시간</span>
        <b>{{ avgDone === null ? '-' : formatSeconds(avgDone) }}</b>
      </p>
      <RouterLink to="/admin" class="admin-link">비용·검수 의견은 관리자 화면에서</RouterLink>
    </aside>

    <!-- 처리 순서 -->
    <section class="steps" aria-labelledby="steps-title">
      <h2 id="steps-title">이렇게 처리됩니다</h2>
      <ol>
        <li v-for="(s, i) in STEPS" :key="s.title" :style="{ '--team': s.color }">
          <span class="step-no">{{ i + 1 }}</span>
          <span class="step-team">{{ s.team }}</span>
          <h3>{{ s.title }}</h3>
          <p>{{ s.body }}</p>
        </li>
      </ol>
    </section>

    <!-- 작업 대장 -->
    <section class="ledger card" aria-labelledby="ledger-title">
      <div class="ledger-head">
        <h2 id="ledger-title">작업 대장</h2>
        <span class="muted">5초마다 자동으로 갱신됩니다</span>
      </div>
      <div v-if="listError" class="error-box">{{ listError }}</div>
      <p v-if="items === null" class="muted">불러오는 중…</p>
      <p v-else-if="items.length === 0 && !listError" class="empty">
        아직 맡긴 작업이 없습니다. 위 작업 지시서에 첫 요청을 적어 보세요.
      </p>
      <ul v-else class="rows">
        <li v-for="r in items" :key="r.requirement_id">
          <RouterLink :to="`/requirements/${r.requirement_id}`" class="row-link">
            <span class="row-title">{{ r.title }}</span>
            <span class="row-meta">
              <StatusBadge :status="r.status" />
              <span class="muted">{{ r.revision }}회차</span>
              <span v-if="r.status === 'accepted'" class="muted">{{ formatSeconds(r.updated_at - r.created_at) }}</span>
              <span class="muted when">{{ formatAgo(r.created_at, now) }}</span>
            </span>
          </RouterLink>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.home {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  grid-template-areas: 'order status' 'steps steps' 'ledger ledger';
  gap: 16px;
}

/* ── 작업 지시서 ── */
.order {
  grid-area: order;
  background: var(--paper);
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  padding: 28px 32px 24px;
  box-shadow: 0 1px 0 var(--rule), 0 12px 32px -24px rgba(24, 33, 46, 0.35);
}
.order-head { display: flex; justify-content: space-between; gap: 24px; margin-bottom: 20px; }
.order-head h1 { font-size: 28px; letter-spacing: -0.03em; margin-bottom: 6px; }
.lead { color: var(--ink-2); font-size: 15px; margin: 0; max-width: 34em; }

/* 결재란: 사무 서류 오른쪽 위의 그 칸 */
.approval-line { width: auto; border-collapse: collapse; flex-shrink: 0; align-self: flex-start; }
.approval-line th, .approval-line td {
  border: 1px solid var(--ink-2); width: 56px; text-align: center; padding: 0;
}
.approval-line th {
  height: 24px; font-size: 12px; font-weight: 600; color: var(--ink); background: var(--paper);
  box-shadow: inset 0 3px 0 var(--team);
}
.approval-line td { height: 48px; }
.approval-line th.approval-label {
  width: 26px; height: auto; font-size: 12px; line-height: 1.5; box-shadow: none; background: var(--paper-2);
}
.approval-line tr:hover td { background: none; }

.field-label { display: block; font-weight: 600; margin-bottom: 6px; }
.order textarea { font-size: 16px; line-height: 1.6; padding: 14px 16px; min-height: 120px; }

.order-foot { display: flex; justify-content: space-between; align-items: flex-end; gap: 20px; margin-top: 14px; }
.examples { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; min-width: 0; }
.examples .muted { font-size: 12.5px; }
.example {
  border: 0; background: none; padding: 0; color: var(--pen); text-align: left; font-size: 13.5px;
  max-width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.example:hover:not(:disabled) { text-decoration: underline; text-underline-offset: 3px; }
.send { display: grid; grid-template-columns: auto auto; align-items: center; gap: 4px 12px; flex-shrink: 0; }
.send-btn { grid-row: span 2; padding: 12px 22px; font-size: 15px; }
.count, .hint { font-size: 12px; text-align: right; }
.order .error-box { margin: 14px 0 0; }

/* ── 작업 현황 ── */
.status {
  grid-area: status;
  background: var(--paper); border: 1px solid var(--rule); border-radius: var(--radius);
  padding: 22px 22px 18px; display: flex; flex-direction: column;
}
.status dl { margin: 0; }
.status dl > div {
  display: flex; justify-content: space-between; align-items: baseline;
  padding: 9px 0; border-bottom: 1px solid var(--rule);
}
.status dt { color: var(--ink-2); }
.status dd { margin: 0; font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
.status dd.warn { color: var(--hold); }
.status dd.bad { color: var(--seal); }
.avg { display: flex; justify-content: space-between; align-items: baseline; margin: 14px 0 0; }
.avg b { font-size: 15px; }
.admin-link { margin-top: auto; padding-top: 16px; font-size: 13px; }

/* ── 처리 순서 ── */
.steps { grid-area: steps; padding: 8px 0 4px; }
.steps h2 { margin-bottom: 12px; }
.steps ol { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.steps li {
  background: var(--paper); border: 1px solid var(--rule); border-radius: var(--radius);
  padding: 16px 18px; border-top: 3px solid var(--team);
}
.step-no { font-weight: 700; font-variant-numeric: tabular-nums; margin-right: 8px; }
.step-team { font-size: 12.5px; color: var(--ink-2); }
.steps h3 { margin: 8px 0 6px; font-size: 15px; }
.steps p { margin: 0; color: var(--ink-2); font-size: 13.5px; line-height: 1.6; }

/* ── 작업 대장 ── */
.ledger { grid-area: ledger; margin: 0; }
.ledger-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
.ledger-head h2 { margin: 0; }
.ledger-head .muted { font-size: 12.5px; }
.empty { color: var(--ink-2); padding: 18px 0 6px; margin: 0; }
.rows { list-style: none; margin: 0; padding: 0; }
.rows li + li { border-top: 1px solid var(--rule); }
.row-link {
  display: flex; justify-content: space-between; align-items: center; gap: 16px;
  padding: 12px 4px; color: var(--ink);
}
.row-link:hover { text-decoration: none; background: var(--paper-2); }
.row-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
.row-link:hover .row-title { color: var(--pen); }
.row-meta { display: flex; align-items: center; gap: 12px; flex-shrink: 0; font-size: 13px; }
.when { min-width: 56px; text-align: right; }

@media (max-width: 960px) {
  /* minmax(0, …): 줄바꿈 없는 예시 문장이 칸을 밀어내 가로 스크롤이 생기지 않게 */
  .home { grid-template-columns: minmax(0, 1fr); grid-template-areas: 'order' 'status' 'steps' 'ledger'; }
  .steps ol { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 640px) {
  .order { padding: 20px 18px; }
  .order-head { flex-direction: column; }
  .order-foot { flex-direction: column; align-items: stretch; }
  .send { grid-template-columns: 1fr; }
  .send-btn { grid-row: auto; }
  .count, .hint { text-align: left; }
  .steps ol { grid-template-columns: 1fr; }
  .row-link { flex-direction: column; align-items: flex-start; gap: 6px; }
}
</style>
