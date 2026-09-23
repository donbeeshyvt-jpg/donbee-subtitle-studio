// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import EnvironmentPanel from "../src/components/EnvironmentPanel";
import type { EnvironmentReport } from "../src/api/types";
afterEach(cleanup);

const report = (checked: string): EnvironmentReport => ({
  checked_at: checked,
  items: {
    python: { label: "Python", status: "ok", version: "3.12.10" },
    ffmpeg: { label: "FFmpeg", status: "missing", guidance: "winget install Gyan.FFmpeg" },
    node: { label: "Node.js", status: "outdated", version: "16.0.0", guidance: "winget install OpenJS.NodeJS.LTS" },
    lmstudio: { label: "LM Studio 本機伺服器", status: "unreachable", guidance: "請開啟 LM Studio" },
  },
  packages: { lock: "requirements.lock", items: [
    { name: "fastapi", group: "core", required: "0.138.1", installed: "0.138.1", status: "ok" },
    { name: "httpx", group: "core", required: "0.28.1", installed: "0.27.0", status: "version_mismatch" },
    { name: "pywebview", group: "v1", required: "6.2.1", installed: null, status: "optional_missing" },
  ] },
  models: { manifest: "models.manifest.json", missing: ["hf:MIT/ast"], optional_missing: ["hf:microsoft/VibeVoice-ASR"] },
  paths: { project_root: "root", data_dir: "root/data", models_dir: "root/models", hf_cache: "root/models/hf", torch_home: "root/models/torch", gguf_dir: "root/models/gguf" },
});

it("shows Chinese status badges, guidance for problems, package and model gaps, and rechecks on demand", async () => {
  const load = vi.fn().mockResolvedValue(report("2026-09-16T08:00:00+00:00"));
  const recheck = vi.fn().mockResolvedValue(report("2026-09-16T09:00:00+00:00"));
  render(<EnvironmentPanel load={load} recheck={recheck} />);
  await waitFor(() => expect(screen.getByText("Python")).toBeTruthy());
  expect(screen.getByText("正常")).toBeTruthy();
  expect(screen.getByText("缺少")).toBeTruthy();
  expect(screen.getByText("版本過舊")).toBeTruthy();
  expect(screen.getByText("無法連線")).toBeTruthy();
  expect(screen.getByText("winget install Gyan.FFmpeg")).toBeTruthy();
  expect(screen.getByText(/httpx/)).toBeTruthy();
  expect(screen.getByText(/選用群組未安裝：pywebview（v1）/)).toBeTruthy();
  expect(screen.queryByText(/pywebview：未安裝/)).toBeNull();
  expect(screen.getByText(/hf:MIT\/ast/)).toBeTruthy();
  expect(screen.getByText(/2026-09-16T08:00:00/)).toBeTruthy();
  const button = screen.getByRole("button", { name: "重新檢查" });
  fireEvent.click(button);
  expect((button as HTMLButtonElement).disabled).toBe(true);
  await waitFor(() => expect(screen.getByText(/2026-09-16T09:00:00/)).toBeTruthy());
  expect(recheck).toHaveBeenCalledTimes(1);
  expect((button as HTMLButtonElement).disabled).toBe(false);
});

it("lists optional models, requires confirmation before download, and polls progress until done", async () => {
  const load = vi.fn().mockResolvedValue(report("2026-09-16T08:00:00+00:00"));
  const missing = { id: "hf:microsoft/VibeVoice-ASR", kind: "hf", required: false, status: "missing" as const };
  const statuses = [
    { items: [missing], required_missing: [], offline_ready: true, download: null },
    { items: [missing], required_missing: [], offline_ready: true, download: { status: "running" as const, ids: [missing.id], events: [{ id: missing.id, stage: "start" }] } },
    { items: [{ ...missing, status: "present" as const }], required_missing: [], offline_ready: true, download: { status: "done" as const, ids: [missing.id], events: [] } },
  ];
  let call = 0;
  const models = vi.fn().mockImplementation(() => Promise.resolve(statuses[Math.min(call++, 2)]));
  const download = vi.fn().mockResolvedValue({ status: "running" });
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  render(<EnvironmentPanel load={load} recheck={load} models={models} download={download} pollMs={10} />);
  const button = await waitFor(() => screen.getByRole("button", { name: /複製或下載/ }));
  expect(screen.getByText("選用")).toBeTruthy();
  fireEvent.click(button);
  expect(download).not.toHaveBeenCalled();
  fireEvent.click(button);
  expect(confirm).toHaveBeenCalledTimes(2);
  await waitFor(() => expect(download).toHaveBeenCalledWith([missing.id]));
  await waitFor(() => expect(screen.getByText(/已就緒/)).toBeTruthy(), { timeout: 3000 });
  expect(models.mock.calls.length).toBeGreaterThanOrEqual(3);
  confirm.mockRestore();
});

it("reports a load failure honestly instead of showing a fake healthy state", async () => {
  const load = vi.fn().mockRejectedValue(new Error("服務未啟動"));
  render(<EnvironmentPanel load={load} recheck={load} />);
  await waitFor(() => expect(screen.getByText(/無法取得環境檢查/)).toBeTruthy());
  expect(screen.queryByText("正常")).toBeNull();
});

// 2026-09-19：Breeze-ASR（ct2 類）用名稱與大小顯示；按「下載並轉換」前的確認要寫清楚來源、大小與授權
it("shows Breeze models by name and size and confirms source, size and license before converting", async () => {
  const load = vi.fn().mockResolvedValue(report("2026-09-19T08:00:00+00:00"));
  const breeze = {
    id: "ct2:breeze-asr-26",
    kind: "ct2" as const,
    required: false,
    status: "missing" as const,
    label: "Breeze-ASR-26（台語語音→中文字）",
    repo: "MediaTek-Research/Breeze-ASR-26",
    download_bytes: 6_175_389_907,
    license: "Apache-2.0",
  };
  const models = vi.fn().mockResolvedValue({ items: [breeze], required_missing: [], offline_ready: true, download: null });
  const download = vi.fn().mockResolvedValue({ status: "running" });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<EnvironmentPanel load={load} recheck={load} models={models} download={download} pollMs={10} />);
  await waitFor(() => expect(screen.getByText("Breeze-ASR-26（台語語音→中文字）")).toBeTruthy());
  expect(document.querySelector('li[data-status="missing"]')!.textContent).toContain("6.2 GB");
  fireEvent.click(screen.getByRole("button", { name: "下載並轉換" }));
  const message = confirm.mock.calls[0][0] as string;
  expect(message).toContain("MediaTek-Research/Breeze-ASR-26");
  expect(message).toContain("6.2 GB");
  expect(message).toContain("Apache-2.0");
  expect(download).not.toHaveBeenCalled();
  confirm.mockRestore();
});
