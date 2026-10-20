<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { createRequirement, listRequirements } from '../api/client.js'
import StatusBadge from '../components/StatusBadge.vue'
import { formatTime } from '../utils/format.js'

const router = useRouter()

const EXAMPLES = [
  '장바구니에 담긴 상품 금액을 모두 합치고, 합계가 5만원 이상이면 배송비 3000원을 빼주세요',
  '문자열이 회문인지 판별하는 함수',
  '사용자가 입력한 파일명을 받아 그 파일의 줄 수를 세는 함수',
]

const title = ref('')
const submitting = ref(false)
const items = ref([])
const error = ref('')

async function load() {
  try {
    items.value = await listRequirements()
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
}

async function submit() {
  if (!title.value.trim()) return
  submitting.value = true
  error.value = ''
  try {
    const res = await createRequirement(title.value.trim())
    router.push(`/requirements/${res.requirement_id}`) // 상세 화면으로 이동
  } catch (e) {
    error.value = e.message
  } finally {
    submitting.value = false
  }
}

// 진행 중인 요청 상태가 바뀌므로 5초마다 목록을 새로 불러온다
let timer = null
onMounted(() => {
  load()
  timer = setInterval(load, 5000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <section class="card">
    <h2>새 요구사항</h2>
    <textarea
      v-model="title"
      rows="3"
      placeholder="만들고 싶은 기능을 자연어로 적어주세요"
      @keydown.ctrl.enter="submit"
      @keydown.meta.enter="submit"
    ></textarea>
    <div class="row" style="margin-top: 8px">
      <span class="muted">예시:</span>
      <button v-for="ex in EXAMPLES" :key="ex" class="small" @click="title = ex">
        {{ ex.length > 24 ? ex.slice(0, 24) + '…' : ex }}
      </button>
      <button
        class="primary"
        style="margin-left: auto"
        :disabled="submitting || !title.trim()"
        @click="submit"
      >
        {{ submitting ? '제출 중...' : '제출' }}
      </button>
    </div>
  </section>

  <div v-if="error" class="error-box">{{ error }}</div>

  <section class="card">
    <div class="row" style="justify-content: space-between">
      <h2>요청 목록</h2>
      <button class="small" @click="load">새로고침</button>
    </div>
    <table>
      <thead>
        <tr><th>요구사항</th><th>상태</th><th>회차</th><th>생성</th><th>갱신</th></tr>
      </thead>
      <tbody>
        <tr v-if="items.length === 0">
          <td colspan="5" class="muted">아직 요청이 없습니다.</td>
        </tr>
        <tr v-for="r in items" :key="r.requirement_id">
          <td><RouterLink :to="`/requirements/${r.requirement_id}`">{{ r.title }}</RouterLink></td>
          <td><StatusBadge :status="r.status" /></td>
          <td>{{ r.revision }}회차</td>
          <td class="muted">{{ formatTime(r.created_at) }}</td>
          <td class="muted">{{ formatTime(r.updated_at) }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
