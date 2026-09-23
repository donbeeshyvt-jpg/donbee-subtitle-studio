// @vitest-environment jsdom
// 文字模型供應者面板（2026-09-21 使用者重新整理）：
// 「應該只有確認連線跟填金鑰；本地（LM Studio）只要確認有沒有連線、載入哪個就顯示哪個，其他都拿掉，不用特別顯示用甚麼模型；
//   只有 OpenRouter 可以填入存好 API、測試通過後，像我上面那樣填名稱新增」
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import ProviderPanel from "../src/components/ProviderPanel";
import type { ProviderInfo } from "../src/api/types";
afterEach(cleanup);

const base = {
  adapter: "openai_compatible" as const, gpu_ownership: "external" as const, response_format_mode: "json_schema" as const,
  timeout_sec: 600, max_retries: 1 as const, max_context_chars: 12000, max_output_tokens: 2048, api_key_env: null,
};
const items: ProviderInfo[] = [
  { ...base, id: "local-lmstudio", model: "auto", base_url: "http://127.0.0.1:1234/v1", allow_remote: false, local: true, secret_configured: false },
  { ...base, id: "local-llamacpp", model: "auto", base_url: "http://127.0.0.1:8080/v1", allow_remote: false, local: true, secret_configured: false },
  { ...base, id: "api-openrouter", model: "deepseek/deepseek-v4.1-flash", transcription_model: "meta/muse-voice-transcribe-1.0",
    base_url: "https://openrouter.ai/api/v1", allow_remote: true, local: false, api_key_env: "OPENROUTER_API_KEY", secret_configured: false,
    gpu_ownership: "cpu", max_context_chars: 120000, max_output_tokens: 8192 },
  // 2026-09-21 使用者新增 ElevenLabs（只做轉錄）
  { ...base, id: "api-elevenlabs", adapter: "elevenlabs", model: "scribe_v2", transcription_model: "scribe_v2", response_format_mode: "text",
    base_url: "https://api.elevenlabs.io/v1", allow_remote: true, local: false, api_key_env: "ELEVENLABS_API_KEY", secret_configured: false,
    gpu_ownership: "cpu" },
  // 舊設定檔裡的其他項目：面板不再顯示
  { ...base, id: "local-lmstudio-3b", model: "dongbi-qwen3b-cpu", base_url: "http://127.0.0.1:1234/v1", allow_remote: false, local: true, secret_configured: false },
  { ...base, id: "api-deepseek", model: "deepseek-flash", base_url: "https://api.deepseek.com", allow_remote: true, local: false, secret_configured: false },
];

function setup(overrides: Partial<Parameters<typeof ProviderPanel>[0]> = {}) {
  const props = {
    list: vi.fn().mockResolvedValue({ items }),
    probe: vi.fn().mockResolvedValue({ status: "ready", model: "google/gemma-4-e4b", auto_model: true, checked_at: "2026-09-21T10:00:00+00:00" }),
    setSecret: vi.fn().mockResolvedValue({ id: "api-openrouter", secret_configured: true }),
    deleteSecret: vi.fn().mockResolvedValue({ id: "api-openrouter", secret_configured: false }),
    save: vi.fn().mockResolvedValue({}),
    consent: true,
    onConsent: vi.fn(),
    ...overrides,
  };
  render(<ProviderPanel {...props} />);
  return props;
}

it("shows only LM Studio, llama.cpp, OpenRouter and ElevenLabs, without edit or remove buttons", async () => {
  setup();
  await screen.findByText("LM Studio");
  expect(screen.getByText("llama.cpp")).toBeTruthy();
  expect(screen.getByText("OpenRouter")).toBeTruthy();
  expect(screen.getByText("ElevenLabs")).toBeTruthy();
  expect(screen.queryByText(/local-lmstudio-3b|dongbi-qwen3b-cpu|api-deepseek/)).toBeNull(); // 舊項目不顯示
  expect(screen.queryByRole("button", { name: /編輯|移除|新增供應者/ })).toBeNull();
  // 本機不顯示設定的模型字串（auto），測試連線後才顯示實際載入的模型
  expect(screen.queryByText("auto")).toBeNull();
});

it("tests a local connection and shows which model is loaded", async () => {
  const props = setup();
  fireEvent.click(await screen.findByRole("button", { name: "測試 LM Studio 連線" }));
  await screen.findByText("已連線：google/gemma-4-e4b（目前載入）");
  expect(props.probe).toHaveBeenCalledWith("local-lmstudio");
});

it("explains a local service that is not running", async () => {
  setup({ probe: vi.fn().mockResolvedValue({ status: "service_unreachable", checked_at: "x" }) });
  fireEvent.click(await screen.findByRole("button", { name: "測試 llama.cpp 連線" }));
  await screen.findByText(/沒有連上.*llama\.cpp/);
});

it("saves the OpenRouter key write-only", async () => {
  const props = setup();
  const input = (await screen.findByLabelText("OpenRouter 金鑰")) as HTMLInputElement;
  expect(input.type).toBe("password");
  fireEvent.change(input, { target: { value: "sk-or-v1-" + "a".repeat(64) } });
  fireEvent.click(screen.getByRole("button", { name: "儲存 OpenRouter 金鑰" }));
  await waitFor(() => expect(props.setSecret).toHaveBeenCalledWith("api-openrouter", "sk-or-v1-" + "a".repeat(64)));
  await waitFor(() => expect(input.value).toBe("")); // 存完不回顯
  expect(await screen.findByText("已存金鑰")).toBeTruthy();
});

it("lets you set OpenRouter model names only after the connection test passes", async () => {
  const probe = vi.fn().mockResolvedValue({ status: "ready", model: "deepseek/deepseek-v4.1-flash", checked_at: "x" });
  const props = setup({ probe, list: vi.fn().mockResolvedValue({ items: items.map((p) => (p.id === "api-openrouter" ? { ...p, secret_configured: true } : p)) }) });
  const language = (await screen.findByLabelText("語言模型（AI 分析、校字）")) as HTMLInputElement;
  expect(language.disabled).toBe(true); // 還沒測試通過不能改
  fireEvent.click(screen.getByRole("button", { name: "測試 OpenRouter 連線" }));
  await screen.findByText("已連線：金鑰可用，deepseek/deepseek-v4.1-flash");
  await waitFor(() => expect(language.disabled).toBe(false));
  fireEvent.change(language, { target: { value: "deepseek/deepseek-v4.1-flash" } });
  fireEvent.change(screen.getByLabelText("OpenRouter 轉錄模型"), { target: { value: "nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b" } });
  fireEvent.click(screen.getByRole("button", { name: "儲存 OpenRouter 模型" }));
  await waitFor(() => expect(props.save).toHaveBeenCalled());
  const [saved, isNew] = (props.save as ReturnType<typeof vi.fn>).mock.calls[0];
  expect(isNew).toBe(false);
  expect(saved).toMatchObject({ id: "api-openrouter", model: "deepseek/deepseek-v4.1-flash",
                                transcription_model: "nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b" });
});

it("keeps the remote consent switch next to OpenRouter", async () => {
  const props = setup({ consent: false });
  fireEvent.click(await screen.findByLabelText(/同意把逐字稿／音訊送到 OpenRouter/));
  expect(props.onConsent).toHaveBeenCalledWith(true);
});

it("says plainly when the OpenRouter account has no credit left", async () => {
  setup({ probe: vi.fn().mockResolvedValue({ status: "credits_exhausted", checked_at: "x" }),
          list: vi.fn().mockResolvedValue({ items: items.map((p) => (p.id === "api-openrouter" ? { ...p, secret_configured: true } : p)) }) });
  fireEvent.click(await screen.findByRole("button", { name: "測試 OpenRouter 連線" }));
  await screen.findByText(/帳戶餘額不足/);
});

// 2026-09-21 使用者：「新增 ElevenLabs 轉錄 API（跟別的轉錄設定一樣）」→ 同樣只做「填金鑰、測試連線、填轉錄模型名稱」；它只做轉錄，沒有語言模型欄位
it("gives ElevenLabs its own write-only key and only a transcription model", async () => {
  const props = setup();
  const input = (await screen.findByLabelText("ElevenLabs 金鑰")) as HTMLInputElement;
  expect(input.type).toBe("password");
  expect(screen.getAllByLabelText("語言模型（AI 分析、校字）")).toHaveLength(1); // 只有 OpenRouter 有
  expect((screen.getByLabelText("ElevenLabs 轉錄模型") as HTMLInputElement).value).toBe("scribe_v2");
  fireEvent.change(input, { target: { value: "sk_" + "b".repeat(48) } });
  fireEvent.click(screen.getByRole("button", { name: "儲存 ElevenLabs 金鑰" }));
  await waitFor(() => expect(props.setSecret).toHaveBeenCalledWith("api-elevenlabs", "sk_" + "b".repeat(48)));
  await waitFor(() => expect(input.value).toBe(""));
});

it("tests the ElevenLabs key and says a scoped key still works", async () => {
  const probe = vi.fn().mockResolvedValue({ status: "ready", model: "scribe_v2", detail: "KEY_SCOPED", checked_at: "x" });
  const props = setup({ probe });
  fireEvent.click(await screen.findByRole("button", { name: "測試 ElevenLabs 連線" }));
  await screen.findByText(/已連線：金鑰可用，scribe_v2.*限定權限/);
  expect(props.probe).toHaveBeenCalledWith("api-elevenlabs");
});

// 2026-09-21 使用者：「OpenRouter 我要再新增轉錄可以用的 microsoft/mai-transcribe-2」→ 一個來源可以登記多個轉錄模型
it("adds and removes extra OpenRouter transcription models", async () => {
  const probe = vi.fn().mockResolvedValue({ status: "ready", model: "deepseek/deepseek-v4.1-flash", checked_at: "x" });
  const withKey = items.map((p) => (p.id === "api-openrouter" ? { ...p, secret_configured: true } : p));
  const list = vi.fn().mockResolvedValue({ items: withKey });
  const props = setup({ probe, list });
  const extra = (await screen.findByLabelText("要新增的 OpenRouter 轉錄模型名稱")) as HTMLInputElement;
  expect(extra.disabled).toBe(true); // 測試連線通過前不能新增
  fireEvent.click(screen.getByRole("button", { name: "測試 OpenRouter 連線" }));
  await waitFor(() => expect(extra.disabled).toBe(false));
  fireEvent.change(extra, { target: { value: "microsoft/mai-transcribe-2" } });
  // 存好後清單重新讀回：新模型出現、可以移除
  list.mockResolvedValue({ items: withKey.map((p) => (p.id === "api-openrouter"
    ? { ...p, transcription_models: ["meta/muse-voice-transcribe-1.0", "microsoft/mai-transcribe-2"] } : p)) });
  fireEvent.click(screen.getByRole("button", { name: "新增 OpenRouter 轉錄模型" }));
  await waitFor(() => expect(props.save).toHaveBeenCalled());
  const [saved] = (props.save as ReturnType<typeof vi.fn>).mock.calls[0];
  expect(saved).toMatchObject({ id: "api-openrouter", transcription_model: "meta/muse-voice-transcribe-1.0",
                                transcription_models: ["meta/muse-voice-transcribe-1.0", "microsoft/mai-transcribe-2"] });
  fireEvent.click(await screen.findByRole("button", { name: "移除 microsoft/mai-transcribe-2" }));
  await waitFor(() => expect(props.save).toHaveBeenCalledTimes(2));
  expect((props.save as ReturnType<typeof vi.fn>).mock.calls[1][0]).toMatchObject({ transcription_models: ["meta/muse-voice-transcribe-1.0"] });
});

// 2026-09-21 真跑：ElevenLabs 金鑰沒開 Speech to Text 權限 → 測試連線要直接說缺哪個權限、去哪裡開（不是「重填金鑰」）
it("explains a key that is missing the Speech to Text permission", async () => {
  setup({ probe: vi.fn().mockResolvedValue({ status: "auth_failed", detail: "PERMISSION_MISSING:speech_to_text", checked_at: "x" }) });
  fireEvent.click(await screen.findByRole("button", { name: "測試 ElevenLabs 連線" }));
  const text = await screen.findByText(/Speech to Text/);
  expect(text.textContent).toMatch(/沒有開.*Speech to Text.*權限/);
  expect(text.textContent).toContain("API Keys");
  expect(text.textContent).not.toContain("PERMISSION_MISSING");
});
