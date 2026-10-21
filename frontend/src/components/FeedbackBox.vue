<script setup>
// 검수 확인 — 결과물을 받은 사람이 승인하거나 보완을 요청한다.
// 제출하면 LangSmith 피드백(key=user_score)으로 기록되고, 새로고침해도 도장이 남는다.
import { computed, onMounted, ref } from 'vue'
import { getFeedback, sendFeedback } from '../api/client.js'
import { formatTime } from '../utils/format.js'
import SealStamp from './SealStamp.vue'

const props = defineProps({
  requirementId: { type: String, required: true },
  traceUrl: { type: String, default: '' },
})

// 서버(FEEDBACK_REASONS)와 같은 목록
const REASONS = ['요구사항과 다름', '테스트 부족', '코드 품질', '보안 우려', '너무 느림']

const score = ref(null) // 1 = 승인, 0 = 보완 요청
const reasons = ref([])
const comment = ref('')
const saved = ref(null) // 제출된 검수 의견
const justPressed = ref(false) // 방금 제출했을 때만 도장 찍는 동작
const sending = ref(false)
const error = ref('')

onMounted(async () => {
  try {
    saved.value = (await getFeedback(props.requirementId)).feedback
  } catch {
    // 이전 의견을 못 읽어도 새로 남길 수는 있다
  }
})

const placeholder = computed(() =>
  score.value === 0 ? '어떤 점을 고쳐야 하나요? (선택)' : '좋았던 점이나 남길 말 (선택)',
)

function toggleReason(r) {
  reasons.value = reasons.value.includes(r) ? reasons.value.filter((x) => x !== r) : [...reasons.value, r]
}

async function submit() {
  sending.value = true
  error.value = ''
  try {
    saved.value = await sendFeedback(props.requirementId, {
      score: score.value,
      reasons: score.value === 0 ? reasons.value : [],
      comment: comment.value.trim(),
    })
    justPressed.value = true
  } catch (e) {
    error.value = `검수 결과를 보내지 못했습니다. ${e.message}`
  } finally {
    sending.value = false
  }
}

function redo() {
  score.value = saved.value?.score ?? null
  reasons.value = [...(saved.value?.reasons || [])]
  comment.value = saved.value?.comment || ''
  saved.value = null
  justPressed.value = false
}
</script>

<template>
  <div class="review">
    <!-- 제출된 상태: 도장 -->
    <div v-if="saved" class="stamped">
      <SealStamp :kind="saved.score === 1 ? 'approve' : 'revise'" :press="justPressed" />
      <div class="stamped-body">
        <h3>{{ saved.score === 1 ? '결과물을 승인했습니다' : '보완을 요청했습니다' }}</h3>
        <ul v-if="saved.reasons?.length" class="reason-list">
          <li v-for="r in saved.reasons" :key="r">{{ r }}</li>
        </ul>
        <blockquote v-if="saved.comment">{{ saved.comment }}</blockquote>
        <p class="meta">
          <span v-if="saved.created_at">{{ formatTime(saved.created_at) }} · </span>
          <a v-if="traceUrl" :href="traceUrl" target="_blank" rel="noopener">LangSmith 에 기록됨</a>
          <span v-else>LangSmith 에 기록됨</span>
        </p>
        <button class="small" @click="redo">다시 검수하기</button>
      </div>
    </div>

    <!-- 작성 중 -->
    <template v-else>
      <p class="muted intro">결과물을 확인하고 승인하거나 보완을 요청해 주세요.</p>
      <div class="choices" role="radiogroup" aria-label="검수 결과">
        <button
          role="radio" :aria-checked="score === 1" class="choice" :class="{ on: score === 1 }"
          @click="score = 1"
        >
          <b>승인</b>
          <span>문제없이 동작합니다</span>
        </button>
        <button
          role="radio" :aria-checked="score === 0" class="choice" :class="{ on: score === 0, revise: score === 0 }"
          @click="score = 0"
        >
          <b>보완 요청</b>
          <span>고칠 점이 있습니다</span>
        </button>
      </div>

      <fieldset v-if="score === 0" class="reasons">
        <legend>무엇이 아쉬웠나요?</legend>
        <button
          v-for="r in REASONS" :key="r" class="chip" :class="{ on: reasons.includes(r) }"
          :aria-pressed="reasons.includes(r)" @click="toggleReason(r)"
        >{{ r }}</button>
      </fieldset>

      <textarea v-if="score !== null" v-model="comment" rows="3" :placeholder="placeholder" maxlength="1000" />
      <div v-if="error" class="error-box">{{ error }}</div>
      <button class="primary submit" :disabled="score === null || sending" @click="submit">
        {{ sending ? '보내는 중…' : '검수 결과 제출' }}
      </button>
    </template>
  </div>
</template>

<style scoped>
.review { display: flex; flex-direction: column; gap: 12px; }
.intro { margin: 0; }

.choices { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.choice {
  display: flex; flex-direction: column; align-items: flex-start; gap: 2px;
  padding: 14px 16px; text-align: left; border-radius: 6px;
}
.choice b { font-size: 15px; }
.choice span { font-size: 12.5px; color: var(--ink-2); }
.choice.on { border-color: var(--pen); background: var(--pen-weak); box-shadow: inset 0 0 0 1px var(--pen); }
.choice.on.revise { border-color: var(--seal); background: var(--seal-weak); box-shadow: inset 0 0 0 1px var(--seal); }

.reasons { border: 0; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: 6px; }
.reasons legend { font-size: 13px; color: var(--ink-2); margin-bottom: 6px; padding: 0; }
.chip { border-radius: 999px; padding: 4px 12px; font-size: 13px; }
.chip.on { border-color: var(--seal); color: var(--seal); background: var(--seal-weak); }

.submit { align-self: flex-start; padding: 9px 18px; }

.stamped { display: flex; gap: 20px; align-items: flex-start; }
.stamped-body { min-width: 0; }
.stamped-body h3 { font-size: 16px; margin-bottom: 6px; }
.reason-list { display: flex; flex-wrap: wrap; gap: 6px; list-style: none; padding: 0; margin: 0 0 8px; }
.reason-list li {
  font-size: 12.5px; padding: 2px 10px; border-radius: 999px;
  color: var(--seal); background: var(--seal-weak);
}
blockquote {
  margin: 0 0 8px; padding: 2px 0 2px 12px; border-left: 3px solid var(--rule);
  color: var(--ink); white-space: pre-wrap;
}
.meta { font-size: 12.5px; color: var(--ink-2); }

@media (max-width: 640px) {
  .choices { grid-template-columns: 1fr; }
  .stamped { flex-direction: column; }
}
</style>
