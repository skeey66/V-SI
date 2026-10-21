<script setup>
import { ref } from 'vue'
import { approve } from '../api/client.js'

const props = defineProps({
  requirementId: { type: String, required: true },
  detail: { type: [String, Object], default: '' },
})
const emit = defineEmits(['answered'])

const sending = ref(false)
const error = ref('')

async function answer(approved) {
  sending.value = true
  error.value = ''
  try {
    await approve(props.requirementId, approved)
    emit('answered', approved)
  } catch (e) {
    error.value = e.message
  } finally {
    sending.value = false
  }
}
</script>

<template>
  <div class="card approval">
    <h2>사람 승인이 필요합니다</h2>
    <p class="muted">기획이 쓴 인수 테스트가 구현 없이도 통과해, 아무것도 검사하지 않는 것으로 보입니다. 이대로 개발을 진행할지 정해 주세요.</p>
    <pre>{{ typeof detail === 'string' ? detail : JSON.stringify(detail, null, 2) }}</pre>
    <div v-if="error" class="error-box">{{ error }}</div>
    <div class="row" style="margin-top: 10px">
      <button class="primary" :disabled="sending" @click="answer(true)">이대로 진행</button>
      <button class="danger" :disabled="sending" @click="answer(false)">작업 중단</button>
    </div>
  </div>
</template>

<style scoped>
.approval { border: 1px solid #ecd3a6; border-left: 4px solid var(--hold); background: var(--hold-weak); }
.approval pre { background: var(--paper); max-height: 220px; }
</style>
