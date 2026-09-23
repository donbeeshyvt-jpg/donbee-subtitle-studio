// @vitest-environment jsdom
// 2026-09-21 真瀏覽器（模仿使用者換一支影片開新專案）：「詞彙提示」「轉錄術語提示」還留著上一支影片的人名（彭彭、斯斯、海海、PICO PARK），
// 會送進這支影片的轉錄與校字（提示詞規定詞彙表的近音字一定要改）→ 兩個欄位改成每個專案各自一份，新專案從空白開始（校字參考資料本來就是）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { installAppMock } from "./helpers/appMock";

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("dongbi.project", "p1");
  localStorage.setItem("dongbi.page", "subtitles");
  localStorage.setItem("dongbi.glossary", "舊的全域詞彙"); // 舊版存的全域值：不再帶進任何專案
  localStorage.setItem("dongbi.asrHints", "舊的全域提示");
  localStorage.setItem("dongbi.glossary.p1", "彭彭, 斯斯");
  localStorage.setItem("dongbi.asrHints.p1", "PICO PARK");
  installAppMock({
    health: async () => ({ status: "ok" }),
    peaks: async () => ({ peaks: [] }),
    projects: async () => ({ items: [{ project_id: "p1", name: "EP7" }, { project_id: "p2", name: "星之卡比" }] }),
    sequence: async () => ({ revision: "seq_00", source_id: "", items: [] }),
    sources: async () => ({ items: [] }),
  });
});
afterEach(() => {
  cleanup();
  vi.resetModules();
});

it("keeps glossary and ASR hints per project and starts other projects empty", async () => {
  const { default: App } = await import("../src/App");
  render(<App />);
  const glossary = (await screen.findByLabelText("詞彙提示")) as HTMLTextAreaElement;
  const hints = screen.getByLabelText("轉錄術語提示") as HTMLTextAreaElement;
  await waitFor(() => expect(glossary.value).toBe("彭彭, 斯斯"));
  expect(hints.value).toBe("PICO PARK");
  const picker = screen.getByLabelText("開啟專案") as HTMLSelectElement;
  await screen.findByRole("option", { name: "星之卡比" });
  fireEvent.change(picker, { target: { value: "p2" } });
  await waitFor(() => expect(glossary.value).toBe(""));
  expect(hints.value).toBe("");
  fireEvent.change(glossary, { target: { value: "小尹, 初代, 卡比" } });
  expect(localStorage.getItem("dongbi.glossary.p2")).toBe("小尹, 初代, 卡比");
  fireEvent.change(picker, { target: { value: "p1" } });
  await waitFor(() => expect(glossary.value).toBe("彭彭, 斯斯"));
  expect(localStorage.getItem("dongbi.glossary")).toBeNull(); // 舊的全域值清掉，不會再冒出來
}, 15000);
