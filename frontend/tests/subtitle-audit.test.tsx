// @vitest-environment jsdom
// M4-C5（2026-09-20）：字幕預覽每一則可以「只聽入點／出點前後各約 0.5 秒」，用耳朵抽查時間對不對，
// 不必從頭播（SRT 毫秒精度不等於對齊正確）。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import SubtitlePreviewList from "../src/components/SubtitlePreviewList";
import { mockLayout } from "./helpers/layout";
import type { SubtitlePreview } from "../src/api/types";
afterEach(cleanup);

const preview: SubtitlePreview = {
  transcript_revision: "tr_1",
  alignment: "word",
  alignment_revision: "al_1",
  timebase: "sequence",
  srt: "",
  warnings: [],
  entries: [
    { index: 1, start: "00:00:03,310", end: "00:00:03,700", start_us: 3_310_000, end_us: 3_700_000, source_start_us: 3_310_000, source_end_us: 3_700_000, text: "我在哪哦", origin_cue_ids: ["c1"], warnings: [] },
  ],
};

it("plays a short window around the in and out points of one subtitle", () => {
  const restore = mockLayout();
  const audit = vi.fn();
  render(<SubtitlePreviewList preview={preview} error="" currentUs={null} seek={vi.fn()} audit={audit} />);
  fireEvent.click(screen.getByRole("button", { name: "聽入點" }));
  expect(audit).toHaveBeenLastCalledWith(3_310_000);
  fireEvent.click(screen.getByRole("button", { name: "聽出點" }));
  expect(audit).toHaveBeenLastCalledWith(3_700_000);
  restore();
});
