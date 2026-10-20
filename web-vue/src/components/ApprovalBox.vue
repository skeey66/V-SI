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
    <h2>✋ 사람 승인이 필요합니다</h2>
    <p class="muted">기획 결과에 테스트가 없어 그래프가 멈췄습니다. 계속 진행할지 결정해주세요.</p>
    <pre>{{ typeof detail === 'string' ? detail : JSON.stringify(detail, null, 2) }}</pre>
    <div v-if="error" class="error-box">{{ error }}</div>
    <div class="row" style="margin-top: 10px">
      <button class="primary" :disabled="sending" @click="answer(true)">승인하고 계속</button>
      <button class="danger" :disabled="sending" @click="answer(false)">중단</button>
    </div>
  </div>
</template>

<style scoped>
.approval { border: 2px solid var(--warn); background: var(--warn-weak); }
</style>
