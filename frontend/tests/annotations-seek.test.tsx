// @vitest-environment jsdom
// M3-5：摘要／重點的時間碼可點選回播（seek 到該段起點）。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import TranscriptPanel from "../src/components/TranscriptPanel";
import type { Transcript } from "../src/api/types";
afterEach(cleanup);

const transcript: Transcript = {
  revision: "tr_1",
  source_id: "s",
  cues: [{ cue_id: "c1", start_us: 12_000_000, end_us: 15_000_000, text: "這段是重點" }],
};

it("seeks the player to the span start when a summary timecode is clicked", () => {
  const seek = vi.fn();
  render(
    <TranscriptPanel
      transcript={transcript}
      annotations={[
        { id: "a1", kind: "highlight", title: "重點一", text: "這段是重點", cue_ids: ["c1"], spans: [{ start_us: 12_000_000, end_us: 15_000_000 }] },
      ]}
      seek={seek}
      edit={vi.fn().mockResolvedValue(undefined)}
      classifications={[]}
      overrideClassification={vi.fn().mockResolvedValue(undefined)}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /摘要與重點/ }));
  expect(screen.getByText("重點一")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "00:00:12.000" }));
  expect(seek).toHaveBeenCalledWith(12_000_000);
});
