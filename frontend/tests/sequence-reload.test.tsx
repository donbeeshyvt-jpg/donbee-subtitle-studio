// @vitest-environment jsdom
// 2026-09-18 M2-1 真跑發現：流程快照執行時後端會改寫剪輯清單（依範圍建立片段），面板仍拿著舊版本，
// 之後在面板分割片段並儲存 → 409「資料已更新」。沒有本機修改時，輪詢應自動載入最新剪輯清單。
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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
  let sequenceCalls = 0;
  const overrides: Record<string, (...args: unknown[]) => Promise<unknown>> = {
    health: async () => ({ status: "ok" }),
    projects: async () => ({ items: [{ project_id: "p1", name: "流程專案" }] }),
    // 第一次（開啟專案）回空的剪輯清單；之後（流程執行後）回後端建立的片段
    sequence: async () => {
      sequenceCalls += 1;
      return sequenceCalls === 1
        ? { revision: "seq_00", source_id: "s1", items: [] }
        : {
            revision: "seq_01",
            source_id: "s1",
            items: [{ id: "seg_1", kind: "clip", start_us: 6_600_000_000, end_us: 7_200_000_000, name: "片段 1", selected: true, tags: {} }],
          };
    },
    sources: async () => ({ items: [{ source_id: "s1", kind: "youtube", title: "來源", asset_ids: [] }] }),
    jobs: async () => ({ items: [{ job_id: "wf", kind: "workflow", status: "succeeded", stage: "succeeded", result: {} }] }),
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
  localStorage.clear();
  localStorage.setItem("dongbi.project", "p1");
});
afterEach(cleanup);

it("loads the sequence rewritten by a workflow when there are no local edits", async () => {
  render(<App />);
  await waitFor(() => expect(screen.getByLabelText("開啟專案")).toHaveProperty("value", "p1"));
  // 輪詢（2 秒）之後，片段清單的計數徽章應變成 1（jsdom 的虛擬列表不渲染列本身），且沒有 409 橫幅
  const badge = () => document.querySelector('section[data-section="segments"] .collapsible-toggle .count')?.textContent ?? null;
  expect(badge()).toBeNull();
  await waitFor(() => expect(badge()).toBe("1"), { timeout: 6000 });
  expect(document.querySelector(".error-banner")).toBeNull();
}, 10000);
