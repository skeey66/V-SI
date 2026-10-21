<script setup>
import { ref, onMounted } from 'vue'
import { listFiles, getFile } from '../api/client.js'

const props = defineProps({ requirementId: { type: String, required: true } })

const files = ref([])
const current = ref(null) // { path, content }
const error = ref('')

onMounted(async () => {
  try {
    files.value = await listFiles(props.requirementId)
    if (files.value.length > 0) await open(files.value[0].path)
  } catch (e) {
    error.value = e.message
  }
})

// 파일이 어느 팀의 산출물인지 — 기획은 명세와 인수 테스트, 개발은 구현을 쓴다
function roleOf(path) {
  const name = path.split('/').pop()
  if (name.endsWith('.md')) return '명세'
  if (name.startsWith('test_') || name.endsWith('_test.py')) return '인수 테스트'
  return '구현'
}

async function open(path) {
  error.value = ''
  try {
    current.value = await getFile(props.requirementId, path)
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <div class="viewer">
    <div v-if="error" class="error-box">{{ error }}</div>
    <ul class="list">
      <li v-if="files.length === 0" class="muted">남아 있는 파일이 없습니다. 작업 공간이 초기화되었을 수 있습니다.</li>
      <li v-for="f in files" :key="f.path">
        <button class="file" :class="{ on: current?.path === f.path }" @click="open(f.path)">
          <span class="name">{{ f.path }}</span>
          <span class="role">{{ roleOf(f.path) }}</span>
        </button>
      </li>
    </ul>
    <div class="content">
      <pre v-if="current">{{ current.content }}</pre>
      <p v-else-if="files.length" class="muted">파일을 고르면 내용이 여기에 보입니다.</p>
    </div>
  </div>
</template>

<style scoped>
.viewer { display: grid; grid-template-columns: 200px minmax(0, 1fr); gap: 12px; }
.list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.file {
  width: 100%; display: flex; flex-direction: column; align-items: flex-start; gap: 0;
  padding: 7px 10px; text-align: left; border-color: transparent; background: transparent;
}
.file:hover:not(:disabled) { border-color: var(--rule); background: var(--paper-2); }
.file.on { border-color: var(--rule); background: var(--paper); box-shadow: inset 3px 0 0 var(--ink); }
.file .name { font-family: var(--mono); font-size: 12.5px; word-break: break-all; }
.file .role { font-size: 11.5px; color: var(--ink-2); }
.content { min-width: 0; }
.content pre { max-height: 460px; }
@media (max-width: 800px) { .viewer { grid-template-columns: 1fr; } }
</style>
