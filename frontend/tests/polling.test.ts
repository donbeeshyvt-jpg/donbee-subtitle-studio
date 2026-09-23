// 頁面流暢（2026-09-21 真瀏覽器量到）：沒有任何工作在跑時，頁面仍每 2 秒打 5 個 API（10 秒 25 次）。
// 輪詢改成看狀態：有工作在跑才 2 秒一次；閒置 15 秒一次；分頁看不見時不輪詢（回來再立刻更新）。
import { expect, it } from "vitest";
import { pollDelay } from "../src/domain/polling";

const job = (status: string) => ({ job_id: status, kind: "analyze", status });

it("polls fast only while something is running", () => {
  expect(pollDelay([job("running")], false)).toBe(2000);
  expect(pollDelay([job("queued"), job("succeeded")], false)).toBe(2000);
  expect(pollDelay([job("cancelling")], false)).toBe(2000);
  expect(pollDelay([job("waiting")], false)).toBe(2000);
});

it("backs off when everything is finished", () => {
  expect(pollDelay([job("succeeded"), job("failed"), job("cancelled")], false)).toBe(15000);
  expect(pollDelay([], false)).toBe(15000);
});

it("does not poll a hidden tab at all", () => {
  expect(pollDelay([job("running")], true)).toBeNull();
  expect(pollDelay([], true)).toBeNull();
});

// 回到分頁會立刻更新一次；但分頁在短時間內來回切（或瀏覽器反覆觸發 visibilitychange）不可每次都打一輪 API
import { shouldWake } from "../src/domain/polling";
it("wakes up at most once per two seconds when the tab keeps flipping visible", () => {
  expect(shouldWake(10_000, 0)).toBe(true); // 很久沒更新：回來就更新
  expect(shouldWake(10_000, 9_000)).toBe(false); // 1 秒前才更新過：不再打
  expect(shouldWake(10_000, 8_000)).toBe(true);
});

// 真瀏覽器追到的洩漏：effect 重跑時若有一次更新還在路上，它結束後會再排下一次 → 留下沒人能停的輪詢鏈，越用越多
import { createPoller } from "../src/domain/polling";
import { vi } from "vitest";
it("never keeps polling after it has been stopped, even when a refresh was still in flight", async () => {
  vi.useFakeTimers();
  try {
    let finish: () => void = () => {};
    const refresh = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    const poller = createPoller(refresh, () => 1000);
    poller.start();
    await vi.advanceTimersByTimeAsync(1000);
    expect(refresh).toHaveBeenCalledTimes(1); // 第一次更新進行中
    poller.stop(); // 例如切換專案、工作狀態改變
    finish(); // 那次更新這時才回來
    await vi.advanceTimersByTimeAsync(20000);
    expect(refresh).toHaveBeenCalledTimes(1); // 不可以再排下一次
  } finally {
    vi.useRealTimers();
  }
});

it("wakes immediately once but not twice within two seconds", async () => {
  vi.useFakeTimers();
  try {
    const refresh = vi.fn(async () => {});
    const poller = createPoller(refresh, () => 15000);
    poller.start();
    await vi.advanceTimersByTimeAsync(2500);
    poller.wake();
    poller.wake();
    await vi.advanceTimersByTimeAsync(10);
    expect(refresh).toHaveBeenCalledTimes(1);
    poller.stop();
  } finally {
    vi.useRealTimers();
  }
});

it("refreshes right away when you come back to the tab after a while", async () => {
  vi.useFakeTimers();
  try {
    const refresh = vi.fn(async () => {});
    const poller = createPoller(refresh, () => 15000);
    poller.start();
    poller.stop(); // 切到別的分頁
    await vi.advanceTimersByTimeAsync(60000);
    poller.start(); // 回來
    poller.wake();
    await vi.advanceTimersByTimeAsync(10);
    expect(refresh).toHaveBeenCalledTimes(1);
    poller.stop();
  } finally {
    vi.useRealTimers();
  }
});
