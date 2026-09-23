// 詞彙切分（2026-09-20 使用者第 4、5 點）：與後端 app.studio.terms.split_terms 相同規則。
// 有逗號、頓號、分號或換行就照它們切（保留「PICO PARK」這種含空格的名稱）；只有空格時照空格切。去重、保留順序。
import type { Job } from "../api/types";

const SEPARATORS = /[,，、;；\n\r]+/;

export function splitTerms(text: string, limit = 100, maxChars = 80): string[] {
  if (!text || !text.trim()) return [];
  const parts = SEPARATORS.test(text) ? text.split(SEPARATORS) : text.split(/\s+/);
  const seen: string[] = [];
  for (const part of parts) {
    const term = part.split(/\s+/).filter(Boolean).join(" ").slice(0, maxChars).trim();
    if (term && !seen.includes(term)) seen.push(term);
    if (seen.length >= limit) break;
  }
  return seen;
}

export const joinTerms = (terms: string[]) => terms.join(", ");

// 兩段詞彙合併（匯入文字檔時把新詞接在後面，重複的不再加）
export const mergeTerms = (current: string, added: string, limit = 100) =>
  joinTerms(splitTerms([...splitTerms(current, limit), ...splitTerms(added, limit)].join("\n"), limit));

// 讀使用者選的文字檔（UTF-8）；舊瀏覽器沒有 File.text() 時用 FileReader
export function readTextFile(file: File): Promise<string> {
  if (typeof file.text === "function") return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("讀取檔案失敗"));
    reader.readAsText(file, "utf-8");
  });
}

// 字幕品質檢查（M4-C2）：匯出清單與預覽回傳的計數 → 一句話
export interface SubtitleQuality {
  entries: number;
  overlaps: number;
  short_display: number;
  long_display: number;
  fast_entries: number;
  max_chars_per_sec: number;
  adjacent_repeats: number;
  missing_cues: number;
  missing_cue_ids: string[];
  warnings: Record<string, number>;
}

export function qualitySummary(quality: SubtitleQuality | null | undefined): string {
  if (!quality) return "";
  const parts: string[] = [];
  if (quality.overlaps) parts.push(`重疊 ${quality.overlaps}`);
  if (quality.fast_entries) parts.push(`太快 ${quality.fast_entries}`);
  if (quality.long_display) parts.push(`過長 ${quality.long_display}`);
  if (quality.short_display) parts.push(`過短 ${quality.short_display}`);
  if (quality.adjacent_repeats) parts.push(`相鄰重複 ${quality.adjacent_repeats}`);
  if (quality.missing_cues) parts.push(`疑似漏字 ${quality.missing_cues} 句`);
  return parts.length ? `字幕檢查：${parts.join("、")}` : `字幕檢查：${quality.entries} 則，沒有發現問題`;
}

// 校字工作的結果摘要：按了「依上下文校字」之後，就算一個字都沒改也要說清楚檢查了什麼、為什麼沒改
export function correctionSummary(job: Job | undefined, appliedCount = 0): string {
  if (!job) return "";
  if (["queued", "running", "waiting"].includes(job.status)) return "校字中…";
  if (job.status === "failed") return `校字失敗：${job.error?.message || job.error?.code || "未知原因"}`;
  if (job.status !== "succeeded") return job.status === "cancelled" ? "校字已取消" : "";
  const r = (job.result || {}) as Record<string, unknown>;
  const chunks = Array.isArray(r.sent_cue_ids) ? (r.sent_cue_ids as unknown[]) : [];
  const checked = chunks.reduce<number>((n, c) => n + (Array.isArray(c) ? c.length : 0), 0);
  const patches = Array.isArray(r.patches) ? (r.patches as { confidence?: string }[]) : [];
  const high = patches.filter((p) => p.confidence !== "low").length;
  const low = patches.length - high;
  const blocked = Array.isArray(r.rejected_patches) ? r.rejected_patches.length : 0;
  const ignored = typeof r.ignored_no_change === "number" ? r.ignored_no_change : 0;
  const skipped = Array.isArray(r.failed_chunks) ? r.failed_chunks.length : 0;
  // 送法（2026-09-20 使用者）：走 API 要看得出是整份送一次，還是放不下而分段（分段會附鄰句）
  const requests = typeof r.requests === "number" ? r.requests : 0;
  const window = typeof r.context_window_cues === "number" ? r.context_window_cues : 0;
  const how = !requests ? "" : r.segmented ? `（分 ${requests} 段送出${window ? `，每段附前後 ${window} 句作參考` : ""}）` : "（整份一次送出）";
  const head = checked ? `校字完成：檢查 ${checked} 句${how}` : `校字完成${how}`;
  const reference = r.reference as { chars_total?: number; chars_sent?: number; truncated?: boolean } | undefined;
  const cut = reference?.truncated ? `；參考資料太長，只送了前 ${reference.chars_sent} 字（共 ${reference.chars_total} 字）` : "";
  const tail = (skipped ? `；${skipped} 段模型輸出無效、已略過` : "") + cut;
  if (!patches.length && !blocked) {
    return `${head}，沒有找到需要修正的地方${ignored ? `（${ignored} 處只差標點，已忽略）` : ""}${tail}`;
  }
  const parts: string[] = [];
  if (high) parts.push(appliedCount ? `已套用 ${appliedCount} 處` : `高信心 ${high} 處`);
  if (low) parts.push(`低信心 ${low} 處待確認`);
  if (blocked) parts.push(`被擋 ${blocked} 處`);
  return `${head}；${parts.join("、")}${ignored ? `；${ignored} 處只差標點，已忽略` : ""}${tail}`;
}
