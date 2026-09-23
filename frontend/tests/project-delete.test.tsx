// @vitest-environment jsdom
// 刪除專案（2026-09-20 使用者：「工作室的專案整理一下」）：頁面要能刪，而且一定先二次確認，刪完清單要更新。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const deleted: string[] = [];
let listed = [
  { project_id: "p1", name: "要刪的專案" },
  { project_id: "p2", name: "要留的專案" },
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
    projects: async () => ({ items: listed }),
    sequence: async () => ({ revision: "seq_00", source_id: "", items: [] }),
    environment: async () => ({ items: [], python: {}, packages: [] }),
    deleteProject: async (id: unknown) => {
      deleted.push(id as string);
      listed = listed.filter((p) => p.project_id !== id);
      return { project_id: id, removed_entities: 9, removed_jobs: 3, removed_artifacts: 2, freed_bytes: 2_500_000 };
    },
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
  deleted.length = 0;
  listed = [
    { project_id: "p1", name: "要刪的專案" },
    { project_id: "p2", name: "要留的專案" },
  ];
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("asks before deleting the open project and drops it from the list", async () => {
  render(<App />);
  const picker = (await screen.findByLabelText("開啟專案")) as HTMLSelectElement;
  await waitFor(() => expect(picker.value).toBe("p1"));

  vi.stubGlobal("confirm", vi.fn(() => false));
  fireEvent.click(screen.getByRole("button", { name: "刪除專案" }));
  await waitFor(() => expect(window.confirm).toHaveBeenCalled());
  expect((window.confirm as unknown as { mock: { calls: string[][] } }).mock.calls[0][0]).toContain("要刪的專案");
  expect(deleted).toEqual([]); // 說不要就真的不刪

  vi.stubGlobal("confirm", vi.fn(() => true));
  fireEvent.click(screen.getByRole("button", { name: "刪除專案" }));
  await waitFor(() => expect(deleted).toEqual(["p1"]));
  await waitFor(() => expect(picker.value).toBe("")); // 刪掉的專案不再是開啟中
  await waitFor(() => expect(screen.queryByRole("option", { name: "要刪的專案" })).toBeNull());
});
