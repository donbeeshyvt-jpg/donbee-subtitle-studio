// @vitest-environment jsdom
// 使用者 2026-09-19：按「選擇資料夾…」跳出「尚未建立本機工作階段」，而且多出一個要打字的路徑欄。
// 手動輸入只給「這台電腦開不了視窗」（DIALOG_UNAVAILABLE）用；其他錯誤（連線失效等）只顯示看得懂的訊息。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import OutputRootPicker from "../src/components/OutputRootPicker";
afterEach(cleanup);

const fail = (status: number, code: string, message: string) => Object.assign(new Error(message), { status, code });
const renderPicker = (pickFolder: () => Promise<{ path: string | null; cancelled: boolean }>) =>
  render(<OutputRootPicker label="字幕輸出" roots={[]} value="" onChange={vi.fn()} addRoot={vi.fn()} pickFolder={pickFolder} />);

it("does not switch to typing a path when the connection to the local service failed", async () => {
  renderPicker(() => Promise.reject(fail(401, "SESSION_EXPIRED", "與本機服務的連線已失效，請重新整理頁面（F5）")));
  fireEvent.click(screen.getByRole("button", { name: "選擇字幕輸出資料夾" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("重新整理"));
  expect(screen.queryByLabelText("字幕輸出資料夾路徑")).toBeNull();
});

it("offers typing a path only when this computer cannot open the folder window", async () => {
  renderPicker(() => Promise.reject(fail(501, "DIALOG_UNAVAILABLE", "這台電腦無法開啟選擇視窗，請改用手動輸入路徑")));
  fireEvent.click(screen.getByRole("button", { name: "選擇字幕輸出資料夾" }));
  await waitFor(() => expect(screen.getByLabelText("字幕輸出資料夾路徑")).toBeTruthy());
});
