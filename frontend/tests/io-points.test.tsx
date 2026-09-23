// @vitest-environment jsdom
// 使用者 2026-09-18：入出點要能取消；I 與 O 沒有形成有效範圍（未設或同一點）時「加入片段」不可用。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { installAppMock } from "./helpers/appMock";

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("dongbi.project", "p1");
  localStorage.setItem("dongbi.page", "edit");
  installAppMock({
    health: async () => ({ status: "ok" }),
    peaks: async () => ({ peaks: [] }),
    projects: async () => ({ items: [{ project_id: "p1", name: "入出點" }] }),
    sequence: async () => ({ revision: "seq_00", source_id: "s1", items: [] }),
    sources: async () => ({ items: [{ source_id: "s1", kind: "youtube", title: "來源", asset_ids: ["a1"] }] }),
    asset: async () => ({ asset_id: "a1", source_id: "s1", kind: "audio", duration_us: 600_000_000, source_map: [{ source_start_us: 6_600_000_000, source_end_us: 7_200_000_000, asset_start_us: 0 }] }),
  });
});
afterEach(() => {
  cleanup();
  vi.resetModules();
});

// jsdom 的媒體元素不會真的播放：直接覆寫 currentTime 再送 timeupdate，讓 App 的 time 狀態更新
function playheadTo(video: HTMLVideoElement, seconds: number) {
  Object.defineProperty(video, "currentTime", { value: seconds, configurable: true, writable: true });
  fireEvent.timeUpdate(video);
}

it("keeps 加入片段 disabled until in and out form a real range, and can clear the range", async () => {
  const { default: App } = await import("../src/App");
  render(<App />);
  const video = (await waitFor(() => {
    const el = document.querySelector("video");
    expect(el).toBeTruthy();
    return el!;
  }, { timeout: 4000 })) as HTMLVideoElement;
  Object.defineProperty(video, "duration", { value: 600, configurable: true });
  fireEvent.loadedMetadata(video);
  const add = screen.getByRole("button", { name: /加入片段/ }) as HTMLButtonElement;
  const setIn = screen.getByRole("button", { name: /^入點/ });
  const setOut = screen.getByRole("button", { name: /^出點/ });
  // 一開始沒有入出點：沒有範圍帶、加入片段停用、讀數顯示未設定
  expect(document.querySelector(".io-range")).toBeNull();
  expect(add.disabled).toBe(true);
  // 2026-09-21：讀數改成可輸入的欄位，沒設定時是空的（提示 --:--:--.---）
  expect((screen.getByLabelText("入點時間") as HTMLInputElement).value).toBe("");
  expect((screen.getByLabelText("出點時間") as HTMLInputElement).value).toBe("");
  // 入點與出點在同一點：仍然停用
  playheadTo(video, 120);
  fireEvent.click(setIn);
  fireEvent.click(setOut);
  expect(add.disabled).toBe(true);
  expect(document.querySelector(".io-range")).toBeNull();
  // 出點在入點之後：範圍帶出現、可加入片段
  playheadTo(video, 150);
  fireEvent.click(setOut);
  await waitFor(() => expect(add.disabled).toBe(false));
  const band = document.querySelector(".io-range") as HTMLElement;
  expect(band.style.left).toBe("20%");
  expect(band.style.width).toBe("5%");
  // 清除入出點：範圍帶消失、按鈕停用；Esc 也可以
  fireEvent.click(screen.getByRole("button", { name: /清除入出點/ }));
  expect(document.querySelector(".io-range")).toBeNull();
  expect(add.disabled).toBe(true);
  fireEvent.click(setIn);
  playheadTo(video, 200);
  fireEvent.click(setOut);
  await waitFor(() => expect(add.disabled).toBe(false));
  fireEvent.keyDown(document.body, { key: "Escape" });
  await waitFor(() => expect(add.disabled).toBe(true));
  expect(document.querySelector(".io-range")).toBeNull();
}, 15000);

it("adds a clip with the P key when a range is selected on the edit page", async () => {
  const { default: App } = await import("../src/App");
  render(<App />);
  const video = (await waitFor(() => {
    const el = document.querySelector("video");
    expect(el).toBeTruthy();
    return el!;
  }, { timeout: 4000 })) as HTMLVideoElement;
  Object.defineProperty(video, "duration", { value: 600, configurable: true });
  fireEvent.loadedMetadata(video);
  const badge = () => document.querySelector('section[data-section="segments"] .collapsible-toggle .count')?.textContent ?? null;
  fireEvent.keyDown(document.body, { key: "p" }); // 沒有範圍：沒事
  expect(badge()).toBeNull();
  playheadTo(video, 100);
  fireEvent.keyDown(document.body, { key: "i" });
  playheadTo(video, 130);
  fireEvent.keyDown(document.body, { key: "o" });
  await waitFor(() => expect(document.querySelector(".io-range")).toBeTruthy());
  fireEvent.keyDown(document.body, { key: "p" });
  await waitFor(() => expect(badge()).toBe("1"));
  expect(document.querySelector(".io-range")).toBeNull(); // 加入後範圍清除
}, 15000);

it("pauses playback when the playhead reaches the out point inside the I/O range", async () => {
  const { default: App } = await import("../src/App");
  render(<App />);
  const video = (await waitFor(() => {
    const el = document.querySelector("video");
    expect(el).toBeTruthy();
    return el!;
  }, { timeout: 4000 })) as HTMLVideoElement;
  Object.defineProperty(video, "duration", { value: 600, configurable: true });
  fireEvent.loadedMetadata(video);
  const pause = vi.spyOn(video, "pause").mockImplementation(() => {});
  let paused = false;
  Object.defineProperty(video, "paused", { get: () => paused, configurable: true });
  playheadTo(video, 100);
  fireEvent.keyDown(document.body, { key: "i" });
  playheadTo(video, 130);
  fireEvent.keyDown(document.body, { key: "o" });
  await waitFor(() => expect(document.querySelector(".io-range")).toBeTruthy());
  // 從範圍內播放，經過出點 → 暫停並停在出點
  fireEvent.play(video);
  playheadTo(video, 129.8);
  playheadTo(video, 130.1);
  expect(pause).toHaveBeenCalledTimes(1);
  expect(Math.round((video.currentTime as number) * 1000)).toBe(130_000);
  paused = true;
  fireEvent.pause(video);
  // 在範圍外（出點之後）繼續播放不受影響；直接跳到範圍外（大跳）也不算「碰到出點」
  paused = false;
  playheadTo(video, 131);
  playheadTo(video, 131.3);
  playheadTo(video, 110);
  playheadTo(video, 200);
  expect(pause).toHaveBeenCalledTimes(1);
}, 15000);

// 2026-09-21 真瀏覽器（模仿使用者切 5–10 分、9–15 分）：只能靠播放位置或拖曳，切不準 → 入出點可以直接打時間（素材相對時間）
it("sets in and out points by typing a time, and rejects times outside the asset", async () => {
  const { default: App } = await import("../src/App");
  render(<App />);
  const video = (await waitFor(() => {
    const el = document.querySelector("video");
    expect(el).toBeTruthy();
    return el!;
  }, { timeout: 4000 })) as HTMLVideoElement;
  Object.defineProperty(video, "duration", { value: 600, configurable: true });
  fireEvent.loadedMetadata(video);
  const add = screen.getByRole("button", { name: /加入片段/ }) as HTMLButtonElement;
  const inBox = screen.getByLabelText("入點時間") as HTMLInputElement;
  const outBox = screen.getByLabelText("出點時間") as HTMLInputElement;
  fireEvent.change(inBox, { target: { value: "2:00" } });
  fireEvent.keyDown(inBox, { key: "Enter" });
  fireEvent.change(outBox, { target: { value: "00:02:30" } });
  fireEvent.blur(outBox);
  await waitFor(() => expect(add.disabled).toBe(false));
  const band = document.querySelector(".io-range") as HTMLElement;
  expect(band.style.left).toBe("20%");
  expect(band.style.width).toBe("5%");
  expect(inBox.value).toBe("00:02:00.000");
  expect(inBox.title).toContain("01:52:00.000"); // 來源時間（素材從來源 1:50:00 開始）
  // 超出素材長度或打錯：說明原因、保留原本的值
  fireEvent.change(outBox, { target: { value: "12:00" } });
  fireEvent.keyDown(outBox, { key: "Enter" });
  await screen.findByText(/超出素材長度/);
  expect(outBox.value).toBe("00:02:30.000");
  fireEvent.change(inBox, { target: { value: "abc" } });
  fireEvent.keyDown(inBox, { key: "Enter" });
  await screen.findByText(/時間格式/);
  expect(inBox.value).toBe("00:02:00.000");
  // 清空＝取消這個點
  fireEvent.change(inBox, { target: { value: "" } });
  fireEvent.blur(inBox);
  await waitFor(() => expect(add.disabled).toBe(true));
}, 15000);
