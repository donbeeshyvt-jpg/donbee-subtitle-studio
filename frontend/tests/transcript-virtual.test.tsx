// @vitest-environment jsdom
// B10 契約：長逐字稿只渲染可視範圍（虛擬列表），搜尋過濾在大量 cue 下仍即時。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import TranscriptPanel from "../src/components/TranscriptPanel";
import type { Transcript } from "../src/api/types";
afterEach(cleanup);

function bigTranscript(count: number): Transcript {
  return {
    revision: "tr_big",
    source_id: "s",
    cues: Array.from({ length: count }, (_, i) => ({
      cue_id: `cue_${i}`,
      start_us: i * 2_000_000,
      end_us: i * 2_000_000 + 1_500_000,
      text: i % 97 === 0 ? `抽樣句 ${i}` : `第 ${i} 句逐字稿內容，用來測試長列表的滾動與過濾。`,
    })),
  };
}

it("renders only a window of a 2,000-cue transcript and filters without rendering everything", () => {
  const transcript = bigTranscript(2000);
  const started = performance.now();
  render(
    <TranscriptPanel
      transcript={transcript}
      annotations={[]}
      seek={vi.fn()}
      edit={vi.fn().mockResolvedValue(undefined)}
      classifications={[]}
      overrideClassification={vi.fn().mockResolvedValue(undefined)}
    />,
  );
  const firstPaint = performance.now() - started;
  const rendered = document.querySelectorAll("article.cue").length;
  expect(rendered).toBeLessThanOrEqual(40);
  expect(document.querySelector(".transcript-scroll")).toBeTruthy();
  const search = screen.getByPlaceholderText(/搜尋|過濾|關鍵字/) as HTMLInputElement;
  const filterStarted = performance.now();
  fireEvent.change(search, { target: { value: "抽樣句" } });
  const filterElapsed = performance.now() - filterStarted;
  expect(document.querySelectorAll("article.cue").length).toBeLessThanOrEqual(40);
  expect(filterElapsed).toBeLessThan(1000);
  expect(firstPaint).toBeLessThan(3000);
});
