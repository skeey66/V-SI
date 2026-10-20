<script setup>
// 3D 사무실 — 에이전트가 자리에서 일하고, 일이 넘어가면 서류를 들고 걸어간다.
//
// 장면(OfficeScene.ts)과 상태 계산(officeState.ts)은 기존 React 화면에서 그대로
// 가져왔다. three.js 코드라 프레임워크와 무관하다. Vue 는 캔버스를 놓고 장면을
// 만들고/치우기만 하고, 그릴 상태는 SSE 이벤트를 adapter.js 로 바꿔 리듀서로 접는다.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { OfficeScene } from '../office/OfficeScene'
import { initialOfficeState, reduceOffice } from '../office/officeState'
import { toOfficeEvents, toOfficeStream } from '../office/adapter.js'
import '../office/Office.css'

const props = defineProps({
  events: { type: Array, required: true }, // token 을 뺀 SSE 이벤트
  outputs: { type: Object, default: () => ({}) }, // 에이전트별 스트리밍 출력
  finished: { type: Boolean, default: false }, // 끝난 실행이면 "다시 보기" 를 보인다
})

const TEAM = { planner: '기획팀', dev: '개발팀', qa: 'QA팀', security: '보안팀' }

const host = ref(null)
const error = ref(null)
const picked = ref(null)
let scene = null

// ── 다시 보기 ──
// gpt-4o-mini 로는 한 요구사항이 20초 안팎에 끝나서, 걸어가 서류를 넘기는 애니메이션
// (3~4초)이 다음 이벤트에 덮여 거의 안 보인다. 끝난 실행의 이벤트를 사람이 볼 수
// 있는 속도로 하나씩 다시 흘려 준다. null 이면 실시간(전체 이벤트)이다.
const replayCount = ref(null)
let replayTimer = null

const shown = computed(() => (replayCount.value === null ? props.events : props.events.slice(0, replayCount.value)))

// 이벤트 전체를 매번 처음부터 접는다 — 한 실행의 이벤트는 수십 개라 비용이 없다.
const state = computed(() => toOfficeStream(shown.value).reduce(reduceOffice, initialOfficeState()))

function mountScene() {
  try {
    scene = new OfficeScene(host.value)
  } catch (e) {
    error.value = String(e) // WebGL 이 없는 환경
    return
  }
  scene.onPick = (agent) => (picked.value = agent)
  scene.start()
  scene.apply(state.value)
}

function replay() {
  clearTimeout(replayTimer)
  // 장면은 이미 재생한 서류 전달을 기억한다 — 처음부터 다시 그리려면 새로 만든다.
  scene?.dispose()
  replayCount.value = 0
  mountScene()
  const step = () => {
    // 사무실에 변화를 주는 다음 이벤트까지 건너뛴다
    let i = replayCount.value
    while (i < props.events.length && toOfficeEvents(props.events[i]).length === 0) i++
    if (i >= props.events.length) {
      replayCount.value = null
      return
    }
    replayCount.value = i + 1
    const walks = toOfficeEvents(props.events[i]).some((e) => e.event_type === 'task_submitted')
    replayTimer = setTimeout(step, walks ? 4000 : 1500)
  }
  replayTimer = setTimeout(step, 800)
}

onMounted(mountScene)

watch(state, (s) => scene?.apply(s))

onBeforeUnmount(() => {
  clearTimeout(replayTimer)
  scene?.dispose()
  scene = null
})

// 캐릭터를 클릭하면: 그 팀의 마지막 판정과 출력
const pickedVerdict = computed(() => {
  if (!picked.value) return null
  return [...props.events].reverse().find((e) => e.type === 'verdict' && e.agent === picked.value) || null
})
const pickedOutput = computed(() => (picked.value ? (props.outputs[picked.value] || '').slice(-3000) : ''))
</script>

<template>
  <div class="vsi-office office-theme">
    <div ref="host" class="vsi-office-canvas" />
    <div v-if="error" class="vsi-office-error">
      3D 화면을 열지 못했습니다. 아래 타임라인은 그대로 쓸 수 있습니다.
      <div class="vsi-office-error-detail">{{ error }}</div>
    </div>

    <aside v-if="picked" class="vsi-art">
      <div class="vsi-art-head">
        <b>{{ TEAM[picked] }}</b>
        <span class="vsi-art-rev">{{ state.revision }}회차</span>
        <button class="vsi-art-close" aria-label="닫기" @click="picked = null">×</button>
      </div>
      <div class="vsi-art-body">
        <p v-if="pickedVerdict" class="vsi-art-summary">
          {{ pickedVerdict.verdict }} (exit {{ pickedVerdict.exit_code }}) — {{ pickedVerdict.summary }}
        </p>
        <div v-if="pickedOutput" class="vsi-art-file">
          <div class="vsi-art-file-name">에이전트 출력</div>
          <pre>{{ pickedOutput }}</pre>
        </div>
        <p v-if="!pickedVerdict && !pickedOutput" class="vsi-art-msg">아직 이 팀의 출력이 없습니다.</p>
      </div>
    </aside>

    <div v-if="!error" class="vsi-office-hint">
      캐릭터를 클릭하면 출력 · 드래그로 둘러보기 · 휠로 줌
      <button class="vsi-office-reset" @click="scene?.resetCamera()">시점 초기화</button>
      <button v-if="finished" class="vsi-office-reset" @click="replay">
        {{ replayCount === null ? '▶ 다시 보기' : '▶ 재생 중…' }}
      </button>
    </div>
  </div>
</template>

<style>
/* 기존 화면(어두운 테마)의 변수 이름을 사무실 영역 안에서만 정의한다. */
.office-theme {
  --line: #262a33;
  --line-strong: #3a3f4b;
  --text: #e6e8ee;
  --text-muted: #9aa1ad;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: var(--text);
}
</style>
