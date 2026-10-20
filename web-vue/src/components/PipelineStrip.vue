<script setup>
import { AGENT_LABELS } from '../utils/format.js'

// states: { planner: 'idle' | 'active' | 'wait' | 'done' | 'fail', ... }
defineProps({ states: { type: Object, required: true } })
</script>

<template>
  <div class="strip">
    <div class="node" :class="states.planner">{{ AGENT_LABELS.planner }}</div>
    <span class="arrow">→</span>
    <div class="node" :class="states.gate">{{ AGENT_LABELS.gate }}</div>
    <span class="arrow">→</span>
    <div class="node" :class="states.dev">{{ AGENT_LABELS.dev }}</div>
    <span class="arrow">→</span>
    <!-- QA 와 보안은 병렬로 실행되므로 위아래로 쌓아서 표시 -->
    <div class="parallel">
      <div class="node" :class="states.qa">{{ AGENT_LABELS.qa }}</div>
      <div class="node" :class="states.security">{{ AGENT_LABELS.security }}</div>
    </div>
    <span class="arrow">→</span>
    <div class="node" :class="states.judge">{{ AGENT_LABELS.judge }}</div>
  </div>
</template>

<style scoped>
.strip { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.parallel { display: flex; flex-direction: column; gap: 4px; }
.arrow { color: var(--muted); }
.node {
  min-width: 72px; text-align: center; padding: 8px 12px; border-radius: 6px;
  border: 2px solid var(--border); background: #fafbfc; color: var(--muted); font-weight: 600;
}
.node.active { border-color: var(--primary); color: var(--primary); background: var(--primary-weak); animation: pulse 1.2s infinite; }
.node.wait { border-color: var(--warn); color: var(--warn); background: var(--warn-weak); }
.node.done { border-color: var(--ok); color: var(--ok); background: var(--ok-weak); }
.node.fail { border-color: var(--fail); color: var(--fail); background: var(--fail-weak); }
@keyframes pulse { 50% { opacity: 0.55; } }
</style>
