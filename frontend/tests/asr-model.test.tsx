// @vitest-environment jsdom
// 使用者 2026-09-19：補上 Breeze-ASR 轉錄中文；2026-09-20：26 移除，「預設 asr25 為預設第一個，再來次要是 v3」。
// 轉錄卡片的「精修模型」預設跟著服務（capabilities.default_asr_model），標「（預設）」；沒安裝的不能選、自動退回 large-v3；
// 快速草稿不做精修：選單變灰、不送 asr_model（不再把「快速草稿」鎖住）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { installAppMock } from "./helpers/appMock";

const jobCalls: Record<string, unknown>[] = [];
const state = { transcribing: false, installed: true, remoteReady: true, more: false };
beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("dongbi.project", "p1");
  localStorage.setItem("dongbi.page", "subtitles");
  jobCalls.length = 0;
  state.transcribing = false;
  state.installed = true;
  state.remoteReady = true;
  state.more = false;
  installAppMock({
    health: async () => ({ status: "ok" }),
    peaks: async () => ({ peaks: [] }),
    projects: async () => ({ items: [{ project_id: "p1", name: "Breeze" }] }),
    sequence: async () => ({ revision: "seq_00", source_id: "s1", items: [] }),
    sources: async () => ({ items: [{ source_id: "s1", kind: "local", title: "剪好的音訊.wav", asset_ids: ["a1"] }] }),
    asset: async () => ({ asset_id: "a1", source_id: "s1", kind: "audio", duration_us: 84_400_000, source_map: [{ source_start_us: 0, source_end_us: 84_400_000, asset_start_us: 0 }] }),
    capabilities: async () => ({
      output_roots: [],
      source_subtitles: false,
      default_asr_model: state.installed ? "breeze-asr-25" : "large-v3",
      asr_models: [
        { key: "large-v3", label: "Whisper large-v3", installed: true, download_bytes: 0 },
        { key: "breeze-asr-25", label: "Breeze-ASR-25（台語語音→中文字）", installed: state.installed, download_bytes: 6_175_389_907 },
        { key: "openrouter", label: "OpenRouter 遠端轉錄（openai/whisper-1）", installed: state.remoteReady, remote: true, timestamps: false, missing: state.remoteReady ? null : "secret" },
        // 2026-09-21：同一服務的其他轉錄模型與 ElevenLabs
        ...(state.more
          ? [
              { key: "openrouter:microsoft/mai-transcribe-2", label: "OpenRouter 遠端轉錄（microsoft/mai-transcribe-2）", installed: true, remote: true,
                timestamps: false, provider_label: "OpenRouter", remote_model: "microsoft/mai-transcribe-2" },
              { key: "elevenlabs", label: "ElevenLabs 遠端轉錄（scribe_v2）", installed: true, remote: true, timestamps: false,
                provider_label: "ElevenLabs", remote_model: "scribe_v2" },
            ]
          : []),
      ],
    }),
    jobs: async () => ({
      items: state.transcribing
        ? [{ job_id: "an1", kind: "analyze", status: "running", stage: "refine", body: { source_id: "s1", asr_model: jobCalls[0]?.asr_model } }]
        : [],
    }),
    job: async (_p: unknown, body: unknown) => {
      jobCalls.push(body as Record<string, unknown>);
      state.transcribing = true;
      return { job_id: "an1", kind: "analyze", status: "queued" };
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
  const select = (await screen.findByLabelText("精修模型")) as HTMLSelectElement;
  await waitFor(() => expect(select.querySelectorAll("option").length).toBe(state.more ? 5 : 3), { timeout: 5000 });
  const button = (await screen.findByRole("button", { name: /^(重新)?轉錄$/ })) as HTMLButtonElement;
  await waitFor(() => expect(button.disabled).toBe(false), { timeout: 5000 });
  return { select, button, profile: screen.getByLabelText("辨識品質") as HTMLSelectElement };
}
const option = (select: HTMLSelectElement, value: string) => select.querySelector(`option[value="${value}"]`) as HTMLOptionElement;

it("uses Breeze-ASR-25 by default for the refine pass and marks it as the default", async () => {
  const { select, button } = await setup();
  await waitFor(() => expect(select.value).toBe("breeze-asr-25"));
  expect(option(select, "breeze-asr-25").textContent).toContain("（預設）");
  expect(option(select, "large-v3").textContent).not.toContain("（預設）");
  fireEvent.click(button);
  await waitFor(() => expect(jobCalls.length).toBe(1));
  expect(jobCalls[0]).toMatchObject({ kind: "analyze", profile: "quality", asr_model: "breeze-asr-25" });
  await waitFor(() => expect(document.querySelector(".transcribe-state")?.textContent).toContain("Breeze-ASR-25 精修"), { timeout: 5000 });
}, 20000);

it("lets the user switch back to large-v3 and remembers it", async () => {
  const { select, button } = await setup();
  fireEvent.change(select, { target: { value: "large-v3" } });
  expect(localStorage.getItem("dongbi.asrModel")).toBe("large-v3");
  fireEvent.click(button);
  await waitFor(() => expect(jobCalls.length).toBe(1));
  expect(jobCalls[0]).toMatchObject({ profile: "quality", asr_model: "large-v3" });
}, 20000);

it("quick draft skips the refine pass: the model select greys out and no model is sent", async () => {
  const { select, button, profile } = await setup();
  expect(option(profile, "draft").disabled).toBe(false); // 預設是 Breeze 也能選快速草稿
  fireEvent.change(profile, { target: { value: "draft" } });
  await waitFor(() => expect(select.disabled).toBe(true));
  fireEvent.click(button);
  await waitFor(() => expect(jobCalls.length).toBe(1));
  expect(jobCalls[0].profile).toBe("draft");
  expect(jobCalls[0].asr_model).toBeUndefined();
}, 20000);

it("falls back to large-v3 when Breeze-ASR-25 is not installed", async () => {
  state.installed = false;
  localStorage.setItem("dongbi.asrModel", "breeze-asr-25");
  const { select, button } = await setup();
  await waitFor(() => expect(select.value).toBe("large-v3"));
  expect(option(select, "breeze-asr-25").disabled).toBe(true);
  expect(option(select, "breeze-asr-25").textContent).toContain("未安裝");
  expect(option(select, "large-v3").textContent).toContain("（預設）");
  fireEvent.click(button);
  await waitFor(() => expect(jobCalls.length).toBe(1));
  expect(jobCalls[0].asr_model).toBe("large-v3");
}, 20000);

// 2026-09-20：OpenRouter 遠端轉錄（聲音會送出本機）：要有金鑰、也要先在模型設定同意使用遠端服務，才可以選；送出時帶 remote_consent
it("offers OpenRouter transcription only with a key and remote consent, and sends the consent along", async () => {
  const { select, button } = await setup();
  const remote = option(select, "openrouter");
  expect(remote.disabled).toBe(true); // 還沒同意遠端服務
  expect(remote.textContent).toContain("同意");
  cleanup();
  vi.resetModules();
  localStorage.setItem("dongbi.remote_consent", "1");
  const again = await setup();
  expect(option(again.select, "openrouter").disabled).toBe(false);
  fireEvent.change(again.select, { target: { value: "openrouter" } });
  fireEvent.click(again.button);
  await waitFor(() => expect(jobCalls.length).toBe(1));
  expect(jobCalls[0]).toMatchObject({ profile: "quality", asr_model: "openrouter", remote_consent: true });
  void button;
}, 30000);

it("explains that the OpenRouter key is missing", async () => {
  state.remoteReady = false;
  localStorage.setItem("dongbi.remote_consent", "1");
  const { select } = await setup();
  const remote = option(select, "openrouter");
  expect(remote.disabled).toBe(true);
  expect(remote.textContent).toContain("金鑰");
}, 20000);

// 2026-09-21 使用者：新增 ElevenLabs 轉錄、OpenRouter 再新增 microsoft/mai-transcribe-2 → 精修模型清單各一個選項，註明音訊送去哪個服務
it("lists ElevenLabs and extra OpenRouter transcription models and sends the chosen key", async () => {
  state.more = true;
  localStorage.setItem("dongbi.remote_consent", "1");
  const { select, button } = await setup();
  expect(option(select, "elevenlabs").textContent).toContain("音訊會送到 ElevenLabs");
  const mai = option(select, "openrouter:microsoft/mai-transcribe-2");
  expect(mai.disabled).toBe(false);
  expect(mai.textContent).toContain("音訊會送到 OpenRouter");
  fireEvent.change(select, { target: { value: "elevenlabs" } });
  fireEvent.click(button);
  await waitFor(() => expect(jobCalls.length).toBe(1));
  expect(jobCalls[0]).toMatchObject({ profile: "quality", asr_model: "elevenlabs", remote_consent: true });
}, 30000);

// 2026-09-21 真瀏覽器：精修模型可以是 Breeze／Qwen／ElevenLabs，按鈕卻一直寫「轉錄（WhisperX）」→ 按鈕旁寫清楚這次實際會用什麼
it("says which draft and refine models the transcription will use", async () => {
  state.more = true;
  localStorage.setItem("dongbi.remote_consent", "1");
  const { select } = await setup();
  const plan = await screen.findByLabelText("這次轉錄會用");
  expect(plan.textContent).toContain("草稿 WhisperX turbo");
  expect(plan.textContent).toContain("Breeze-ASR-25");
  fireEvent.change(select, { target: { value: "elevenlabs" } });
  await waitFor(() => expect(plan.textContent).toContain("精修 ElevenLabs scribe_v2"));
  expect(plan.textContent).toContain("音訊會送到 ElevenLabs");
  fireEvent.change(screen.getByLabelText("辨識品質"), { target: { value: "draft" } });
  await waitFor(() => expect(plan.textContent).toContain("只做草稿"));
}, 30000);
