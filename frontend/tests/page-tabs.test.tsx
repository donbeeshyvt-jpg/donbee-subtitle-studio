// @vitest-environment jsdom
// 使用者 2026-09-18 第五輪：分兩頁——第 1 頁「下載與剪輯」（取得素材、預覽、片段、時間軸、輸出），
// 第 2 頁「字幕與校字」（轉錄、AI 分析、逐字稿、待確認建議）；素材、預覽與時間軸是兩頁共用的固定區塊。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { installAppMock } from "./helpers/appMock";

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("dongbi.project", "p1");
  installAppMock({
    health: async () => ({ status: "ok" }),
    peaks: async () => ({ peaks: [] }),
    projects: async () => ({ items: [{ project_id: "p1", name: "分頁" }] }),
    sequence: async () => ({ revision: "seq_00", source_id: "s1", items: [] }),
    sources: async () => ({ items: [{ source_id: "s1", kind: "youtube", title: "來源", asset_ids: ["a1"], transcript_revision: "tr_1" }] }),
    asset: async () => ({ asset_id: "a1", source_id: "s1", kind: "audio", duration_us: 600_000_000, source_map: [{ source_start_us: 6_600_000_000, source_end_us: 7_200_000_000, asset_start_us: 0 }] }),
    transcript: async () => ({ revision: "tr_1", source_id: "s1", cues: [{ cue_id: "c1", start_us: 6_600_000_000, end_us: 6_601_000_000, text: "第一句" }] }),
  });
});
afterEach(() => {
  cleanup();
  vi.resetModules();
});

// 字幕頁拆成上排（轉錄＋AI）、逐字稿欄、待確認建議列三個容器，以 data-page 判斷歸屬
const pageOf = (el: Element | null) => el?.closest("[data-page]") as HTMLElement | null;
const pageName = (el: Element | null) => pageOf(el)?.dataset.page ?? null;

it("starts on the download-and-edit page and switches to the subtitle page with shared media", async () => {
  const { default: App } = await import("../src/App");
  render(<App />);
  await screen.findByText("下載設定");
  const editPage = pageOf(screen.getByText("下載設定"));
  const subtitlePage = pageOf(document.querySelector(".transcript-panel"));
  expect(editPage).not.toBeNull();
  expect(subtitlePage).not.toBeNull();
  expect(editPage).not.toBe(subtitlePage);
  expect(editPage!.hidden).toBe(false);
  expect(subtitlePage!.hidden).toBe(true);
  // 第 1 頁：取得素材、片段清單、輸出；第 2 頁：轉錄、AI 分析（上排）、逐字稿（中排右側）、待確認建議（下排）
  expect(pageName(document.querySelector('section[data-section="segments"]'))).toBe("edit");
  expect(pageName(document.querySelector('section[data-section="export"]'))).toBe("edit");
  expect(pageName(document.querySelector('section[data-section="ai"]'))).toBe("subtitles");
  expect(pageName(document.querySelector(".transcribe-row"))).toBe("subtitles");
  expect(pageName(document.querySelector(".proposals-column"))).toBe("subtitles");
  // 第 2 頁的順序：上排（轉錄＋AI）→ 共用預覽 → 逐字稿 → 待確認建議
  const order = [".page-top", ".media-stage", ".page-transcript", ".proposals-column"].map((s) => document.querySelector(s)!);
  for (let i = 1; i < order.length; i += 1) expect(order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  // 素材、預覽與時間軸是兩頁共用的固定區塊（不在任何一頁裡）；版面位置由 main.mode-* 的 grid 決定
  const stage = document.querySelector(".media-stage")!;
  expect(pageOf(stage)).toBeNull();
  expect(stage.querySelector(".workspace")).toBeTruthy();
  expect(stage.querySelector(".timeline-panel")).toBeTruthy();
  expect(document.querySelector("main")!.className).toBe("mode-edit");
  fireEvent.click(screen.getByRole("tab", { name: /字幕與校字/ }));
  await waitFor(() => expect(subtitlePage!.hidden).toBe(false));
  expect(editPage!.hidden).toBe(true);
  expect((document.querySelector(".page-top") as HTMLElement).hidden).toBe(false);
  expect(document.querySelector("main")!.className).toBe("mode-subtitles");
  expect((document.querySelector(".proposals-column") as HTMLElement).hidden).toBe(false);
  expect(localStorage.getItem("dongbi.page")).toBe("subtitles");
  // 播放器只有一個（共用區塊），切頁後仍在
  await waitFor(() => expect(document.querySelectorAll("video").length).toBe(1), { timeout: 4000 });
  expect(pageOf(document.querySelector("video"))).toBeNull();
});
