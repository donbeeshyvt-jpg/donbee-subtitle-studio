// @vitest-environment jsdom
// 2026-09-21 真瀏覽器 B02：貼連結按「加入下載」而沒填範圍，會直接下載整支（2 小時）並自動轉錄，沒有任何確認。
// 沒指定範圍時先問一次；說不要就什麼都不送。有填範圍就照常直接送。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

const jobs: Record<string, unknown>[] = [];
vi.mock("../src/api/client", () => {
  class ApiError extends Error {
    constructor(public status: number, public code: string, message: string) {
      super(message);
    }
  }
  const overrides: Record<string, (...args: unknown[]) => Promise<unknown>> = {
    health: async () => ({ status: "ok" }),
    projects: async () => ({ items: [{ project_id: "p1", name: "測試" }] }),
    sequence: async () => ({ revision: "seq_00", source_id: "", items: [] }),
    environment: async () => ({ items: [], python: {}, packages: [] }),
    source: async () => ({ source_id: "s1" }),
    job: async (_p: unknown, body: unknown) => {
      jobs.push(body as Record<string, unknown>);
      return { job_id: "j1", status: "queued" };
    },
  };
  const api = new Proxy({}, {
    get: (_t, name: string) => async (...args: unknown[]) => {
      await new Promise((r) => setTimeout(r, 0));
      return (overrides[name] ?? (async () => ({ items: [] })))(...args);
    },
  });
  const request = async (path: string) => (path === "/capabilities" ? { output_roots: [], source_subtitles: false } : { items: [] });
  return { api, request, ApiError };
});

import App from "../src/App";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  jobs.length = 0;
  localStorage.clear();
});

it("asks before downloading a whole video when no range was given", async () => {
  localStorage.setItem("dongbi.project", "p1");
  render(<App />);
  const link = await screen.findByLabelText("影片來源連結");
  await waitFor(() => expect((screen.getByRole("button", { name: /加入下載/ }) as HTMLButtonElement).disabled).toBe(false));
  vi.stubGlobal("confirm", vi.fn(() => false));
  fireEvent.change(link, { target: { value: "https://www.youtube.com/watch?v=Yn2mE6_tMC8" } });
  fireEvent.click(screen.getByRole("button", { name: /加入下載/ }));
  await waitFor(() => expect(window.confirm).toHaveBeenCalled());
  expect((window.confirm as unknown as { mock: { calls: string[][] } }).mock.calls[0][0]).toMatch(/整支/);
  await new Promise((r) => setTimeout(r, 30));
  expect(jobs).toEqual([]); // 說不要就不送
});

// 2026-09-21 真瀏覽器：勾「下載完成後自動轉錄」時，自動排的轉錄沒有帶「精修模型」與「轉錄術語提示」→ 一律用預設
it("sends the chosen refine model and hints along with auto-transcribe", async () => {
  localStorage.setItem("dongbi.project", "p1");
  localStorage.setItem("dongbi.page", "edit");
  render(<App />);
  const link = await screen.findByLabelText("影片來源連結");
  await waitFor(() => expect((screen.getByRole("button", { name: /加入下載/ }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText("下載時間範圍"), { target: { value: "00:05:00-00:10:00" } });
  fireEvent.change(screen.getByLabelText("轉錄術語提示"), { target: { value: "小尹, 初代" } });
  fireEvent.change(link, { target: { value: "https://www.youtube.com/watch?v=9TeJznbG-y0" } });
  fireEvent.click(screen.getByRole("button", { name: /加入下載/ }));
  await waitFor(() => expect(jobs.length).toBe(1));
  const follow = (jobs[0] as { follow_up?: Record<string, unknown> }).follow_up;
  expect(follow).toMatchObject({ kind: "analyze", profile: "quality", asr_model: "large-v3", asr_hints: "小尹, 初代" });
});
