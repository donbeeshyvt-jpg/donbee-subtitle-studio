// @vitest-environment jsdom
// 使用者 2026-09-18：第 1 頁只輸出影音（合併／分段、原畫質裁切）；第 2 頁才有 SRT 匯出與修改紀錄 JSON；
// 下載與輸出區塊最下面都要能指定本地資料夾。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { installAppMock } from "./helpers/appMock";

const calls: { jobs: Record<string, unknown>[]; roots: unknown[]; history: number; requests: string[] } = { jobs: [], roots: [], history: 0, requests: [] };
const roots: { id: string; name: string; path: string }[] = [];
beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("dongbi.project", "p1");
  calls.jobs = [];
  calls.roots = [];
  calls.history = 0;
  roots.length = 0;
  installAppMock({
    health: async () => ({ status: "ok" }),
    peaks: async () => ({ peaks: [] }),
    projects: async () => ({ items: [{ project_id: "p1", name: "輸出拆分" }] }),
    sequence: async () => ({ revision: "seq_00", source_id: "s1", items: [] }),
    sources: async () => ({ items: [{ source_id: "s1", kind: "youtube", title: "來源", asset_ids: ["a1"], transcript_revision: "tr_1" }] }),
    asset: async () => ({ asset_id: "a1", source_id: "s1", kind: "audio", duration_us: 600_000_000, source_map: [{ source_start_us: 6_600_000_000, source_end_us: 7_200_000_000, asset_start_us: 0 }] }),
    transcript: async () => ({ revision: "tr_1", source_id: "s1", cues: [{ cue_id: "c1", start_us: 6_600_000_000, end_us: 6_601_000_000, text: "第一句" }] }),
    save: async (_p: unknown, body: unknown) => ({ ...(body as Record<string, unknown>), revision: "seq_01" }),
    job: async (_p: unknown, body: unknown) => {
      calls.jobs.push(body as Record<string, unknown>);
      return { job_id: `ex${calls.jobs.length}`, kind: "export", status: "queued" };
    },
    addOutputRoot: async (path: unknown, name: unknown, create: unknown) => {
      calls.roots.push({ path, name, create });
      roots.push({ id: "root_new", name: (name as string) || "字幕輸出", path: path as string });
      return { items: [...roots], id: "root_new" };
    },
    pickFolder: async () => ({ path: "D:\\輸出\\影片", cancelled: false }),
    transcriptHistory: async () => {
      calls.history += 1;
      return { current: "tr_1", base: { revision: "tr_0" }, steps: [], total_edits: 0 };
    },
  });
});
afterEach(() => {
  cleanup();
  vi.resetModules();
});

it("page 1 exports media only (merge or per-segment) and lets the user add a local folder", async () => {
  localStorage.setItem("dongbi.page", "edit");
  const { default: App } = await import("../src/App");
  render(<App />);
  const button = (await screen.findByRole("button", { name: /開始匯出/ })) as HTMLButtonElement;
  await waitFor(() => expect(button.disabled).toBe(false), { timeout: 4000 });
  const section = document.querySelector('section[data-section="export"]')!;
  // 第 1 頁沒有字幕句數／標點；有合併／分段與剪輯方式
  expect(section.textContent).not.toContain("每則字幕");
  expect(section.textContent).toContain("合併為一個檔案");
  expect(section.textContent).toContain("精準剪輯");
  // 指定本地資料夾
  fireEvent.click(screen.getByRole("button", { name: "選擇影音輸出資料夾" })); // 用原生視窗選，不用打字
  await waitFor(() => expect(calls.roots.length).toBe(1));
  expect(calls.roots[0]).toMatchObject({ path: "D:\\輸出\\影片" });
  await waitFor(() => expect((screen.getByLabelText("影音輸出儲存位置") as HTMLSelectElement).value).toBe("root_new"));
  fireEvent.click(button);
  await waitFor(() => expect(calls.jobs.length).toBe(1), { timeout: 4000 });
  const body = calls.jobs[0];
  expect(body.kind).toBe("export");
  expect(body.formats).toEqual(["audio"]); // 只有影音，沒有 srt
  expect(body.output_root_id).toBe("root_new");
}, 15000);

it("page 2 exports the SRT of the whole asset with subtitle options and downloads the change history JSON", async () => {
  localStorage.setItem("dongbi.page", "subtitles");
  const createObjectURL = vi.fn(() => "blob:history");
  (URL as unknown as { createObjectURL: unknown }).createObjectURL = createObjectURL;
  (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = vi.fn();
  const clicked: string[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    clicked.push(this.download);
  });
  const { default: App } = await import("../src/App");
  render(<App />);
  const exportSrt = (await screen.findByRole("button", { name: /匯出 SRT/ })) as HTMLButtonElement;
  await waitFor(() => expect(exportSrt.disabled).toBe(false), { timeout: 4000 });
  const panel = exportSrt.closest(".subtitle-export")!;
  expect(panel.textContent).toContain("每則字幕");
  fireEvent.change(screen.getByLabelText("每則字幕句數"), { target: { value: "2" } });
  fireEvent.click(screen.getByLabelText("保留標點"));
  fireEvent.click(exportSrt);
  await waitFor(() => expect(calls.jobs.length).toBe(1), { timeout: 4000 });
  const body = calls.jobs[0];
  expect(body).toMatchObject({ kind: "export", source_id: "s1", transcript_revision: "tr_1", formats: ["srt"], sentences_per_cue: 2, keep_punctuation: true });
  // 2026-09-19：這一版逐字稿還沒對齊完就按匯出 → 交給後端等（或補排）逐詞對齊，不悄悄退回句子時間
  expect(body.await_alignment).toBe(true);
  expect(panel.querySelector(".srt-timing-hint")!.textContent).toContain("匯出會先等逐詞對齊完成");
  expect(body.ranges).toEqual([{ start_us: 6_600_000_000, end_us: 7_200_000_000 }]);
  expect(body.sequence_revision).toBeUndefined();
  fireEvent.click(screen.getByRole("button", { name: /匯出修改紀錄/ }));
  await waitFor(() => expect(calls.history).toBe(1));
  await waitFor(() => expect(clicked.length).toBe(1));
  expect(clicked[0]).toContain("逐字稿修改紀錄");
  expect(createObjectURL).toHaveBeenCalled();
}, 15000);
