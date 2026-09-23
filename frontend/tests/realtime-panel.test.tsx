// @vitest-environment jsdom
// M7 即時字幕與翻譯（2026-09-20 使用者：「M7 也可以納入」）：麥克風或播放中的素材 → 每 0.5 秒送一段 16 kHz PCM →
// 顯示暫定文字與定稿字幕，譯文跟在每一行下面；停止後可下載 SRT。音訊來源在測試裡用替身。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { installAppMock } from "./helpers/appMock";
import { downsampleToPcm16 } from "../src/domain/pcm";

const calls: { create: unknown[]; audio: number[]; finish: number } = { create: [], audio: [], finish: 0 };
beforeEach(() => {
  calls.create = [];
  calls.audio = [];
  calls.finish = 0;
  installAppMock({
    realtimeCreate: async (body: unknown) => {
      calls.create.push(body);
      return { session_id: "rt_1", sample_rate: 16000, model: "turbo" };
    },
    realtimeAudio: async (_sid: unknown, data: unknown) => {
      calls.audio.push((data as ArrayBuffer).byteLength);
      return calls.audio.length === 1
        ? { events: [{ type: "tentative", text: "大家" }], received_sec: 0.5 }
        : {
            events: [
              { type: "line", id: "L1", start: 0.2, end: 0.9, text: "大家好。" },
              { type: "translation", line_id: "L1", text: "Hi everyone.", latency_sec: 0.8 },
            ],
            received_sec: 1.0,
          };
    },
    realtimeFinish: async () => {
      calls.finish += 1;
      return {
        events: [],
        lines: [{ id: "L1", start: 0.2, end: 0.9, text: "大家好。", translation: "Hi everyone." }],
        srt: "1\n00:00:00,200 --> 00:00:00,900\n大家好。\n",
        stats: { steps: 2, real_time_factor: 0.2, commit_lag_sec: { median: 0.9 }, translation_sec: { median: 0.8 } },
      };
    },
  });
});
afterEach(() => {
  cleanup();
  vi.resetModules();
});

it("downsamples browser audio to 16 kHz 16-bit PCM", () => {
  const input = new Float32Array(48).fill(0.5);
  const output = downsampleToPcm16(input, 48000);
  expect(output.length).toBe(16);
  expect(output[0]).toBe(16384);
});

it("streams audio chunks, shows committed lines with translations and offers the SRT", async () => {
  const { default: RealtimePanel } = await import("../src/components/RealtimePanel");
  let push: (samples: Int16Array) => void = () => {};
  const stop = vi.fn();
  const openSource = vi.fn(async (_kind: string, onChunk: (samples: Int16Array) => void) => {
    push = onChunk;
    return stop;
  });
  render(<RealtimePanel providers={[{ id: "local-lmstudio", model: "google/gemma-4-e4b", local: true }]} provider="local-lmstudio" remoteConsent={false} openSource={openSource} />);
  fireEvent.change(screen.getByLabelText("翻譯成"), { target: { value: "en" } });
  fireEvent.click(screen.getByRole("button", { name: "開始即時字幕" }));
  await waitFor(() => expect(openSource).toHaveBeenCalled());
  expect(calls.create[0]).toEqual({ model: "turbo", language: "zh", translate_to: "en", provider_id: "local-lmstudio", remote_consent: false });
  push(new Int16Array(8000));
  await screen.findByText("大家");
  push(new Int16Array(8000));
  await screen.findByText("大家好。");
  expect(screen.getByText("Hi everyone.")).toBeTruthy();
  expect(calls.audio).toEqual([16000, 16000]);
  fireEvent.click(screen.getByRole("button", { name: "停止" }));
  await waitFor(() => expect(calls.finish).toBe(1));
  expect(stop).toHaveBeenCalled();
  expect(await screen.findByRole("button", { name: "下載 SRT" })).toBeTruthy();
  expect(screen.getByText(/定稿延遲中位 0.9 秒/)).toBeTruthy();
}, 15000);

it("asks for a text model before translating", async () => {
  const { default: RealtimePanel } = await import("../src/components/RealtimePanel");
  render(<RealtimePanel providers={[]} provider="" remoteConsent={false} openSource={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("翻譯成"), { target: { value: "en" } });
  fireEvent.click(screen.getByRole("button", { name: "開始即時字幕" }));
  await screen.findByText(/先在「AI 分析、摘要與校字」選文字模型/);
  expect(calls.create).toEqual([]);
});
