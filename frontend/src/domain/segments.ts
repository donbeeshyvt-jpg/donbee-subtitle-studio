// SPDX-License-Identifier: GPL-2.0-only
// 2026-09-16 修改自 LosslessCut segments.ts，改為微秒、瀏覽器 UUID 與明確 marker。
import type { Segment } from "../api/types";
// 匯出時自動補的「整段」片段（tags.auto === "full"）：使用者加了自己的片段就移除
export function withoutAutoFull(items: Segment[]): Segment[] {
  return items.filter((s) => s.tags?.auto !== "full");
}
export function createSegment(props: Partial<Segment> = {}): Segment {
  const end = props.end_us;
  return {
    id: crypto.randomUUID(),
    start_us: props.start_us ?? 0,
    end_us: end,
    name: props.name || "",
    selected: props.selected ?? true,
    tags: Object.fromEntries(
      Object.entries(props.tags || {}).map(([k, v]) => [k, String(v)]),
    ),
    kind: end == null ? "marker" : "clip",
  };
}
// 保留上游排序掃描與相交判定；相鄰選段保持獨立。
export function combineOverlappingSegments(existing: Segment[]): Segment[] {
  if (!existing.length) return [];
  const sorted = [...existing].sort((a, b) => a.start_us - b.start_us);
  let current = sorted[0];
  const combined: Segment[] = [];
  for (let i = 1; i < sorted.length; i++) {
    const next = sorted[i];
    const end = current.end_us ?? current.start_us;
    if (end > next.start_us) {
      current = {
        ...current,
        end_us: Math.max(end, next.end_us ?? next.start_us),
        kind: "clip",
      };
    } else {
      combined.push(current);
      current = next;
    }
  }
  combined.push(current);
  return combined;
}
export function updateBoundary(
  s: Segment,
  key: "start_us" | "end_us",
  value: number,
  duration: number,
): Segment {
  const t = Math.round(Math.max(0, Math.min(value, duration)));
  if (key === "start_us")
    return {
      ...s,
      start_us: Math.min(t, s.end_us == null ? duration : s.end_us - 1),
    };
  return { ...s, kind: "clip", end_us: Math.max(s.start_us + 1, t) };
}
export function splitSegment(s: Segment, time: number): Segment[] {
  if (s.end_us == null || time <= s.start_us || time >= s.end_us) return [s];
  return [{ ...s, end_us: time }, createSegment({ ...s, start_us: time })];
}
export function moveSegment(items: Segment[], from: number, to: number) {
  if (from < 0 || to < 0 || from >= items.length || to >= items.length)
    return items;
  const copy = [...items];
  const [v] = copy.splice(from, 1);
  copy.splice(to, 0, v);
  return copy;
}
