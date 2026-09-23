// SPDX-License-Identifier: GPL-2.0-only
// 2026-09-16 由 LosslessCut util/duration.ts 的格式化與解析流程改寫為整數微秒。
// base：素材在來源中的起點（微秒）。介面一律顯示「素材相對時間」，資料仍以來源時間儲存。
export function formatTimecode(us: number, base = 0) {
  us = us - base;
  const ms = Math.round(Math.abs(us) / 1000);
  return `${us < 0 ? "-" : ""}${String(Math.floor(ms / 3600000)).padStart(2, "0")}:${String(Math.floor(ms / 60000) % 60).padStart(2, "0")}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}.${String(ms % 1000).padStart(3, "0")}`;
}
export function parseTimecode(value: string, base = 0): number {
  const match = value
    .trim()
    .match(/^(?:(?:(\d+):)?(\d{1,2}):)?(\d+(?:[.,]\d+)?)$/);
  if (!match) throw Error("時間格式請用 時:分:秒");
  const h = Number(match[1] || 0),
    m = Number(match[2] || 0),
    s = Number(match[3].replace(",", "."));
  const us = Math.round((h * 3600 + m * 60 + s) * 1e6);
  if (m > 59 || (match[2] != null && s >= 60) || !Number.isSafeInteger(us))
    throw Error("時間超出有效範圍");
  return base + us;
}
export function parseRanges(text: string) {
  return text.trim()
    ? text
        .split(/[\n;；]+/)
        .filter((x) => x.trim())
        .map((line) => {
          const [a, b, ...rest] = line.trim().split(/\s*[-–~]\s*/);
          if (!b || rest.length) throw Error("每行請填起點-終點");
          const start_us = parseTimecode(a),
            end_us = parseTimecode(b);
          if (end_us <= start_us) throw Error("終點必須晚於起點");
          return { start_us, end_us };
        })
    : [];
}
