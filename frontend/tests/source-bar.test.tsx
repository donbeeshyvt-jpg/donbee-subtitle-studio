// @vitest-environment jsdom
// 使用者 2026-09-18：剪輯頁的貼連結／匯入不見了（其實被收在可摺疊區塊裡）；字幕頁也要能上傳，兩頁共用同一份素材。
// → 來源列（貼連結、加入下載、匯入本機檔案）固定顯示在兩頁最上方，不放在可摺疊區塊裡。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { installAppMock } from "./helpers/appMock";

const uploads: File[] = [];
beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("dongbi.project", "p1");
  localStorage.setItem("dongbi.section.acquire", "0"); // 使用者把下載設定收起來了
  uploads.length = 0;
  installAppMock({
    health: async () => ({ status: "ok" }),
    peaks: async () => ({ peaks: [] }),
    projects: async () => ({ items: [{ project_id: "p1", name: "來源列" }] }),
    sequence: async () => ({ revision: "seq_00", source_id: null, items: [] }),
    sources: async () => ({ items: uploads.length ? [{ source_id: "s_up", kind: "local", title: "剪好的音訊.wav", asset_ids: ["a_up"] }] : [] }),
    asset: async () => ({ asset_id: "a_up", source_id: "s_up", kind: "audio", duration_us: 84_400_000, source_map: [{ source_start_us: 0, source_end_us: 84_400_000, asset_start_us: 0 }] }),
    upload: async (_p: unknown, file: unknown) => {
      uploads.push(file as File);
      return { source_id: "s_up", asset_id: "a_up" };
    },
  });
});
afterEach(() => {
  cleanup();
  vi.resetModules();
});

const visibleOnPage = (el: Element | null) => !!el && !el.closest("[hidden]") && !el.closest(".collapsible-body");

it("keeps the link box and import button visible on both pages, outside any collapsible", async () => {
  localStorage.setItem("dongbi.page", "edit");
  const { default: App } = await import("../src/App");
  render(<App />);
  await screen.findByLabelText("影片來源連結");
  expect(visibleOnPage(screen.getByLabelText("影片來源連結"))).toBe(true);
  expect(visibleOnPage(screen.getByRole("button", { name: /加入下載/ }))).toBe(true);
  expect(visibleOnPage(screen.getByRole("button", { name: /匯入本機檔案/ }))).toBe(true);
  fireEvent.click(screen.getByRole("tab", { name: /字幕與校字/ }));
  await waitFor(() => expect(document.querySelector("main")!.className).toBe("mode-subtitles"));
  expect(visibleOnPage(screen.getByLabelText("影片來源連結"))).toBe(true);
  expect(visibleOnPage(screen.getByRole("button", { name: /匯入本機檔案/ }))).toBe(true);
});

it("uploads a finished audio file from the subtitle page and makes it the shared media", async () => {
  localStorage.setItem("dongbi.page", "subtitles");
  const { default: App } = await import("../src/App");
  render(<App />);
  await screen.findByRole("button", { name: /匯入本機檔案/ });
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File(["RIFF"], "剪好的音訊.wav", { type: "audio/wav" });
  fireEvent.change(input, { target: { files: [file] } });
  await waitFor(() => expect(uploads.length).toBe(1));
  // 上傳後：播放器載入該素材，轉錄按鈕可用
  await waitFor(() => expect(document.querySelector("video")?.getAttribute("src")).toContain("a_up"), { timeout: 5000 });
  const transcribe = screen.getByRole("button", { name: /^(重新)?轉錄$/ }) as HTMLButtonElement;
  await waitFor(() => expect(transcribe.disabled).toBe(false));
}, 15000);
