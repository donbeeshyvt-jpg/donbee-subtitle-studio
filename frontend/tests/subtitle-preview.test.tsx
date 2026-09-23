// @vitest-environment jsdom
// 使用者 2026-09-20 第 6 點：「SRT 檔案多少就是多少，不要預覽不同步」→ 網頁的字幕預覽直接用匯出 SRT 的同一段計算（POST /subtitles/preview），
// 參數與「匯出 SRT」相同；列出每一則的 SRT 時間（與檔案相同的字串），點一則跳到該處；還沒對齊時說明目前是句子時間。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { installAppMock } from "./helpers/appMock";
import { mockLayout } from "./helpers/layout";

const previewCalls: unknown[] = [];
const state = { alignment: "word" as string, quality: null as Record<string, unknown> | null };
let restoreLayout: () => void = () => {};
beforeEach(() => {
  restoreLayout = mockLayout(); // 虛擬列表在 jsdom 需要尺寸才會渲染列
  localStorage.clear();
  localStorage.setItem("dongbi.project", "p1");
  localStorage.setItem("dongbi.page", "subtitles");
  previewCalls.length = 0;
  state.alignment = "word";
  state.quality = null;
  installAppMock({
    health: async () => ({ status: "ok" }),
    peaks: async () => ({ peaks: [] }),
    projects: async () => ({ items: [{ project_id: "p1", name: "預覽" }] }),
    sequence: async () => ({ revision: "seq_00", source_id: "s1", items: [] }),
    sources: async () => ({ items: [{ source_id: "s1", kind: "local", title: "EP7.wav", asset_ids: ["a1"], transcript_revision: "tr_1" }] }),
    asset: async () => ({ asset_id: "a1", source_id: "s1", kind: "audio", duration_us: 84_400_000, source_map: [{ source_start_us: 0, source_end_us: 84_400_000, asset_start_us: 0 }] }),
    transcript: async () => ({ revision: "tr_1", source_id: "s1", cues: [{ cue_id: "c0", start_us: 240_000, end_us: 1_380_000, text: "真的!是我!" }] }),
    subtitlePreview: async (_p: unknown, body: unknown) => {
      previewCalls.push(body);
      return {
        transcript_revision: "tr_1", alignment: state.alignment, alignment_revision: state.alignment === "word" ? "al_1" : null, timebase: "sequence",
        srt: "1\n00:00:00,240 --> 00:00:00,700\n真的\n",
        warnings: [],
        quality: state.quality || { entries: 2, overlaps: 0, short_display: 0, long_display: 0, fast_entries: 0, max_chars_per_sec: 6, adjacent_repeats: 0, missing_cues: 0, missing_cue_ids: [], warnings: {} },
        entries: [
          { index: 1, start: "00:00:00,240", end: "00:00:00,700", start_us: 240_000, end_us: 700_000, source_start_us: 240_000, source_end_us: 700_000, text: "真的", origin_cue_ids: ["c0"], warnings: [] },
          { index: 2, start: "00:00:01,400", end: "00:00:02,600", start_us: 1_400_000, end_us: 2_600_000, source_start_us: 1_400_000, source_end_us: 2_600_000, text: "啊 救我", origin_cue_ids: ["c1", "c2"], warnings: ["merged_short_cue"] },
        ],
      };
    },
  });
});
afterEach(() => {
  restoreLayout();
  cleanup();
  vi.resetModules();
});

async function openPreview() {
  const { default: App } = await import("../src/App");
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "SRT 字幕預覽" }, { timeout: 5000 }));
}

it("shows the exact SRT entries computed with the same settings as the SRT export", async () => {
  await openPreview();
  await screen.findByText("00:00:00,240 → 00:00:00,700", undefined, { timeout: 5000 });
  expect(screen.getByText("真的")).toBeTruthy();
  expect(screen.getByText("啊 救我")).toBeTruthy();
  expect(screen.getByText(/已合併過短字幕/)).toBeTruthy();
  expect(screen.getByText(/與匯出的 SRT 檔逐字相同.*共 2 則/)).toBeTruthy();
  expect(previewCalls[0]).toEqual({
    source_id: "s1",
    ranges: [{ start_us: 0, end_us: 84_400_000 }],
    transcript_revision: "tr_1",
    sentences_per_cue: 1,
    keep_punctuation: false,
    subtitle_timebase: "sequence",
    grouping: "merge",
  });
}, 15000);

it("says the preview uses sentence timing until word alignment finishes", async () => {
  state.alignment = "pending";
  await openPreview();
  await screen.findByText(/逐詞對齊還沒完成：目前是句子時間/, undefined, { timeout: 5000 });
}, 15000);

it("summarises the subtitle quality check in the preview header", async () => {
  state.quality = { entries: 2, overlaps: 0, short_display: 0, long_display: 1, fast_entries: 1, max_chars_per_sec: 15.5, adjacent_repeats: 0, missing_cues: 0, missing_cue_ids: [], warnings: {} };
  await openPreview();
  await screen.findByText(/字幕檢查：太快 1、過長 1/, undefined, { timeout: 5000 });
}, 15000);
