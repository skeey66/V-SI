// REST API 를 부르는 얇은 fetch 래퍼 모음. (계약: docs/lg/api-contract.md)

// 공통 요청 함수: JSON 을 보내고 JSON 을 받는다. 실패하면 Error 를 던진다.
async function request(method, path, body) {
  const options = { method, headers: {} }
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json'
    options.body = JSON.stringify(body)
  }
  let res
  try {
    res = await fetch(path, options)
  } catch {
    throw new Error('서버에 연결할 수 없습니다 (lg_api 가 8200 포트에서 실행 중인지 확인하세요)')
  }
  const text = await res.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!res.ok) {
    // FastAPI 는 보통 {"detail": "..."} 형태로 오류를 준다
    const detail = data && data.detail ? data.detail : text
    throw new Error(`요청 실패 (HTTP ${res.status}) ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`)
  }
  return data
}

const enc = encodeURIComponent

// ---- 요구사항 ----
export const createRequirement = (title) => request('POST', '/api/requirements', { title })
export const listRequirements = () => request('GET', '/api/requirements')
export const getRequirement = (id) => request('GET', `/api/requirements/${enc(id)}`)
export const approve = (id, approved) =>
  request('POST', `/api/requirements/${enc(id)}/approve`, { approved })
export const listFiles = (id) => request('GET', `/api/requirements/${enc(id)}/files`)
// 파일 경로는 "src/cart.py" 처럼 / 를 포함하므로, 각 조각만 인코딩한다
export const getFile = (id, path) =>
  request('GET', `/api/requirements/${enc(id)}/files/${path.split('/').map(enc).join('/')}`)
export const sendFeedback = (id, score, comment) =>
  request('POST', `/api/requirements/${enc(id)}/feedback`, comment ? { score, comment } : { score })

// ---- 관리자 ----
export const getConfig = () => request('GET', '/api/admin/config')
export const updateConfig = (patch) => request('PUT', '/api/admin/config', patch)
export const getSummary = () => request('GET', '/api/admin/summary')
export const listRuns = (limit = 50) => request('GET', `/api/admin/runs?limit=${limit}`)
export const getPrompt = () => request('GET', '/api/admin/prompts/planner')
export const pushPrompt = (template) => request('POST', '/api/admin/prompts/planner', { template })
export const activatePrompt = (commitHash) =>
  request('POST', '/api/admin/prompts/planner/activate', { commit_hash: commitHash })
