// @vitest-environment jsdom
// 使用者 2026-09-20：
// 3.「依上下文校字並直接套用」按了逐字稿沒變化 → 按鈕旁要說清楚結果（檢查幾句、套用幾處、低信心／被擋幾處，或「沒有需要修正的地方」）。
// 4. 校字詞彙：可匯入文字檔、逗號或空格分隔；可貼故事大綱（參考資料）讓模型參考用詞。
// 5. 轉錄術語提示：另一個欄位，送給轉錄模型；逗號分隔、可匯入文字檔；「內容拆解單詞」用目前的文字模型把內容整理成逗號分隔關鍵詞。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { installAppMock } from "./helpers/appMock";

const jobCalls: Record<string, unknown>[] = [];
const keywordCalls: unknown[] = [];
const state = { correctJob: null as Record<string, unknown> | null };
const ids = Array.from({ length: 51 }, (_, i) => `c${i}`);
beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("dongbi.project", "p1");
  localStorage.setItem("dongbi.page", "subtitles");
  jobCalls.length = 0;
  keywordCalls.length = 0;
  state.correctJob = null;
  installAppMock({
    health: async () => ({ status: "ok" }),
    peaks: async () => ({ peaks: [] }),
    projects: async () => ({ items: [{ project_id: "p1", name: "詞彙" }] }),
    providers: async () => ({ items: [{ id: "local-lmstudio", model: "auto", local: true },
                                      { id: "local-llamacpp", model: "auto", local: true },
                                      { id: "api-openrouter", model: "openai/gpt-4o-mini", local: false, secret_configured: false }] }),
    sequence: async () => ({ revision: "seq_00", source_id: "s1", items: [] }),
    sources: async () => ({ items: [{ source_id: "s1", kind: "local", title: "EP7.wav", asset_ids: ["a1"], transcript_revision: "tr_1" }] }),
    asset: async () => ({ asset_id: "a1", source_id: "s1", kind: "audio", duration_us: 84_400_000, source_map: [{ source_start_us: 0, source_end_us: 84_400_000, asset_start_us: 0 }] }),
    transcript: async () => ({ revision: "tr_1", source_id: "s1", cues: [{ cue_id: "c0", start_us: 0, end_us: 1_000_000, text: "彈步遊戲" }] }),
    capabilities: async () => ({ output_roots: [], source_subtitles: false, default_asr_model: "large-v3", asr_models: [{ key: "large-v3", label: "Whisper large-v3", installed: true, download_bytes: 0 }] }),
    jobs: async () => ({ items: state.correctJob ? [state.correctJob] : [] }),
    job: async (_p: unknown, body: unknown) => {
      jobCalls.push(body as Record<string, unknown>);
      return { job_id: `j${jobCalls.length}`, kind: (body as Record<string, unknown>).kind, status: "queued" };
    },
    probeProvider: async (id: unknown) =>
      id === "api-openrouter"
        ? { provider_id: id, status: "secret_missing", model: "openai/gpt-4o-mini", detail: "PROVIDER_SECRET_MISSING" }
        : { provider_id: id, status: "ready", model: "google/gemma-4-e4b", auto_model: true, models: ["google/gemma-4-e4b"] },
    keywords: async (body: unknown) => {
      keywordCalls.push(body);
      return { keywords: ["彭彭", "斯斯", "PICO PARK"], joined: "彭彭, 斯斯, PICO PARK", method: "llm", model: "google/gemma-4-e4b" };
    },
  });
});
afterEach(() => {
  cleanup();
  vi.resetModules();
});

async function setup() {
  const { default: App } = await import("../src/App");
  render(<App />);
  const correct = (await screen.findByRole("button", { name: /依上下文校字/ })) as HTMLButtonElement;
  await waitFor(() => expect(correct.disabled).toBe(false), { timeout: 5000 });
  return correct;
}
const upload = (input: HTMLElement, text: string, name = "terms.txt") =>
  fireEvent.change(input, { target: { files: [new File([text], name, { type: "text/plain" })] } });

it("sends space-separated glossary terms and the reference outline with the correction", async () => {
  const correct = await setup();
  fireEvent.change(screen.getByLabelText("詞彙提示"), { target: { value: "彭彭 斯斯  海海" } });
  fireEvent.change(screen.getByLabelText("校字參考資料"), { target: { value: "本集三人玩 PICO PARK，彭彭負責開門。" } });
  fireEvent.click(correct);
  await waitFor(() => expect(jobCalls.some((b) => b.kind === "correct")).toBe(true));
  const body = jobCalls.find((b) => b.kind === "correct")!;
  expect(body.glossary).toEqual(["彭彭", "斯斯", "海海"]);
  expect(body.reference_text).toBe("本集三人玩 PICO PARK，彭彭負責開門。");
  // 說明怎麼送進模型
  expect(screen.getByText(/context\.glossary/)).toBeTruthy();
}, 15000);

it("imports glossary and reference text files", async () => {
  await setup();
  fireEvent.change(screen.getByLabelText("詞彙提示"), { target: { value: "彭彭" } });
  upload(screen.getByLabelText("匯入詞彙文字檔"), "斯斯、海海\n彭彭");
  await waitFor(() => expect((screen.getByLabelText("詞彙提示") as HTMLTextAreaElement).value).toBe("彭彭, 斯斯, 海海"));
  upload(screen.getByLabelText("匯入參考資料文字檔"), "故事大綱：三人合作闖關。", "outline.txt");
  await waitFor(() => expect((screen.getByLabelText("校字參考資料") as HTMLTextAreaElement).value).toBe("故事大綱：三人合作闖關。"));
}, 15000);

it("sends ASR term hints with the transcription and splits free text into keywords with the current model", async () => {
  await setup();
  const hints = screen.getByLabelText("轉錄術語提示") as HTMLTextAreaElement;
  fireEvent.change(hints, { target: { value: "本集彭彭和斯斯玩 PICO PARK" } });
  fireEvent.click(screen.getByRole("button", { name: "內容拆解單詞" }));
  await waitFor(() => expect(hints.value).toBe("彭彭, 斯斯, PICO PARK"));
  expect(keywordCalls[0]).toEqual({ text: "本集彭彭和斯斯玩 PICO PARK", provider_id: "local-lmstudio", remote_consent: false });
  expect(screen.getByText(/google\/gemma-4-e4b.*3 個詞/)).toBeTruthy();
  upload(screen.getByLabelText("匯入術語文字檔"), "海海 天照堂");
  await waitFor(() => expect(hints.value).toBe("彭彭, 斯斯, PICO PARK, 海海, 天照堂"));
  fireEvent.click(screen.getByRole("button", { name: /^(重新)?轉錄$/ }));
  await waitFor(() => expect(jobCalls.some((b) => b.kind === "analyze")).toBe(true));
  expect(jobCalls.find((b) => b.kind === "analyze")!.asr_hints).toBe("彭彭, 斯斯, PICO PARK, 海海, 天照堂");
}, 15000);

it("explains a correction that found nothing to change instead of staying silent", async () => {
  state.correctJob = {
    job_id: "cj1", kind: "correct", status: "succeeded", stage: "succeeded", body: { transcript_revision: "tr_1" },
    result: { patches: [], rejected_patches: [], ignored_no_change: 2, sent_cue_ids: [ids.slice(0, 30), ids.slice(30)], chunks: 2, quality_status: "unverified" },
  };
  await setup();
  await screen.findByText(/校字完成：檢查 51 句，沒有找到需要修正的地方（2 處只差標點，已忽略）/, undefined, { timeout: 5000 });
}, 15000);

it("summarizes applied, low-confidence and blocked corrections", async () => {
  state.correctJob = {
    job_id: "cj2", kind: "correct", status: "succeeded", stage: "succeeded", body: { transcript_revision: "tr_1" },
    result: {
      patches: [
        { cue_id: "c0", original_text: "彈步遊戲", replacement_text: "彈幕遊戲", reason: "同音", confidence: "high", base_revision: "tr_1" },
        { cue_id: "c1", original_text: "我在你", replacement_text: "我跟你", reason: "推測", confidence: "low", base_revision: "tr_1" },
      ],
      rejected_patches: [{ cue_id: "c2", rejection_reasons: ["stylistic_rewrite"] }], ignored_no_change: 0, sent_cue_ids: [ids], chunks: 1,
    },
  };
  await setup();
  await screen.findByText(/校字完成：檢查 51 句；高信心 1 處、低信心 1 處待確認、被擋 1 處/, undefined, { timeout: 5000 });
}, 15000);

it("says which text model split the words and follows the AI section selection", async () => {
  await setup();
  const hints = screen.getByLabelText("轉錄術語提示") as HTMLTextAreaElement;
  fireEvent.change(hints, { target: { value: "本集彭彭和斯斯玩 PICO PARK" } });
  // 還沒按：先寫明會用哪個模型（跟「AI 分析、摘要與校字」同一個）
  expect(screen.getByText(/拆解會用「AI 分析、摘要與校字」選的模型：LM Studio（本機，跟著載入的模型）/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "內容拆解單詞" }));
  await screen.findByText(/用 local-lmstudio · google\/gemma-4-e4b 拆出 3 個詞/);
}, 15000);

it("keeps the OpenRouter port selectable and shows what the backend has configured", async () => {
  await setup();
  const select = screen.getByLabelText("校字與摘要模型") as HTMLSelectElement;
  expect([...select.options].some((o) => o.value === "api-openrouter" && !o.disabled)).toBe(true);
  fireEvent.change(select, { target: { value: "api-openrouter" } });
  await screen.findByText(/後台未填金鑰/, undefined, { timeout: 5000 });
}, 15000);

it("sends the chosen correction strength", async () => {
  const correct = await setup();
  const strength = screen.getByLabelText("校字強度") as HTMLSelectElement;
  expect(strength.value).toBe("conservative");
  fireEvent.change(strength, { target: { value: "rewrite" } });
  fireEvent.click(correct);
  await waitFor(() => expect(jobCalls.some((b) => b.kind === "correct")).toBe(true));
  expect(jobCalls.find((b) => b.kind === "correct")!.correction_mode).toBe("rewrite");
  expect(correct.textContent).toContain("逐句改寫");
}, 15000);

it("offers only the three model ports and tests the connection to show what is loaded", async () => {
  await setup();
  const select = screen.getByLabelText("校字與摘要模型") as HTMLSelectElement;
  expect([...select.options].map((o) => o.value)).toEqual(["", "local-lmstudio", "local-llamacpp", "api-openrouter"]);
  expect([...select.options].map((o) => o.textContent)).toEqual([
    "抽取式摘要（不使用文字模型）",
    "LM Studio（本機，跟著載入的模型）",
    "llama.cpp（本機，跟著載入的模型）",
    "OpenRouter（遠端 API，需金鑰）",
  ]);
  fireEvent.click(screen.getByRole("button", { name: "測試連線" }));
  await screen.findByText(/已連線：google\/gemma-4-e4b/, undefined, { timeout: 5000 });
  // 遠端：看後台有沒有金鑰、用哪一個模型
  const setSel = (el: HTMLSelectElement, v: string) => fireEvent.change(el, { target: { value: v } });
  setSel(select, "api-openrouter");
  await screen.findByText(/後台未填金鑰/, undefined, { timeout: 5000 });
}, 15000);
