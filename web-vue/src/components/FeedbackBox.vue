<script setup>
import { ref } from 'vue'
import { sendFeedback } from '../api/client.js'

const props = defineProps({ requirementId: { type: String, required: true } })

const score = ref(null) // 1 = 👍, 0 = 👎
const comment = ref('')
const sent = ref(false)
const error = ref('')

async function submit() {
  error.value = ''
  try {
    await sendFeedback(props.requirementId, score.value, comment.value.trim())
    sent.value = true
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <div>
    <p v-if="sent" class="pass">피드백 감사합니다! (LangSmith 에 기록되었습니다)</p>
    <template v-else>
      <div class="row">
        <button :class="{ primary: score === 1 }" @click="score = 1">👍 좋아요</button>
        <button :class="{ primary: score === 0 }" @click="score = 0">👎 아쉬워요</button>
      </div>
      <textarea v-model="comment" rows="2" placeholder="의견 (선택)" style="margin-top: 8px"></textarea>
      <div v-if="error" class="error-box" style="margin-top: 8px">{{ error }}</div>
      <button class="primary" style="margin-top: 8px" :disabled="score === null" @click="submit">
        피드백 보내기
      </button>
    </template>
  </div>
</template>
