// @vitest-environment jsdom
// 2026-09-21 使用者：設定頁「應該只有確認連線跟填金鑰」→ 齒輪裡不再有第二個「校字與摘要使用」選單（舊的 id · model 格式），
// 選哪個模型只在「AI 分析、摘要與校字」區選；設定頁只剩三個來源的測試連線與 OpenRouter 金鑰。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("../src/api/client", () => {
  class ApiError extends Error {
    constructor(public status: number, public code: string, message: string) {
      super(message);
    }
  }
  const overrides: Record<string, (...args: unknown[]) => Promise<unknown>> = {
    health: async () => ({ status: "ok" }),
    projects: async () => ({ items: [] }),
    environment: async () => ({ items: [], python: {}, packages: [] }),
    providerList: async () => ({ items: [
      { id: "local-lmstudio", model: "auto", base_url: "http://127.0.0.1:1234/v1", local: true, allow_remote: false, secret_configured: false },
      { id: "local-llamacpp", model: "auto", base_url: "http://127.0.0.1:8080/v1", local: true, allow_remote: false, secret_configured: false },
      { id: "api-openrouter", model: "deepseek/deepseek-v4.1-flash", base_url: "https://openrouter.ai/api/v1", local: false, allow_remote: true, secret_configured: true },
    ] }),
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
  vi.restoreAllMocks();
});

it("keeps only connection tests and the OpenRouter key in the settings drawer", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "模型設定" }));
  await screen.findByRole("button", { name: "測試 LM Studio 連線" });
  expect(screen.queryByText("校字與摘要使用")).toBeNull();
  expect(screen.queryByRole("button", { name: "檢查連線" })).toBeNull();
  expect(screen.queryByText(/local-lmstudio · auto/)).toBeNull();
});
