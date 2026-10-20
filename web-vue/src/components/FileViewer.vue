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
      <li v-if="files.length === 0" class="muted">파일 없음</li>
      <li v-for="f in files" :key="f.path">
        <a href="#" :class="{ on: current?.path === f.path }" @click.prevent="open(f.path)">
          📄 {{ f.path }}
        </a>
      </li>
    </ul>
    <div class="content">
      <div v-if="current" class="muted path">{{ current.path }}</div>
      <pre v-if="current">{{ current.content }}</pre>
      <p v-else class="muted">왼쪽에서 파일을 선택하세요.</p>
    </div>
  </div>
</template>

<style scoped>
.viewer { display: grid; grid-template-columns: 220px 1fr; gap: 12px; }
.list { list-style: none; margin: 0; padding: 0; }
.list li { padding: 3px 0; word-break: break-all; }
.list a.on { font-weight: 700; }
.content { min-width: 0; }
.content pre { max-height: 480px; }
.path { margin-bottom: 4px; font-family: var(--mono); }
@media (max-width: 800px) { .viewer { grid-template-columns: 1fr; } }
</style>
