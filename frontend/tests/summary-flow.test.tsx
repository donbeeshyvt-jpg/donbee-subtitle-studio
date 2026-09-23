// @vitest-environment jsdom
// 使用者 2026-09-18 第五輪：「產生摘要我不懂摘要產生在哪」→ 摘要狀態顯示在按鈕旁，完成後自動切到「摘要與重點」分頁；
// 「逐詞對齊」不再是按鈕：轉錄輸出時自動對齊，文字改了自動重新對齊，只顯示狀態。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { installAppMock } from "./helpers/appMock";

const state = { summarized: false, aligning: false };
const jobBodies: Record<string, unknown>[] = [];
beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("dongbi.project", "p1");
  localStorage.setItem("dongbi.page", "subtitles");
  state.summarized = false;
  state.aligning = false;
  jobBodies.length = 0;
  installAppMock({
    health: async () => ({ status: "ok" }),
    peaks: async () => ({ peaks: [] }),
    projects: async () => ({ items: [{ project_id: "p1", name: "摘要" }] }),
    providers: async () => ({ items: [{ id: "local-lmstudio", model: "g", local: true }] }),
    sequence: async () => ({ revision: "seq_00", source_id: "s1", items: [] }),
    sources: async () => ({ items: [{ source_id: "s1", kind: "youtube", title: "來源", asset_ids: ["a1"], transcript_revision: "tr_1" }] }),
    asset: async () => ({ asset_id: "a1", source_id: "s1", kind: "audio", duration_us: 600_000_000, source_map: [{ source_start_us: 6_600_000_000, source_end_us: 7_200_000_000, asset_start_us: 0 }] }),
    transcript: async () => ({ revision: "tr_1", source_id: "s1", cues: [{ cue_id: "c1", start_us: 6_600_000_000, end_us: 6_601_000_000, text: "第一句" }] }),
    annotations: async () => ({
      items: state.summarized
        ? [{ id: "an1", kind: "summary", title: "重點摘要", text: "這段在講合作", cue_ids: ["c1"], transcript_revision: "tr_1", spans: [{ start_us: 6_600_000_000, end_us: 6_601_000_000 }] }]
        : [],
    }),
    jobs: async () => ({
      items: [
        ...(state.summarized ? [{ job_id: "sm1", kind: "summarize", status: "succeeded", stage: "succeeded", body: { transcript_revision: "tr_1" }, result: { annotations: [{ kind: "summary" }] } }] : []),
        ...(state.aligning ? [{ job_id: "al1", kind: "align", status: "running", stage: "align", body: { transcript_revision: "tr_1" } }] : []),
      ],
    }),
    job: async (_p: unknown, body: unknown) => {
      jobBodies.push(body as Record<string, unknown>);
      if ((body as Record<string, unknown>).kind === "summarize") state.summarized = true;
      return { job_id: "sm1", kind: "summarize", status: "queued" };
    },
  });
});
afterEach(() => {
  cleanup();
  vi.resetModules();
});

it("shows summary progress next to the button and switches to the summary tab when done", async () => {
  const { default: App } = await import("../src/App");
  render(<App />);
  const button = (await screen.findByRole("button", { name: "產生摘要" })) as HTMLButtonElement;
  await waitFor(() => expect(button.disabled).toBe(false), { timeout: 4000 });
  fireEvent.click(button);
  await waitFor(() => expect(jobBodies.some((b) => b.kind === "summarize")).toBe(true));
  await waitFor(() => expect(document.querySelector(".summary-state")?.textContent).toContain("摘要完成"), { timeout: 6000 });
  const summaryTab = screen.getByRole("button", { name: /摘要與重點/ });
  await waitFor(() => expect(summaryTab.className).toContain("selected"));
  expect(screen.getByText("重點摘要")).toBeTruthy();
}, 15000);

it("has no manual alignment button and reports alignment as a status", async () => {
  state.aligning = true;
  const { default: App } = await import("../src/App");
  render(<App />);
  await screen.findByRole("button", { name: "產生摘要" });
  expect(screen.queryByRole("button", { name: /^(重新)?逐詞對齊$/ })).toBeNull();
  await waitFor(() => expect(document.querySelector(".alignment-state")?.textContent).toContain("逐詞對齊中"), { timeout: 6000 });
});
