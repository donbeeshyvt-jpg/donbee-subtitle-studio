// @vitest-environment jsdom
// 使用者 2026-09-18 第四輪：下載完成後要有「轉錄」按鈕把目前素材交給 WhisperX，轉錄完成自動載入逐字稿；
// 轉錄只處理播放中的那一份素材（同一範圍可能有多份下載），預設品質為「精修」。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { installAppMock } from "./helpers/appMock";

const jobCalls: unknown[] = [];
const state = { transcribing: false };
beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("dongbi.project", "p1");
  localStorage.setItem("dongbi.page", "subtitles");
  jobCalls.length = 0;
  state.transcribing = false;
  installAppMock({
    health: async () => ({ status: "ok" }),
    peaks: async () => ({ peaks: [] }),
    projects: async () => ({ items: [{ project_id: "p1", name: "轉錄流程" }] }),
    sequence: async () => ({ revision: "seq_00", source_id: "s1", items: [] }),
    sources: async () => ({ items: [{ source_id: "s1", kind: "youtube", title: "來源", asset_ids: ["a_old", "a1"] }] }),
    asset: async (id: unknown) => ({
      asset_id: id,
      source_id: "s1",
      kind: "audio",
      duration_us: 600_000_000,
      source_map: [{ source_start_us: 6_600_000_000, source_end_us: 7_200_000_000, asset_start_us: 0 }],
    }),
    jobs: async () => ({
      items: state.transcribing
        ? [{ job_id: "an1", kind: "analyze", status: "running", stage: "refine", body: { source_id: "s1" } }]
        : [],
    }),
    job: async (_p: unknown, body: unknown) => {
      jobCalls.push(body);
      state.transcribing = true;
      return { job_id: "an1", kind: "analyze", status: "queued" };
    },
  });
});
afterEach(() => {
  cleanup();
  vi.resetModules();
});

it("transcribes only the asset in the player with the quality profile and shows progress", async () => {
  const { default: App } = await import("../src/App");
  render(<App />);
  const button = (await screen.findByRole("button", { name: /^(重新)?轉錄$/ })) as HTMLButtonElement;
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
  await waitFor(() => expect(jobCalls.length).toBe(1));
  const body = jobCalls[0] as Record<string, unknown>;
  expect(body.kind).toBe("analyze");
  expect(body.asset_ids).toEqual(["a_old"]); // 播放中的素材（第一個候選），不是來源的全部素材
  expect(body.profile).toBe("quality");
  expect(body.engine).toBe("whisperx");
  await waitFor(() => expect(document.querySelector(".transcribe-state")?.textContent).toContain("轉錄中：large-v3 精修"));
});
