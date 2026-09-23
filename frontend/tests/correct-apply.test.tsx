// @vitest-environment jsdom
// 使用者 2026-09-18 第四輪：「依上下文校字」要一次跑完並直接改到逐字稿（守門通過的修正自動套用，可一鍵還原）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { installAppMock } from "./helpers/appMock";
import { mockLayout } from "./helpers/layout";

const calls: { job: unknown[]; edits: unknown[] } = { job: [], edits: [] };
const state = { corrected: false, revision: "tr_1" };
let restoreLayout: () => void = () => {};
const cue = (text: string) => ({ cue_id: "c1", start_us: 6_600_000_000, end_us: 6_601_000_000, text });
beforeEach(() => {
  restoreLayout = mockLayout();  // 虛擬列表在 jsdom 要有尺寸才會渲染逐字稿
  localStorage.clear();
  localStorage.setItem("dongbi.project", "p1");
  localStorage.setItem("dongbi.page", "subtitles");
  calls.job = [];
  calls.edits = [];
  state.corrected = false;
  state.revision = "tr_1";
  installAppMock({
    health: async () => ({ status: "ok" }),
    peaks: async () => ({ peaks: [] }),
    projects: async () => ({ items: [{ project_id: "p1", name: "校字套用" }] }),
    providers: async () => ({ items: [{ id: "local-lmstudio", model: "google/gemma-4-e4b", local: true }] }),
    sequence: async () => ({ revision: "seq_00", source_id: "s1", items: [] }),
    sources: async () => ({ items: [{ source_id: "s1", kind: "youtube", title: "來源", asset_ids: ["a1"], transcript_revision: state.revision }] }),
    asset: async () => ({ asset_id: "a1", source_id: "s1", kind: "audio", duration_us: 600_000_000, source_map: [{ source_start_us: 6_600_000_000, source_end_us: 7_200_000_000, asset_start_us: 0 }] }),
    transcript: async () => ({ revision: state.revision, source_id: "s1", cues: [cue(state.revision === "tr_1" ? "彈步遊戲好難" : "彈幕遊戲好難")] }),
    jobs: async () => ({
      items: state.corrected
        ? [{
            job_id: "cj1", kind: "correct", status: "succeeded", stage: "succeeded", body: { transcript_revision: "tr_1" },
            result: {
              patches: [
                { cue_id: "c1", original_text: "彈步遊戲好難", replacement_text: "彈幕遊戲好難", reason: "同音錯字", base_revision: "tr_1", confidence: "high" },
                { cue_id: "c2", original_text: "我在你", replacement_text: "我跟你", reason: "推測", base_revision: "tr_1", confidence: "low" },
              ],
              rejected_patches: [], ignored_no_change: 3, quality_status: "unverified",
            },
          }]
        : [],
    }),
    job: async (_p: unknown, body: unknown) => {
      calls.job.push(body);
      state.corrected = true;
      return { job_id: "cj1", kind: "correct", status: "queued" };
    },
    edits: async (_p: unknown, revision: unknown, edits: unknown, meta: unknown) => {
      calls.edits.push({ revision, edits, meta });
      state.revision = "tr_2";
      return { revision: "tr_2", source_id: "s1", cues: [cue("彈幕遊戲好難")] };
    },
  });
});
afterEach(() => {
  restoreLayout();
  cleanup();
  vi.resetModules();
});

it("runs the correction and applies the accepted patches to the transcript, with a revert action", async () => {
  const { default: App } = await import("../src/App");
  render(<App />);
  const button = (await screen.findByRole("button", { name: /依上下文校字/ })) as HTMLButtonElement;
  await waitFor(() => expect(button.disabled).toBe(false), { timeout: 4000 });
  expect(button.textContent).toContain("直接套用");
  fireEvent.click(button);
  await waitFor(() => expect(calls.job.length).toBe(1));
  expect((calls.job[0] as Record<string, unknown>).kind).toBe("correct");
  // 2026-09-21：提示詞要知道字幕格式 → 校字請求帶上字幕「保留標點」設定（網頁預設不保留）
  expect((calls.job[0] as Record<string, unknown>).keep_punctuation).toBe(false);
  // 工作完成後（輪詢）自動套用：edits 帶 base_revision 與修正文字
  await waitFor(() => expect(calls.edits.length).toBe(1), { timeout: 6000 });
  // 2026-09-20 使用者：「一上下文處理逐字稿就會每一句套上，而不是還要我確認」→ 高低信心都套用，只留新舊比對與整批還原
  expect(calls.edits[0]).toEqual({
    revision: "tr_1",
    edits: [{ cue_id: "c1", text: "彈幕遊戲好難" }, { cue_id: "c2", text: "我跟你" }],
    meta: { origin: "llm_correction", job_id: "cj1" },
  });
  // 文字改了 → 自動排逐詞對齊（新版本），不需要按鈕
  await waitFor(() => expect(calls.job.some((b) => (b as Record<string, unknown>).kind === "align" && (b as Record<string, unknown>).transcript_revision === "tr_2")).toBe(true), { timeout: 8000 });
  // 面板顯示已套用摘要與還原按鈕；低信心建議仍留成卡片讓人決定（不自動套用）
  await screen.findByText(/已直接套用 2 處/);
  expect(screen.queryByRole("button", { name: "接受修改" })).toBeNull();  // 不用再逐條確認
  const comparison = [...document.querySelectorAll(".applied-list li")].map((li) => li.textContent || "");
  expect(comparison.some((line) => line.includes("彈步遊戲好難") && line.includes("彈幕遊戲好難"))).toBe(true);  // 新舊並排
  expect(comparison.some((line) => line.includes("我在你") && line.includes("我跟你"))).toBe(true);
  // 逐字稿那一句也要看得出被改過（使用者：「圖4部分我也沒看到有沒有改」）
  await screen.findByText(/原：彈步遊戲好難/);
  const revert = screen.getByRole("button", { name: /全部還原/ });
  fireEvent.click(revert);
  await waitFor(() => expect(calls.edits.length).toBe(2), { timeout: 4000 });
  expect(calls.edits[1]).toEqual({
    revision: "tr_2",
    edits: [{ cue_id: "c1", text: "彈步遊戲好難" }, { cue_id: "c2", text: "我在你" }],  // 兩處都還原
    meta: { origin: "llm_revert", job_id: "cj1" },
  });
}, 15000);

it("recognises patches already present in the transcript after a reload and offers revert instead of accept", async () => {
  // 重新載入後：逐字稿已含修正文字 → 視為已套用（不再列成待接受），仍可整批還原
  state.corrected = true;
  state.revision = "tr_2";
  const { default: App } = await import("../src/App");
  render(<App />);
  await screen.findByText(/已直接套用 1 處/, undefined, { timeout: 6000 });
  expect(screen.getAllByRole("button", { name: "接受修改" }).length).toBe(1); // 低信心那一則仍待人決定
  expect(calls.edits.length).toBe(0);
  fireEvent.click(screen.getByRole("button", { name: /全部還原/ }));
  await waitFor(() => expect(calls.edits.length).toBe(1), { timeout: 4000 });
  expect(calls.edits[0]).toEqual({ revision: "tr_2", edits: [{ cue_id: "c1", text: "彈步遊戲好難" }], meta: { origin: "llm_revert", job_id: "cj1" } });
}, 15000);

it("exports the whole asset as one clip when no segment was added, and shows errors next to the button", async () => {
  // 使用者：匯入音檔、轉錄完成後應可直接匯出帶時間軸的 SRT，不必先手動加片段（輸出設定在第 1 頁）
  localStorage.setItem("dongbi.page", "edit");
  state.corrected = false;
  const saved: unknown[] = [];
  const jobBodies: unknown[] = [];
  const { installAppMock } = await import("./helpers/appMock");
  installAppMock({
    health: async () => ({ status: "ok" }),
    peaks: async () => ({ peaks: [] }),
    projects: async () => ({ items: [{ project_id: "p1", name: "匯出" }] }),
    sequence: async () => ({ revision: "seq_00", source_id: "s1", items: [] }),
    sources: async () => ({ items: [{ source_id: "s1", kind: "youtube", title: "來源", asset_ids: ["a1"], transcript_revision: "tr_1" }] }),
    asset: async () => ({ asset_id: "a1", source_id: "s1", kind: "audio", duration_us: 600_000_000, source_map: [{ source_start_us: 6_600_000_000, source_end_us: 7_200_000_000, asset_start_us: 0 }] }),
    transcript: async () => ({ revision: "tr_1", source_id: "s1", cues: [cue("彈步遊戲好難")] }),
    save: async (_p: unknown, body: unknown) => {
      saved.push(body);
      return { ...(body as Record<string, unknown>), revision: "seq_01" };
    },
    job: async (_p: unknown, body: unknown) => {
      jobBodies.push(body);
      return { job_id: "ex1", kind: "export", status: "queued" };
    },
    jobs: async () => ({ items: [] }),
  });
  const { default: App } = await import("../src/App");
  render(<App />);
  const button = (await screen.findByRole("button", { name: /開始匯出/ })) as HTMLButtonElement;
  await waitFor(() => expect(button.disabled).toBe(false), { timeout: 4000 });
  await waitFor(() => expect((screen.getByLabelText("影音格式") as HTMLSelectElement).value).toBe("audio"), { timeout: 4000 });
  fireEvent.click(button);
  await waitFor(() => expect(jobBodies.length).toBe(1), { timeout: 4000 });
  const body = jobBodies[0] as Record<string, unknown>;
  expect(body.kind).toBe("export");
  expect(body.formats).toEqual(["audio"]); // 第 1 頁只出影音；SRT 在字幕頁
  const items = (saved[0] as { items: { start_us: number; end_us: number; selected: boolean }[] }).items;
  expect(items).toHaveLength(1);
  expect(items[0]).toMatchObject({ start_us: 6_600_000_000, end_us: 7_200_000_000, selected: true });
}, 15000);
