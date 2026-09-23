import { describe, it, expect } from "vitest";
import {
  createSegment,
  combineOverlappingSegments,
  splitSegment,
  updateBoundary,
  moveSegment,
} from "../src/domain/segments";
import { historyReducer, initialHistory } from "../src/hooks/useSegments";
import { parseTimecode, formatTimecode } from "../src/domain/time";
describe("來源時間與非破壞剪輯", () => {
  it("解析使用者十分鐘範圍及毫秒往返", () => {
    expect(parseTimecode("1:50:00")).toBe(6600000000);
    expect(parseTimecode("2:00:00")).toBe(7200000000);
    expect(parseTimecode(formatTimecode(6600123000))).toBe(6600123000);
    expect(() => parseTimecode("1:70:00")).toThrow();
  });
  it("marker 不自動延伸到結尾", () => {
    const m = createSegment({ start_us: 7 });
    expect(m.kind).toBe("marker");
    expect(m.end_us).toBeUndefined();
  });
  it("相鄰範圍不合併，重疊合併不修改輸入", () => {
    const a = [
      createSegment({ start_us: 0, end_us: 10 }),
      createSegment({ start_us: 10, end_us: 20 }),
    ];
    expect(combineOverlappingSegments(a)).toHaveLength(2);
    const b = [a[0], createSegment({ start_us: 5, end_us: 20 })];
    expect(combineOverlappingSegments(b)[0].end_us).toBe(20);
    expect(b[0].end_us).toBe(10);
  });
  it("邊界不可反轉；分割保留名稱與字串tags", () => {
    const a = createSegment({
      start_us: 10,
      end_us: 30,
      name: "詞",
      tags: { x: "a" },
    });
    expect(updateBoundary(a, "start_us", 40, 100).start_us).toBe(29);
    expect(splitSegment(a, 20).map((x) => [x.start_us, x.end_us])).toEqual([
      [10, 20],
      [20, 30],
    ]);
    expect(splitSegment(a, 10)).toEqual([a]);
  });
  it("輸出順序可重排及重複", () => {
    const a = createSegment({ start_us: 10, end_us: 20 }),
      b = createSegment({ start_us: 30, end_us: 40 });
    expect(moveSegment([a, b], 1, 0)).toEqual([b, a]);
  });
  it("100步復原與新分支清除redo，不含逐字稿", () => {
    let h = initialHistory;
    for (let i = 0; i < 105; i++)
      h = historyReducer(h, {
        type: "set",
        items: [createSegment({ start_us: i })],
      });
    expect(h.past.length).toBe(100);
    h = historyReducer(h, { type: "undo" });
    expect(h.present[0].start_us).toBe(103);
    h = historyReducer(h, { type: "redo" });
    expect(h.present[0].start_us).toBe(104);
    h = historyReducer(historyReducer(h, { type: "undo" }), {
      type: "set",
      items: [],
    });
    expect(h.future).toEqual([]);
  });
});
