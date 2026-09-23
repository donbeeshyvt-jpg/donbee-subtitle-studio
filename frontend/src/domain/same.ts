// 輪詢資料內容相同時保留原本的 state 參考：避免每 2 秒讓整個畫面（含 memo 的面板）重新渲染。
export function sameData(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;
  }
}

/** 給 setState 用：`setJobs(keep(next))` — 內容相同就回傳舊值（不觸發重繪），否則採用新值。 */
export const keep =
  <T>(next: T) =>
  (prev: T): T =>
    sameData(prev, next) ? prev : next;
