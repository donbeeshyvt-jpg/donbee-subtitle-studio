// 頁面流暢：輪詢回來內容相同時保留原本的 state 參考，避免每 2 秒重繪整個畫面。
import { describe, expect, it } from "vitest";
import { keep, sameData } from "../src/domain/same";

describe("sameData / keep", () => {
  it("treats structurally equal data as the same and keeps the previous reference", () => {
    const prev = [{ id: "j1", status: "running", result: { asset_ids: ["a"] } }];
    const next = [{ id: "j1", status: "running", result: { asset_ids: ["a"] } }];
    expect(sameData(prev, next)).toBe(true);
    expect(keep(next)(prev)).toBe(prev);
  });
  it("returns the new value when anything differs", () => {
    const prev = [{ id: "j1", status: "running" }];
    const next = [{ id: "j1", status: "succeeded" }];
    expect(sameData(prev, next)).toBe(false);
    expect(keep(next)(prev)).toBe(next);
    expect(keep<{ id: string }[]>([])([{ id: "x" }])).toEqual([]);
  });
  it("handles undefined and identical references without throwing", () => {
    expect(sameData(undefined, undefined)).toBe(true);
    const same = { a: 1 };
    expect(keep(same)(same)).toBe(same);
  });
});
