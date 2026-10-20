/** 3D 사무실 리듀서(`officeState.ts`)가 읽는 이벤트 모양.
 *
 * 사무실 코드는 기존 React 화면(`web/`)에서 그대로 가져왔다. 그 화면은 기존 A2A
 * 경로의 이벤트(`task_submitted`, `task_completed` …)를 받았으므로, 새 SSE 이벤트는
 * `adapter.js` 가 이 모양으로 바꿔 넘긴다.
 */
export type VsiEvent = {
  event_id: number;
  aggregate: string;
  aggregate_id: string;
  event_type: string;
  payload: Record<string, unknown>;
};
