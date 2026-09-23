// App 層測試共用的 API 替身：每個呼叫在下一個 macrotask 才回應（像真實網路），
// overrides 可依測試情境改回應；state 讓測試在中途改變後端狀態（例如工作完成）。
import { vi } from "vitest";

export type Override = (...args: unknown[]) => Promise<unknown> | unknown;

export function installAppMock(overrides: Record<string, Override>) {
  vi.doMock("../../src/api/client", () => {
    class ApiError extends Error {
      constructor(
        public status: number,
        public code: string,
        message: string,
      ) {
        super(message);
      }
    }
    const api = new Proxy(
      {},
      {
        get: (_target, name: string) => async (...args: unknown[]) => {
          await new Promise((resolve) => setTimeout(resolve, 0));
          // 字幕預覽不是列表端點；預設替身必須遵守 entries／srt 契約。
          const handler = overrides[name] ?? (name === "subtitlePreview"
            ? async (_project: unknown, body: unknown) => ({
                transcript_revision: (body as { transcript_revision: string }).transcript_revision,
                alignment: "not_applicable", alignment_revision: null, timebase: "sequence",
                entries: [], srt: "", warnings: [],
              })
            : async () => ({ items: [] }));
          return handler(...args);
        },
      },
    );
    const request = async (path: string) => {
      if (path === "/capabilities") return overrides.capabilities ? overrides.capabilities() : { output_roots: [], source_subtitles: false };
      return { items: [] };
    };
    return { api, request, ApiError };
  });
}
