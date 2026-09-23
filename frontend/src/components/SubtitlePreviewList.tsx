// SRT 字幕預覽（2026-09-20 使用者第 6 點：「SRT 檔案多少就是多少，不要預覽不同步」）：
// 內容來自 POST /subtitles/preview，與匯出 SRT 同一段計算；時間字串就是檔案裡的字串。點一則跳到該處，播放中標示目前這一則。
import { memo, useEffect, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import type { SubtitlePreview } from "../api/types";
import { qualitySummary } from "../domain/terms";

const WARNINGS: Record<string, string> = {
  merged_short_cue: "已合併過短字幕",
  short_display: "顯示時間過短",
  coarse_alignment: "句子時間",
  coarse_sentence_boundary: "句界為估計",
  unknown_word_time: "部分字沒有時間",
  truncated_word: "字被範圍切到",
  partial_cue: "句子被範圍切到",
};

export function previewStatus(preview: SubtitlePreview | null, error: string): string {
  if (error) return `字幕預覽失敗：${error}`;
  if (!preview) return "完成轉錄後，這裡列出匯出 SRT 的每一則字幕";
  const count = `共 ${preview.entries.length} 則`;
  const checked = qualitySummary(preview.quality);
  const issues = checked && !checked.includes("沒有發現問題") ? ` · ${checked}` : "";
  if (preview.alignment === "word") return `與匯出的 SRT 檔逐字相同（已逐詞對齊）· ${count}${issues}`;
  if (preview.alignment === "pending") return `逐詞對齊還沒完成：目前是句子時間，對齊完成後自動更新；匯出會等對齊完成 · ${count}${issues}`;
  if (preview.alignment === "failed") return `逐詞對齊失敗：預覽與匯出都用句子時間，與匯出的 SRT 檔逐字相同 · ${count}${issues}`;
  return `與匯出的 SRT 檔逐字相同 · ${count}${issues}`;
}

function SubtitlePreviewList({
  preview,
  error,
  currentUs,
  seek,
  audit,
}: {
  preview: SubtitlePreview | null;
  error: string;
  currentUs: number | null;
  seek: (us: number) => void;
  // 只播這個時間點前後各約 0.5 秒（M4-C5 入出點抽查）
  audit?: (us: number) => void;
}) {
  const entries = preview?.entries || [];
  const ref = useRef<HTMLDivElement>(null);
  const virtual = useVirtualizer({
    count: entries.length,
    getScrollElement: () => ref.current,
    estimateSize: () => 64,
    overscan: 8,
    initialRect: { width: 360, height: 640 },
  });
  const currentIndex =
    currentUs == null ? -1 : entries.findIndex((e) => e.source_start_us <= currentUs && currentUs < e.source_end_us);
  useEffect(() => {
    if (currentIndex < 0) return;
    try {
      virtual.scrollToIndex(currentIndex, { align: "auto" });
    } catch {
      /* 測試環境沒有版面 */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentIndex]);
  return (
    <div className="subtitle-preview">
      <p className="muted subtitle-preview-status" role="status">
        {previewStatus(preview, error)}
      </p>
      <div className="transcript-scroll" ref={ref}>
        {entries.length ? (
          <div style={{ height: virtual.getTotalSize(), position: "relative" }}>
            {virtual.getVirtualItems().map((row) => {
              const entry = entries[row.index];
              return (
                <article
                  key={entry.index}
                  ref={virtual.measureElement}
                  data-index={row.index}
                  className={row.index === currentIndex ? "cue srt-entry is-current" : "cue srt-entry"}
                  onClick={(e) => {
                    if ((e.target as HTMLElement).closest("button")) return;
                    seek(entry.source_start_us);
                  }}
                  style={{ position: "absolute", width: "100%", top: 0, transform: `translateY(${row.start}px)` }}
                >
                  <div className="cue-heading">
                    <button className="text-button timecode" onClick={() => seek(entry.source_start_us)}>
                      {entry.start} → {entry.end}
                    </button>
                    <span className="muted">#{entry.index}</span>
                    {audit ? (
                      <span className="srt-audit">
                        <button className="text-button" onClick={() => audit(entry.source_start_us)}>
                          聽入點
                        </button>
                        <button className="text-button" onClick={() => audit(entry.source_end_us)}>
                          聽出點
                        </button>
                      </span>
                    ) : null}
                  </div>
                  <p>{entry.text}</p>
                  {entry.warnings.length ? <small>{entry.warnings.map((w) => WARNINGS[w] || w).join("、")}</small> : null}
                </article>
              );
            })}
          </div>
        ) : (
          <div className="empty">
            <h3>{preview ? "這個範圍沒有字幕" : "還沒有字幕"}</h3>
            <p>預覽用與「匯出 SRT」相同的設定計算（每則句數、保留標點、逐詞對齊）。</p>
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(SubtitlePreviewList);
