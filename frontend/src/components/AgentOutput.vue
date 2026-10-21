<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { AGENT_LABELS } from '../utils/format.js'

// outputs: { planner: '누적 텍스트', dev: '...', ... }
// active: 지금 실행 중인 에이전트 이름 (탭 자동 전환용)
const props = defineProps({
  outputs: { type: Object, required: true },
  active: { type: String, default: '' },
})

const agents = ['planner', 'dev', 'qa', 'security']
const selected = ref('planner')
const follow = ref(true) // 실행 중인 에이전트 탭을 자동으로 따라갈지

// 새로운 에이전트가 시작되면 그 탭으로 이동
watch(
  () => props.active,
  (a) => {
    if (follow.value && a && agents.includes(a)) selected.value = a
  },
)

const text = computed(() => props.outputs[selected.value] || '')

// 텍스트가 늘어나면 맨 아래로 자동 스크롤
const pre = ref(null)
watch(text, async () => {
  await nextTick()
  if (pre.value) pre.value.scrollTop = pre.value.scrollHeight
})

function choose(a) {
  selected.value = a
  follow.value = false // 사용자가 직접 고르면 자동 전환을 끈다
}
</script>

<template>
  <div>
    <div class="tabs">
      <button
        v-for="a in agents"
        :key="a"
        class="small"
        :class="{ on: selected === a }"
        @click="choose(a)"
      >
        {{ AGENT_LABELS[a] }}
        <span v-if="active === a" class="dot">●</span>
        <span class="muted">{{ (outputs[a] || '').length }}자</span>
      </button>
      <label class="follow">
        <input v-model="follow" type="checkbox" /> 실행 중 에이전트 따라가기
      </label>
    </div>
    <pre ref="pre" class="out">{{ text || '(아직 출력 없음)' }}</pre>
  </div>
</template>

<style scoped>
.tabs { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: 8px; }
.tabs button.on { background: var(--primary); color: #fff; border-color: var(--primary); }
.tabs button.on .muted { color: #dfe7ff; }
.dot { color: var(--ok); }
.follow { margin-left: auto; font-size: 12px; color: var(--muted); }
.out { height: 420px; overflow-y: auto; }
</style>
