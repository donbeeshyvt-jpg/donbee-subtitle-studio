// @vitest-environment jsdom
// 2026-09-18 真瀏覽器發現：按「新專案」後，上一個專案的逐字稿仍顯示在面板（openProject 有清、createProject 沒清）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

vi.mock("../src/api/client", () => {
  class ApiError extends Error {
    constructor(
      public status: number,
      public code: string,
      message: string,
    ) {
      super(message);
    }
  }
  const overrides: Record<string, (...args: unknown[]) => Promise<unknown>> = {
    health: async () => ({ status: "ok" }),
    projects: async () => ({ items: [{ project_id: "p1", name: "舊專案" }] }),
    createProject: async (name: unknown) => ({ project_id: "p2", name }),
    sequence: async () => ({ revision: "seq_00", source_id: "s1", items: [] }),
    sources: async () => ({
      items: [{ source_id: "s1", kind: "youtube", title: "來源", transcript_revision: "tr_1", asset_ids: [] }],
    }),
    transcript: async () => ({
      revision: "tr_1",
      source_id: "s1",
      cues: [{ cue_id: "c1", start_us: 0, end_us: 1_000_000, text: "舊專案的逐字稿句子" }],
    }),
    environment: async () => ({ items: [], python: {}, packages: [] }),
  };
  const api = new Proxy(
    {},
    {
      // 模擬真實網路：每個呼叫在下一個 macrotask 才回應，讓 React 有機會在回應之間重新渲染（微任務即回會讓 refresh 的專案檢查提早中止）
      get: (_target, name: string) => async (...args: unknown[]) => {
        await new Promise((resolve) => setTimeout(resolve, 0));
        return (overrides[name] ?? (async () => ({ items: [] })))(...args);
      },
    },
  );
  const request = async (path: string) =>
    path === "/capabilities" ? { output_roots: [], source_subtitles: false } : { items: [] };
  return { api, request, ApiError };
});

import App from "../src/App";

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("dongbi.project", "p1");
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("clears the previous project's transcript when a new project is created", async () => {
  render(<App />);
  // 舊專案的逐字稿載入後，空狀態標題消失（jsdom 的虛擬列表不會渲染句子本身，改以空狀態判斷）
  await waitFor(() => expect(screen.queryByText("從音訊開始理解")).toBeNull());
  expect(document.querySelector(".transcript-scroll > div[style]")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("專案名稱"), { target: { value: "新專案 A" } });
  const create = screen.getByRole("button", { name: /新專案/ }) as HTMLButtonElement;
  await waitFor(() => expect(create.disabled).toBe(false));
  fireEvent.click(create);
  const picker = screen.getByLabelText("開啟專案") as HTMLSelectElement;
  await waitFor(() => expect(picker.value).toBe("p2"));
  // 新專案沒有逐字稿：面板必須回到空狀態，不能殘留上一個專案的句子
  await screen.findByText("從音訊開始理解");
});

// 2026-09-21 真瀏覽器（模仿使用者）：上次停在「② 字幕與校字」，按「新專案」後還停在 ②，
// 下載範圍在 ① 看不到，提示卻寫「先在下方『取得素材』」→ 新專案一律回到 ①，空白提示講實際位置。
it("opens a new project on the download page and points to where downloads are set up", async () => {
  localStorage.setItem("dongbi.page", "subtitles");
  render(<App />);
  const tabs = await screen.findAllByRole("tab");
  await waitFor(() => expect(tabs[1].getAttribute("aria-selected")).toBe("true"));
  fireEvent.change(screen.getByLabelText("專案名稱"), { target: { value: "新專案 B" } });
  const create = screen.getByRole("button", { name: /新專案/ }) as HTMLButtonElement;
  await waitFor(() => expect(create.disabled).toBe(false));
  fireEvent.click(create);
  await waitFor(() => expect(screen.getAllByRole("tab")[0].getAttribute("aria-selected")).toBe("true"));
  expect(localStorage.getItem("dongbi.page")).toBe("edit");
  expect(screen.queryByText(/先在下方「取得素材」/)).toBeNull();
  expect(screen.getAllByText(/最上方貼上連結/).length).toBeGreaterThan(0);
});
