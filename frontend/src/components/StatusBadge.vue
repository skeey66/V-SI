<script setup>
import { STATUS_LABELS } from '../utils/format.js'

defineProps({ status: { type: String, default: '' } })
</script>

<template>
  <!-- 상태별 색은 아래 CSS 클래스(s-상태)로 지정 -->
  <span class="badge" :class="'s-' + status">{{ STATUS_LABELS[status] || status || '-' }}</span>
</template>

<style scoped>
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 999px;
  font-size: 12px; font-weight: 600; background: #eef0f3; color: var(--muted); white-space: nowrap;
}
.s-planned { background: #eef0f3; color: #4b5563; }
.s-implementing, .s-verifying { background: var(--primary-weak); color: var(--primary); }
.s-remediating, .s-blocked { background: var(--warn-weak); color: var(--warn); }
.s-accepted { background: var(--ok-weak); color: var(--ok); }
.s-escalated, .s-error { background: var(--fail-weak); color: var(--fail); }
</style>
