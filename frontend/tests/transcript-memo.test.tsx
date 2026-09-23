// @vitest-environment jsdom
// 頁面流暢：父層無關狀態更新時，逐字稿面板在 props 未變下不得重新渲染（memo）。
import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useVirtualizer } from "@tanstack/react-virtual";
import TranscriptPanel from "../src/components/TranscriptPanel";
import type { Transcript } from "../src/api/types";
import { mockLayout } from "./helpers/layout";
// 面板每次渲染都會呼叫一次 useVirtualizer：用它的呼叫次數當作「面板渲染次數」。
vi.mock("@tanstack/react-virtual", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@tanstack/react-virtual")>();
  return { ...mod, useVirtualizer: vi.fn(mod.useVirtualizer) };
});
afterEach(cleanup);

const transcript: Transcript = {
  revision: "tr_1",
  source_id: "s",
  cues: Array.from({ length: 300 }, (_, i) => ({ cue_id: `c${i}`, start_us: i * 1_000_000, end_us: i * 1_000_000 + 900_000, text: `第 ${i} 句` })),
};
const stable = {
  annotations: [] as never[],
  seek: vi.fn(),
  edit: vi.fn().mockResolvedValue(undefined),
  classifications: [] as never[],
  overrideClassification: vi.fn().mockResolvedValue(undefined),
};

function Parent() {
  const [unrelated, setUnrelated] = useState(0);
  const [current, setCurrent] = useState(transcript);
  return (
    <>
      <button onClick={() => setUnrelated((n) => n + 1)}>無關更新 {unrelated}</button>
      <button onClick={() => setCurrent({ ...transcript, revision: "tr_2" })}>換稿</button>
      <TranscriptPanel transcript={current} {...stable} />
    </>
  );
}

it("does not re-render the transcript panel when the parent updates unrelated state", () => {
  vi.mocked(useVirtualizer).mockClear();
  render(<Parent />);
  const mountRenders = vi.mocked(useVirtualizer).mock.calls.length;
  expect(mountRenders).toBeGreaterThanOrEqual(1);
  fireEvent.click(screen.getByRole("button", { name: /無關更新/ }));
  fireEvent.click(screen.getByRole("button", { name: /無關更新/ }));
  expect(screen.getByRole("button", { name: /無關更新 2/ })).toBeTruthy();
  // props 未變：memo 命中，面板不再渲染（useVirtualizer 呼叫次數不增加）
  expect(vi.mocked(useVirtualizer).mock.calls.length).toBe(mountRenders);
  // 對照：逐字稿真的換了版本時必須重新渲染（證明計數方式有效）
  fireEvent.click(screen.getByRole("button", { name: "換稿" }));
  expect(vi.mocked(useVirtualizer).mock.calls.length).toBeGreaterThan(mountRenders);
  expect(document.querySelectorAll("article.cue").length).toBeLessThanOrEqual(40);
});

// 使用者 2026-09-20：「好多個待確認的地方那些又是做甚麼用的，根本沒地方套用」
// → 逐字稿的旗標是辨識提示，不是要使用者動作；用看得懂的字，純資訊的（VAD 保留）不顯示
it("shows recognition flags as plain hints and hides the noisy VAD one", () => {
  const transcript = {
    revision: "tr_1", source_id: "s1",
    cues: [
      { cue_id: "c1", start_us: 0, end_us: 1_000_000, text: "第一句", review_flags: ["vad_uncertain_audio_preserved"] },
      { cue_id: "c2", start_us: 1_000_000, end_us: 2_000_000, text: "第二句", review_flags: ["possible_hallucination", "vad_uncertain_audio_preserved"] },
      { cue_id: "c3", start_us: 2_000_000, end_us: 3_000_000, text: "第三句", review_flags: ["draft_gap_filled"] },
    ],
  };
  const restore = mockLayout();
  render(<TranscriptPanel transcript={transcript as never} annotations={[]} seek={vi.fn()} edit={vi.fn().mockResolvedValue(undefined)} classifications={[]} overrideClassification={vi.fn().mockResolvedValue(undefined)} />);
  expect(screen.queryByText(/vad_uncertain_audio_preserved/)).toBeNull();
  expect(screen.getByText(/可能是辨識幻聽/)).toBeTruthy();
  expect(screen.getByText(/草稿漏掉後補轉/)).toBeTruthy();
  expect(screen.queryByText(/待確認/)).toBeNull();
  restore();
});
