import { describe, it, expect, vi, beforeEach } from "vitest";
describe("HTTP 契約與真實失敗狀態", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
  });
  it("先建立同源工作階段，錯誤409保留錯誤型別，不回假成功", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response("{}", { status: 200 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            error: { code: "REVISION_CONFLICT", message: "版本已更新" },
          }),
          { status: 409 },
        ),
      );
    vi.stubGlobal("fetch", fetch);
    const { api } = await import("../src/api/client");
    await expect(api.sequence("p")).rejects.toMatchObject({
      status: 409,
      code: "REVISION_CONFLICT",
    });
    expect(fetch.mock.calls[0][0]).toBe("/v1/session");
    expect(fetch.mock.calls[1][1].credentials).toBe("same-origin");
  });
  it("長逐字稿逐頁讀取並使用人工接受文字與原始句ID", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response("{}"))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            revision: "tr",
            source_id: "s",
            cues: [
              {
                id: "c1",
                start_us: 0,
                end_us: 10,
                text: "原稿",
                accepted_text: "修正",
              },
            ],
            next_cursor: 200,
          }),
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            revision: "tr",
            source_id: "s",
            cues: [{ id: "c2", start_us: 10, end_us: 20, text: "結尾" }],
            next_cursor: null,
          }),
        ),
      );
    vi.stubGlobal("fetch", fetch);
    const { api } = await import("../src/api/client");
    const t = await api.transcript("p", "tr");
    expect(t.cues.map((c) => [c.cue_id, c.text])).toEqual([
      ["c1", "修正"],
      ["c2", "結尾"],
    ]);
    expect(fetch.mock.calls[2][0]).toContain("cursor=200");
  });
});

// 使用者 2026-09-19：頁面開著超過 12 小時後，所有操作都跳「尚未建立本機工作階段」。
// 原因：前端只在載入時拿一次工作階段 cookie（12 小時有效），過期後不會重拿。
describe("工作階段過期會自動重新建立", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
  });
  const expired = () =>
    new Response(JSON.stringify({ error: { code: "ACCESS_DENIED", message: "尚未建立本機工作階段" } }), { status: 401 });
  it("401 時重新建立工作階段並重送同一個請求（含內容）一次", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response("{}"))
      .mockResolvedValueOnce(expired())
      .mockResolvedValueOnce(new Response("{}"))
      .mockResolvedValueOnce(new Response(JSON.stringify({ path: "D:\輸出", cancelled: false })));
    vi.stubGlobal("fetch", fetch);
    const { api } = await import("../src/api/client");
    await expect(api.pickFolder("選擇資料夾")).resolves.toEqual({ path: "D:\輸出", cancelled: false });
    expect(fetch.mock.calls.map((c) => c[0])).toEqual(["/v1/session", "/v1/dialogs/folder", "/v1/session", "/v1/dialogs/folder"]);
    expect(fetch.mock.calls[3][1].body).toBe(fetch.mock.calls[1][1].body);
  });
  it("重建後仍被拒：回清楚的中文訊息，不無限重試", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response("{}"))
      .mockResolvedValueOnce(expired())
      .mockResolvedValueOnce(new Response("{}"))
      .mockResolvedValueOnce(expired());
    vi.stubGlobal("fetch", fetch);
    const { api } = await import("../src/api/client");
    await expect(api.projects()).rejects.toMatchObject({ status: 401, code: "SESSION_EXPIRED", message: expect.stringContaining("重新整理") });
    expect(fetch).toHaveBeenCalledTimes(4);
  });
});
