// @vitest-environment jsdom
// 版面整理：各大區塊可摺疊，狀態記在 localStorage，鍵盤與讀屏可用（aria-expanded／aria-controls）。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";
import CollapsibleSection from "../src/components/CollapsibleSection";

beforeEach(() => localStorage.clear());
afterEach(cleanup);

it("is open by default, toggles closed and exposes aria state", () => {
  render(
    <CollapsibleSection id="export" title="輸出設定" hint="依清單順序輸出">
      <p>內容</p>
    </CollapsibleSection>,
  );
  const toggle = screen.getByRole("button", { name: /輸出設定/ });
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  const body = document.getElementById(toggle.getAttribute("aria-controls")!)!;
  expect(body.hidden).toBe(false);
  expect(screen.getByText("依清單順序輸出")).toBeTruthy();
  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(body.hidden).toBe(true);
  expect(localStorage.getItem("dongbi.section.export")).toBe("0");
});

it("restores the saved state and keeps children mounted so form state survives collapsing", () => {
  localStorage.setItem("dongbi.section.acquire", "0");
  const view = render(
    <CollapsibleSection id="acquire" title="取得素材">
      <input aria-label="連結" defaultValue="" />
    </CollapsibleSection>,
  );
  const toggle = screen.getByRole("button", { name: /取得素材/ });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  const input = view.container.querySelector("input")!;
  fireEvent.change(input, { target: { value: "https://example.test/v" } });
  fireEvent.click(toggle);
  fireEvent.click(toggle);
  expect((view.container.querySelector("input") as HTMLInputElement).value).toBe("https://example.test/v");
});

it("lazy sections mount their children only after the first open, and show a badge and actions", () => {
  render(
    <CollapsibleSection id="env" title="環境與模型" defaultOpen={false} lazy badge={3} actions={<button>重新檢查</button>}>
      <p>很重的內容</p>
    </CollapsibleSection>,
  );
  expect(screen.queryByText("很重的內容")).toBeNull();
  expect(screen.getByText("3")).toBeTruthy();
  expect(screen.getByRole("button", { name: "重新檢查" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /環境與模型/ }));
  expect(screen.getByText("很重的內容")).toBeTruthy();
});
