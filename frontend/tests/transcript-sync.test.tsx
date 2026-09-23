// @vitest-environment jsdom
// 使用者 2026-09-18 第四輪：右側卡片要顯示「素材相對時間」（10 分鐘片段就是 00:00–00:10），
// 點卡片任何地方都要跳到該句，播放中目前句要標示並自動捲到可視範圍。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import TranscriptPanel from "../src/components/TranscriptPanel";
import { formatTimecode, parseTimecode } from "../src/domain/time";
import { mockLayout } from "./helpers/layout";

let restore = () => {};
beforeEach(() => {
  restore = mockLayout();
});
afterEach(() => {
  cleanup();
  restore();
});

const BASE = 6_600_000_000; // 素材從來源 01:50:00 開始
const transcript = {
  revision: "t1",
  source_id: "s",
  cues: [
    { cue_id: "c1", start_us: BASE + 850_000, end_us: BASE + 1_310_000, text: "這樣子" },
    { cue_id: "c2", start_us: BASE + 3_400_000, end_us: BASE + 4_680_000, text: "畢竟被這個打到不會怎樣" },
  ],
};
const panel = (extra: Record<string, unknown> = {}) => (
  <TranscriptPanel
    transcript={transcript}
    annotations={[]}
    seek={vi.fn()}
    edit={vi.fn().mockResolvedValue(undefined)}
    classifications={[]}
    overrideClassification={vi.fn().mockResolvedValue(undefined)}
    {...extra}
  />
);

it("formats and parses timecodes relative to a base", () => {
  expect(formatTimecode(BASE + 850_000, BASE)).toBe("00:00:00.850");
  expect(parseTimecode("00:00:00.850", BASE)).toBe(BASE + 850_000);
  expect(formatTimecode(12_000_000)).toBe("00:00:12.000");
});

it("shows clip-relative times and seeks to the source time when the card body is clicked", () => {
  const seek = vi.fn();
  render(panel({ timeBase: BASE, seek }));
  expect(screen.getByRole("button", { name: "00:00:00.850" })).toBeTruthy();
  expect(screen.queryByText(/01:50:00/)).toBeNull();
  fireEvent.click(screen.getByText("畢竟被這個打到不會怎樣"));
  expect(seek).toHaveBeenCalledWith(BASE + 3_400_000);
  // 按「編輯」不應觸發跳轉
  seek.mockClear();
  fireEvent.click(screen.getAllByRole("button", { name: "編輯" })[0]);
  expect(seek).not.toHaveBeenCalled();
});

it("marks the cue under the playhead as current", () => {
  render(panel({ timeBase: BASE, currentUs: BASE + 3_900_000 }));
  const current = document.querySelectorAll("article.cue.is-current");
  expect(current.length).toBe(1);
  expect(current[0].textContent).toContain("畢竟被這個打到不會怎樣");
});
