// @vitest-environment jsdom
// 使用者 2026-09-18 第六輪：匯入用原生視窗選檔（後端拿得到原始路徑）；輸出設定併進轉錄卡片、資料夾用視窗選；
// 預設輸出到匯入檔旁的 subtitle_studio；轉錄完成自動輸出 SRT 與紀錄。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { installAppMock } from "./helpers/appMock";

const calls: { pickFile: number; pickFolder: number; importLocal: unknown[]; jobs: Record<string, unknown>[]; roots: unknown[] } = { pickFile: 0, pickFolder: 0, importLocal: [], jobs: [], roots: [] };
const state = { imported: false };
beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("dongbi.project", "p1");
  localStorage.setItem("dongbi.page", "subtitles");
  calls.pickFile = 0;
  calls.pickFolder = 0;
  calls.importLocal = [];
  calls.jobs = [];
  calls.roots = [];
  state.imported = false;
  installAppMock({
    health: async () => ({ status: "ok" }),
    peaks: async () => ({ peaks: [] }),
    projects: async () => ({ items: [{ project_id: "p1", name: "側欄輸出" }] }),
    sequence: async () => ({ revision: "seq_00", source_id: state.imported ? "s1" : null, items: [] }),
    sources: async () => ({
      items: state.imported
        ? [{ source_id: "s1", kind: "local", title: "剪好的音訊.wav", asset_ids: ["a1"], origin_dir: "D:\\案子\\EP7", default_output_root_id: "root_side" }]
        : [],
    }),
    asset: async () => ({ asset_id: "a1", source_id: "s1", kind: "audio", duration_us: 84_400_000, source_map: [{ source_start_us: 0, source_end_us: 84_400_000, asset_start_us: 0 }] }),
    capabilities: async () => ({
      source_subtitles: false,
      output_roots: state.imported ? [{ id: "root_side", name: "EP7／subtitle_studio", path: "D:\\案子\\EP7\\subtitle_studio", kind: "sidecar" }] : [],
    }),
    pickFile: async () => {
      calls.pickFile += 1;
      return { path: "D:\\案子\\EP7\\剪好的音訊.wav", cancelled: false };
    },
    pickFolder: async () => {
      calls.pickFolder += 1;
      return { path: "D:\\輸出\\字幕", cancelled: false };
    },
    importLocal: async (_p: unknown, path: unknown) => {
      calls.importLocal.push(path);
      state.imported = true;
      return { source_id: "s1", job_id: "probe1", default_output_root_id: "root_side" };
    },
    addOutputRoot: async (path: unknown) => {
      calls.roots.push(path);
      return {
        items: [
          { id: "root_side", name: "EP7／subtitle_studio", path: "D:\\案子\\EP7\\subtitle_studio", kind: "sidecar" },
          { id: "root_pick", name: "字幕", path: path as string, target: `${path as string}\\subtitle_studio`, kind: "folder" },
        ],
        id: "root_pick",
      };
    },
    job: async (_p: unknown, body: unknown) => {
      calls.jobs.push(body as Record<string, unknown>);
      return { job_id: `j${calls.jobs.length}`, kind: (body as Record<string, unknown>).kind, status: "queued" };
    },
  });
});
afterEach(() => {
  cleanup();
  vi.resetModules();
});

it("imports through the native file window and transcribes with automatic output next to the imported file", async () => {
  const { default: App } = await import("../src/App");
  render(<App />);
  const importButton = (await screen.findByRole("button", { name: /匯入本機檔案/ })) as HTMLButtonElement;
  await waitFor(() => expect(importButton.disabled).toBe(false), { timeout: 5000 });
  fireEvent.click(importButton);
  await waitFor(() => expect(calls.importLocal).toEqual(["D:\\案子\\EP7\\剪好的音訊.wav"]));
  expect(calls.pickFile).toBe(1);
  const transcribe = (await screen.findByRole("button", { name: /^(重新)?轉錄$/ })) as HTMLButtonElement;
  await waitFor(() => expect(transcribe.disabled).toBe(false), { timeout: 5000 });
  // 輸出設定在轉錄卡片裡；預設輸出位置＝匯入檔旁的 subtitle_studio
  const card = transcribe.closest('section[data-section="transcribe"]')!;
  expect(card.querySelector(".subtitle-export")).toBeTruthy();
  expect(card.textContent).toContain("轉錄完成後自動輸出");
  await waitFor(() => expect(card.textContent).toContain("D:\\案子\\EP7\\subtitle_studio"), { timeout: 5000 });
  fireEvent.click(transcribe);
  await waitFor(() => expect(calls.jobs.length).toBe(1));
  expect(calls.jobs[0]).toMatchObject({ kind: "analyze", auto_export: { output_root_id: "root_side", sentences_per_cue: 1, keep_punctuation: false } });
}, 20000);

it("picks another output folder through the native folder window instead of typing", async () => {
  state.imported = true;
  const { default: App } = await import("../src/App");
  render(<App />);
  const pick = (await screen.findByRole("button", { name: "選擇字幕輸出資料夾" })) as HTMLButtonElement;
  await waitFor(() => expect(pick.disabled).toBe(false), { timeout: 5000 });
  fireEvent.click(pick);
  await waitFor(() => expect(calls.roots).toEqual(["D:\\輸出\\字幕"]));
  expect(calls.pickFolder).toBe(1);
  await waitFor(() => expect((screen.getByLabelText("字幕輸出儲存位置") as HTMLSelectElement).value).toBe("root_pick"));
  // 自己選的資料夾也一樣放進 subtitle_studio：「輸出到」要顯示檔案真正會出現的位置
  const card = pick.closest(".subtitle-export")!;
  await waitFor(() => expect(card.querySelector(".output-root-path")!.textContent).toContain("D:\\輸出\\字幕\\subtitle_studio"));
}, 15000);

it("does not fall back to a browser upload when the file window failed for another reason", async () => {
  const click = vi.spyOn(HTMLInputElement.prototype, "click").mockImplementation(() => undefined);
  installAppMock({
    health: async () => ({ status: "ok" }),
    projects: async () => ({ items: [{ project_id: "p1", name: "側欄輸出" }] }),
    sequence: async () => ({ revision: "seq_00", source_id: null, items: [] }),
    sources: async () => ({ items: [] }),
    pickFile: async () => {
      throw Object.assign(new Error("與本機服務的連線已失效，請重新整理頁面（F5）"), { status: 401, code: "SESSION_EXPIRED" });
    },
  });
  const { default: App } = await import("../src/App");
  render(<App />);
  const importButton = (await screen.findByRole("button", { name: /匯入本機檔案/ })) as HTMLButtonElement;
  await waitFor(() => expect(importButton.disabled).toBe(false), { timeout: 5000 });
  fireEvent.click(importButton);
  await waitFor(() => expect(document.body.textContent).toContain("請重新整理頁面"), { timeout: 5000 });
  expect(click).not.toHaveBeenCalled();
  click.mockRestore();
}, 15000);
