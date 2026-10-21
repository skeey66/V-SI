<script setup>
// 검수 도장. 승인은 둥근 도장, 보완 요청은 네모 도장이고 둘 다 인주색이다.
// press 가 true 면 한 번 "꾹" 찍히는 동작을 보여준다 (사용자가 제출했을 때만).
defineProps({
  kind: { type: String, default: 'approve' }, // 'approve' | 'revise'
  size: { type: Number, default: 112 },
  press: { type: Boolean, default: false },
})
</script>

<template>
  <svg
    class="seal" :class="{ press }" :width="size" :height="size" viewBox="0 0 100 100"
    role="img" :aria-label="kind === 'approve' ? '검수 승인 도장' : '보완 요청 도장'"
  >
    <template v-if="kind === 'approve'">
      <circle cx="50" cy="50" r="45" fill="none" stroke="currentColor" stroke-width="4.5" />
      <circle cx="50" cy="50" r="38" fill="none" stroke="currentColor" stroke-width="1.6" />
      <text x="50" y="45" text-anchor="middle">검수</text>
      <text x="50" y="71" text-anchor="middle">승인</text>
    </template>
    <template v-else>
      <rect x="7" y="7" width="86" height="86" rx="6" fill="none" stroke="currentColor" stroke-width="4.5" />
      <rect x="14" y="14" width="72" height="72" rx="3" fill="none" stroke="currentColor" stroke-width="1.6" />
      <text x="50" y="46" text-anchor="middle">보완</text>
      <text x="50" y="72" text-anchor="middle">요청</text>
    </template>
  </svg>
</template>

<style scoped>
.seal {
  color: var(--seal);
  transform: rotate(-9deg);
  opacity: 0.9;
  mix-blend-mode: multiply; /* 종이에 스민 인주처럼 */
  flex-shrink: 0;
}
.seal text {
  font-family: var(--font-seal);
  font-weight: 800;
  font-size: 22px;
  fill: currentColor;
  letter-spacing: 2px;
}
.press { animation: press 340ms cubic-bezier(0.2, 0.9, 0.3, 1.2) both; }
@keyframes press {
  0% { transform: rotate(-9deg) scale(1.7); opacity: 0; }
  60% { transform: rotate(-9deg) scale(0.94); opacity: 0.95; }
  100% { transform: rotate(-9deg) scale(1); opacity: 0.9; }
}
</style>
