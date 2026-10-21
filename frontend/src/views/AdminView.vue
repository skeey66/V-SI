<script setup>
import { ref, onMounted } from 'vue'
import {
  getConfig, updateConfig, getSummary, listRuns, listFeedback,
  getPrompt, pushPrompt, activatePrompt,
} from '../api/client.js'
import StatusBadge from '../components/StatusBadge.vue'
import SealStamp from '../components/SealStamp.vue'
import { formatTime, formatSeconds, formatCost, formatNumber } from '../utils/format.js'

const config = ref(null)
const form = ref({ rag_enabled: false, rag_stages: [], max_revisions: 3 }) // 설정 편집용 사본

// RAG 단계: 누가 언제 어떤 지식을 찾는가
const RAG_STAGES = [
  { id: 'testdesign', label: '테스트 설계', who: '기획 · 테스트 작성 전' },
  { id: 'securecoding', label: '시큐어 코딩', who: '개발 · 구현 전' },
  { id: 'qa', label: '테스트 실패 진단', who: 'QA · 불합격 후' },
  { id: 'security', label: '보안 조치', who: '보안 · 불합격 후' },
]
const summary = ref(null)
const runs = ref(null) // null = 아직 불러오는 중
const feedback = ref(null) // 최근 검수 의견 (null = 불러오는 중)
const prompt = ref(null)
const template = ref('') // 새 프롬프트 버전 입력란
const errors = ref({}) // 영역별 오류 메시지 { config: '...', ... }
const message = ref('') // 성공 알림
const loading = ref(false)

// 영역 하나를 불러오고, 실패하면 그 영역에만 오류를 표시한다
async function loadPart(key, fn) {
  try {
    await fn()
    delete errors.value[key]
  } catch (e) {
    errors.value[key] = e.message
  }
}

async function loadAll() {
  loading.value = true
  message.value = ''
  await Promise.all([
    loadPart('config', async () => {
      config.value = await getConfig()
      form.value = {
        rag_enabled: config.value.rag_enabled,
        rag_stages: [...(config.value.rag_stages || [])],
        max_revisions: config.value.max_revisions,
      }
    }),
    loadPart('summary', async () => (summary.value = await getSummary())),
    loadPart('runs', async () => (runs.value = await listRuns(50))),
    loadPart('feedback', async () => (feedback.value = await listFeedback(30))),
    loadPart('prompt', loadPrompt),
  ])
  loading.value = false
}

async function loadPrompt() {
  prompt.value = await getPrompt()
  if (!template.value) template.value = prompt.value.local_default
}

async function saveConfig() {
  await loadPart('config', async () => {
    config.value = await updateConfig({
      rag_enabled: form.value.rag_enabled,
      rag_stages: form.value.rag_stages,
      max_revisions: Number(form.value.max_revisions),
    })
    message.value = '설정을 저장했습니다.'
  })
}

async function pushNewVersion() {
  await loadPart('prompt', async () => {
    const res = await pushPrompt(template.value)
    message.value = `새 버전을 올렸습니다: ${res.commit_hash.slice(0, 8)}`
    await loadPrompt()
  })
}

// commitHash 가 null 이면 로컬 기본값 사용
async function activate(commitHash) {
  await loadPart('prompt', async () => {
    const res = await activatePrompt(commitHash)
    prompt.value.active_commit = res.active_commit
    message.value = commitHash ? `${commitHash.slice(0, 8)} 버전을 활성화했습니다.` : '로컬 기본값을 사용합니다.'
  })
}

// 해시는 짧은/긴 형태가 섞일 수 있으므로 앞부분이 같으면 같은 것으로 본다
function isActive(hash) {
  const a = prompt.value?.active_commit
  return !!a && (a.startsWith(hash) || hash.startsWith(a))
}

// success_rate 가 0~1 이면 백분율로 바꿔서 표시
function percent(rate) {
  if (rate === null || rate === undefined) return '-'
  const p = rate <= 1 ? rate * 100 : rate
  return `${p.toFixed(1)}%`
}

const feedbackLabel = (score) => (score === 1 ? '승인' : score === 0 ? '보완 요청' : '-')

onMounted(loadAll)
</script>

<template>
  <div class="row" style="justify-content: space-between; margin-bottom: 12px">
    <h1 style="margin: 0">관리자</h1>
    <button :disabled="loading" @click="loadAll">{{ loading ? '불러오는 중...' : '새로고침' }}</button>
  </div>
  <p v-if="message" class="pass">{{ message }}</p>

  <!-- 설정 -->
  <section class="card">
    <h2>설정</h2>
    <div v-if="errors.config" class="error-box">{{ errors.config }}</div>
    <div v-if="config" class="config">
      <div><span class="muted">모델</span> <code>{{ config.model }}</code></div>
      <div>
        <span class="muted">LangSmith 프로젝트</span> <code>{{ config.langsmith_project }}</code>
        <a v-if="config.langsmith_url" :href="config.langsmith_url" target="_blank" rel="noopener">LangSmith 열기 ↗</a>
      </div>
      <label class="row"><input v-model="form.rag_enabled" type="checkbox" /> RAG(지식 검색) 사용</label>
      <fieldset class="stages" :disabled="!form.rag_enabled">
        <legend class="muted">검색 단계</legend>
        <label v-for="st in RAG_STAGES" :key="st.id" class="stage">
          <input v-model="form.rag_stages" type="checkbox" :value="st.id" />
          <b>{{ st.label }}</b> <span class="muted">{{ st.who }}</span>
        </label>
        <p class="muted note">테스트 설계는 테스트를 더 까다롭게 만들어 완료율이 떨어지는 것으로 측정돼 기본에서 꺼 두었습니다.</p>
      </fieldset>
      <label class="row">
        최대 수정 회차
        <input v-model="form.max_revisions" type="number" min="1" max="10" style="width: 80px" />
      </label>
      <div><button class="primary" @click="saveConfig">설정 저장</button></div>
    </div>
  </section>

  <!-- 요약 통계 -->
  <section class="card">
    <h2>실행 요약</h2>
    <div v-if="errors.summary" class="error-box">{{ errors.summary }}</div>
    <p v-if="!summary && !errors.summary" class="muted">LangSmith 에서 불러오는 중…</p>
    <div v-if="summary" class="tiles">
      <div class="tile"><span>실행 수</span><b>{{ formatNumber(summary.runs) }}</b></div>
      <div class="tile"><span>성공률</span><b>{{ percent(summary.success_rate) }}</b></div>
      <div class="tile"><span>평균 지연</span><b>{{ formatSeconds(summary.avg_latency_s) }}</b></div>
      <div class="tile"><span>p95 지연</span><b>{{ formatSeconds(summary.p95_latency_s) }}</b></div>
      <div class="tile"><span>총 토큰</span><b>{{ formatNumber(summary.total_tokens) }}</b></div>
      <div class="tile"><span>총 비용</span><b>{{ formatCost(summary.total_cost) }}</b></div>
      <div class="tile"><span>평균 비용</span><b>{{ formatCost(summary.avg_cost) }}</b></div>
      <div class="tile">
        <span>검수 승인 / 보완 요청</span><b>{{ summary.feedback?.up ?? 0 }} / {{ summary.feedback?.down ?? 0 }}</b>
      </div>
    </div>
    <div v-if="summary?.by_status" class="row" style="margin-top: 10px">
      <span v-for="(n, st) in summary.by_status" :key="st"><StatusBadge :status="st" /> {{ n }}</span>
    </div>
  </section>

  <!-- 검수 의견 -->
  <section class="card">
    <h2>검수 의견</h2>
    <div v-if="errors.feedback" class="error-box">{{ errors.feedback }}</div>
    <p v-else-if="feedback === null" class="muted">LangSmith 에서 불러오는 중…</p>
    <p v-else-if="feedback.length === 0" class="muted">
      아직 검수 의견이 없습니다. 요구사항 화면 아래 "검수 확인"에서 남길 수 있습니다.
    </p>
    <ul v-else class="reviews">
      <li v-for="f in feedback" :key="f.run_id + f.created_at" class="review">
        <SealStamp :kind="f.score === 1 ? 'approve' : 'revise'" :size="44" />
        <div class="review-body">
          <div class="review-head">
            <RouterLink v-if="f.requirement_id" :to="`/requirements/${f.requirement_id}`">{{ f.title || f.requirement_id }}</RouterLink>
            <span v-else>{{ f.title || f.run_id }}</span>
            <span class="muted">{{ formatTime(f.created_at) }}</span>
            <a v-if="f.url" :href="f.url" target="_blank" rel="noopener" class="muted">트레이스</a>
          </div>
          <div v-if="f.reasons?.length" class="review-reasons">
            <span v-for="r in f.reasons" :key="r">{{ r }}</span>
          </div>
          <p v-if="f.comment" class="review-comment">{{ f.comment }}</p>
        </div>
      </li>
    </ul>
  </section>

  <!-- 실행 목록 -->
  <section class="card">
    <h2>최근 실행 (LangSmith)</h2>
    <div v-if="errors.runs" class="error-box">{{ errors.runs }}</div>
    <div class="scroll-x runs-table">
      <table>
        <thead>
          <tr>
            <th>시작</th><th>요구사항</th><th>상태</th><th>지연</th>
            <th>토큰</th><th>비용</th><th>검수</th><th>트레이스</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="runs === null"><td colspan="8" class="muted">LangSmith 에서 불러오는 중…</td></tr>
          <tr v-else-if="runs.length === 0"><td colspan="8" class="muted">아직 실행 기록이 없습니다. 작업 요청 화면에서 요구사항을 넣어 보세요.</td></tr>
          <tr v-for="r in runs" :key="r.run_id">
            <td class="muted">{{ formatTime(r.start_time) }}</td>
            <td>
              <RouterLink v-if="r.requirement_id" :to="`/requirements/${r.requirement_id}`">{{ r.title }}</RouterLink>
              <span v-else>{{ r.title }}</span>
            </td>
            <td><StatusBadge :status="r.status" /> <span v-if="r.error" class="fail" :title="r.error">⚠</span></td>
            <td>{{ formatSeconds(r.latency_s) }}</td>
            <td>{{ formatNumber(r.total_tokens) }}</td>
            <td>{{ formatCost(r.total_cost) }}</td>
            <td :class="{ fail: r.feedback_score === 0, pass: r.feedback_score === 1 }">{{ feedbackLabel(r.feedback_score) }}</td>
            <td><a v-if="r.url" :href="r.url" target="_blank" rel="noopener">열기 ↗</a></td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <!-- 프롬프트 버전 관리 -->
  <section class="card">
    <h2>기획(planner) 프롬프트 버전</h2>
    <div v-if="errors.prompt" class="error-box">{{ errors.prompt }}</div>
    <template v-if="prompt">
      <p>
        현재 사용 중:
        <code v-if="prompt.active_commit">{{ prompt.active_commit.slice(0, 8) }}</code>
        <b v-else>로컬 기본값</b>
        <button class="small" style="margin-left: 8px" :disabled="!prompt.active_commit" @click="activate(null)">
          로컬 기본값 사용
        </button>
      </p>
      <table>
        <thead><tr><th>커밋</th><th>생성</th><th>미리보기</th><th></th></tr></thead>
        <tbody>
          <tr v-if="prompt.commits.length === 0"><td colspan="4" class="muted">Prompt Hub 에 올린 버전이 없습니다.</td></tr>
          <tr v-for="c in prompt.commits" :key="c.commit_hash" :class="{ active: isActive(c.commit_hash) }">
            <td><code>{{ c.commit_hash.slice(0, 8) }}</code></td>
            <td class="muted">{{ formatTime(c.created_at) }}</td>
            <td class="preview">{{ c.preview }}</td>
            <td class="row" style="flex-wrap: nowrap">
              <button class="small" @click="template = c.template || c.preview">불러오기</button>
              <button class="small primary" :disabled="isActive(c.commit_hash)" @click="activate(c.commit_hash)">
                {{ isActive(c.commit_hash) ? '사용 중' : '활성화' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>

      <h3 style="margin-top: 16px; font-size: 14px">새 버전 올리기</h3>
      <textarea v-model="template" rows="10" style="font-family: var(--mono); font-size: 12px"></textarea>
      <div class="row" style="margin-top: 8px">
        <button @click="template = prompt.local_default">로컬 기본값으로 채우기</button>
        <button class="primary" :disabled="!template.trim()" @click="pushNewVersion">Prompt Hub 에 push</button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.config { display: flex; flex-direction: column; gap: 10px; }
.stages { border: 1px solid var(--rule); border-radius: 6px; padding: 10px 14px; margin: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 16px; }
.stages legend { padding: 0 4px; font-size: 12.5px; }
.stages:disabled { opacity: 0.5; }
.stage { display: flex; align-items: baseline; gap: 6px; }
.stage .muted { font-size: 12.5px; }
.stages .note { grid-column: 1 / -1; margin: 4px 0 0; font-size: 12px; }
.config a { margin-left: 8px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
.tile { border: 1px solid var(--border); border-radius: 6px; padding: 10px; display: flex; flex-direction: column; gap: 4px; }
.tile span { color: var(--muted); font-size: 12px; }
.tile b { font-size: 18px; }
.scroll-x { overflow-x: auto; }
/* 실행이 쌓여도 페이지가 끝없이 길어지지 않게 — 머리줄은 고정 */
.runs-table { max-height: 520px; overflow-y: auto; border: 1px solid var(--rule); border-radius: 6px; }
.runs-table th { position: sticky; top: 0; z-index: 1; }
.preview { max-width: 480px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--muted); }
tr.active { background: var(--ok-weak); }
.reviews { list-style: none; margin: 0; padding: 0; }
.review { display: flex; gap: 14px; align-items: flex-start; padding: 12px 0; }
.review + .review { border-top: 1px solid var(--rule); }
.review-body { min-width: 0; flex: 1; }
.review-head { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
.review-head a:first-child, .review-head span:first-child { font-weight: 600; }
.review-head .muted { font-size: 12.5px; }
.review-reasons { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.review-reasons span {
  font-size: 12px; padding: 1px 9px; border-radius: 999px; color: var(--seal); background: var(--seal-weak);
}
.review-comment { margin: 6px 0 0; white-space: pre-wrap; }
</style>
