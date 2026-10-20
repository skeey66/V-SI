// 화면 표시용 작은 도우미 함수들

// 상태값 -> 한글 라벨
export const STATUS_LABELS = {
  planned: '기획됨',
  implementing: '구현 중',
  verifying: '검증 중',
  remediating: '수정 중',
  blocked: '승인 대기',
  accepted: '완료',
  escalated: '에스컬레이션',
  error: '오류',
}

// 더 이상 진행되지 않는 상태
export const TERMINAL = ['accepted', 'escalated', 'error']

export const AGENT_LABELS = {
  planner: '기획',
  dev: '개발',
  qa: 'QA',
  security: '보안',
  gate: '게이트',
  judge: '판정',
  approval: '승인',
}

// ISO 문자열 또는 초 단위 타임스탬프 -> "10/20 14:03:12"
export function formatTime(value) {
  if (value === null || value === undefined || value === '') return '-'
  const d = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  if (isNaN(d)) return String(value)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

// 초 -> "1분 05초" / "12.3초"
export function formatSeconds(s) {
  if (s === null || s === undefined) return '-'
  if (s < 60) return `${Number(s).toFixed(1)}초`
  const m = Math.floor(s / 60)
  return `${m}분 ${String(Math.floor(s % 60)).padStart(2, '0')}초`
}

export function formatCost(usd) {
  if (usd === null || usd === undefined) return '-'
  return `$${Number(usd).toFixed(4)}`
}

export function formatNumber(n) {
  if (n === null || n === undefined) return '-'
  return Number(n).toLocaleString('ko-KR')
}
