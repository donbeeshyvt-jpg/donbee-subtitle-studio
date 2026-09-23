// @vitest-environment jsdom
// 2026-09-20 使用者回報：用 API／CLI 刪掉專案後，開著的頁面仍列出已刪的專案（清單只在開頁時抓一次）。
// 回到這個視窗（focus）就要重抓；開著的專案若已不存在，要退回未開啟狀態，不能停在幽靈專案上。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

let pendingList: Promise<unknown> | null = null; // 設定時，下一次抓專案清單會等它（模擬慢回應）
let listed = [
  { project_id: "p1", name: "還在的專案" },
  { project_id: "p2", name: "等一下會被別的地方刪掉" },
];

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
    projects: async () => {
      const snapshot = listed; // 送出請求那一刻的清單
      if (pendingList) await pendingList;
      return { items: snapshot };
    },
    createProject: async (name: unknown) => {
      const created = { project_id: "p9", name: String(name) };
      listed = [...listed, created];
      return created;
    },
    sequence: async () => ({ revision: "seq_00", source_id: "", items: [] }),
    environment: async () => ({ items: [], python: {}, packages: [] }),
  };
  const api = new Proxy(
    {},
    {
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
  pendingList = null;
  localStorage.clear();
  localStorage.setItem("dongbi.project", "p2");
  listed = [
    { project_id: "p1", name: "還在的專案" },
    { project_id: "p2", name: "等一下會被別的地方刪掉" },
  ];
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("drops projects deleted elsewhere when the window comes back into focus", async () => {
  render(<App />);
  const picker = (await screen.findByLabelText("開啟專案")) as HTMLSelectElement;
  await waitFor(() => expect(picker.value).toBe("p2"));
  expect(screen.getByRole("option", { name: "等一下會被別的地方刪掉" })).toBeTruthy();

  listed = [{ project_id: "p1", name: "還在的專案" }]; // 別的地方（CLI／API）把 p2 刪了
  fireEvent.focus(window);

  await waitFor(() => expect(screen.queryByRole("option", { name: "等一下會被別的地方刪掉" })).toBeNull());
  await waitFor(() => expect(picker.value).toBe("")); // 開著的專案沒了就退回未開啟
  expect(localStorage.getItem("dongbi.project")).toBeNull();
});

// 2026-09-21 真瀏覽器：按「新專案」時視窗剛好取得焦點 → 重抓清單的請求比建立專案早送出、晚回來，
// 網頁拿舊清單判斷「新專案不存在」而清掉它，還顯示「這個專案已經不在了」（專案其實建好了，變成看不到的空專案）。
it("keeps a project created while an older list refresh is still in flight", async () => {
  localStorage.removeItem("dongbi.project");
  render(<App />);
  const picker = (await screen.findByLabelText("開啟專案")) as HTMLSelectElement;
  await screen.findByRole("option", { name: "還在的專案" });
  let release: (value?: unknown) => void = () => undefined;
  pendingList = new Promise((resolve) => (release = resolve));
  fireEvent.focus(window); // 舊清單的請求送出（還沒回來）
  fireEvent.change(screen.getByLabelText("專案名稱"), { target: { value: "新的交叉測試" } });
  fireEvent.click(screen.getByRole("button", { name: "新專案" }));
  await waitFor(() => expect(picker.value).toBe("p9"));
  release(); // 舊清單這時才回來（裡面沒有 p9）
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(picker.value).toBe("p9");
  expect(screen.queryByText(/這個專案已經不在了/)).toBeNull();
  expect(screen.getByRole("option", { name: "新的交叉測試" })).toBeTruthy();
  expect(localStorage.getItem("dongbi.project")).toBe("p9");
});
