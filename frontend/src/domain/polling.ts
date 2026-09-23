// 輪詢頻率（頁面流暢，2026-09-21 真瀏覽器量到閒置時每 2 秒打 5 個 API）：
// 有工作在跑才 2 秒一次；全部結束後 15 秒一次；分頁看不見時不輪詢（回到分頁會立刻更新一次）。
export const ACTIVE_JOB_STATUSES = ["queued", "running", "cancelling", "waiting"];
export const FAST_POLL_MS = 2000;
export const IDLE_POLL_MS = 15000;

export function pollDelay(jobs: { status: string }[], hidden: boolean): number | null {
  if (hidden) return null;
  return jobs.some((job) => ACTIVE_JOB_STATUSES.includes(job.status)) ? FAST_POLL_MS : IDLE_POLL_MS;
}

// 回到分頁時立刻更新，但兩秒內已更新過就不再打（分頁來回切換或瀏覽器反覆觸發 visibilitychange 時不會一直打 API）
export const WAKE_MIN_GAP_MS = 2000;
export function shouldWake(now: number, lastRefresh: number): boolean {
  return now - lastRefresh >= WAKE_MIN_GAP_MS;
}

// 輪詢器：stop() 之後，即使還有一次更新在路上，回來後也不會再排下一次（避免 effect 重跑時留下停不掉的輪詢鏈）
export function createPoller(refresh: () => Promise<void>, delay: () => number | null) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = true;
  let last = -Infinity;
  const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());
  const schedule = () => {
    if (stopped) return;
    clearTimeout(timer);
    const wait = delay();
    if (wait !== null) timer = setTimeout(() => void tick(), wait);
  };
  const tick = () => {
    if (stopped) return Promise.resolve();
    last = now();
    return refresh()
      .catch(() => undefined)
      .finally(schedule);
  };
  return {
    start() {
      stopped = false;
      if (last === -Infinity) last = now(); // 第一次開始：開頁時已經載入過，不重複打；之後重新開始保留上次更新時間
      schedule();
    },
    stop() {
      stopped = true;
      clearTimeout(timer);
    },
    // 回到分頁：兩秒內更新過就只重新排時間，不再多打一輪
    wake() {
      if (stopped) return;
      if (shouldWake(now(), last)) void tick();
      else schedule();
    },
  };
}
