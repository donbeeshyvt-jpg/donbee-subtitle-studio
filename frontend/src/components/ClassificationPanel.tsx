import { useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import type {
  Classification,
  ClassificationSpan,
  AudioLabel,
} from "../api/types";
import { formatTimecode } from "../domain/time";
const labels: Record<AudioLabel, string> = {
  speech: "語音",
  music: "音樂",
  mixed: "混合",
  uncertain: "不確定",
};
export default function ClassificationPanel({
  classifications,
  seek,
  override,
  timeBase = 0,
}: {
  classifications: Classification[];
  seek: (t: number) => void;
  override: (
    classification: Classification,
    span: ClassificationSpan,
    label: AudioLabel,
  ) => Promise<void>;
  timeBase?: number;
}) {
  const [busy, setBusy] = useState("");
  const parent = useRef<HTMLDivElement>(null);
  const latest = new Map<string, Classification>();
  for (const c of classifications) latest.set(c.asset_id, c);
  const entries = [...latest.values()]
    .flatMap((c) =>
      (c.spans || c.raw_spans || []).map((span) => ({
        classification: c,
        span,
      })),
    )
    .sort((a, b) => a.span.start_us - b.span.start_us);
  const virtual = useVirtualizer({
    count: entries.length,
    getScrollElement: () => parent.current,
    estimateSize: () => 106,
    overscan: 4,
  });
  return (
    <>
      <div className="classification-note">
        <strong>音訊候選分類</strong>
        <p>
          分類閾值尚未校準，請回播複核。人工調整會建立新版本，原始分類與字幕保留。
        </p>
      </div>
      <div className="transcript-scroll" ref={parent}>
        {!entries.length ? (
          <div className="empty">
            <h3>等待音訊分類</h3>
            <p>音訊分析後，這裡會顯示語音、音樂與混合區段。</p>
          </div>
        ) : (
          <div style={{ height: virtual.getTotalSize(), position: "relative" }}>
            {virtual.getVirtualItems().map((row) => {
              const { classification: c, span } = entries[row.index];
              const identity = `${c.id}:${span.id}`;
              return (
                <article
                  key={identity}
                  ref={virtual.measureElement}
                  data-index={row.index}
                  className="cue classification-cue"
                  style={{
                    position: "absolute",
                    width: "100%",
                    top: 0,
                    transform: `translateY(${row.start}px)`,
                  }}
                >
                  <button
                    className="text-button timecode"
                    onClick={() => seek(span.start_us)}
                  >
                    {formatTimecode(span.start_us, timeBase)} →{" "}
                    {formatTimecode(span.end_us, timeBase)}
                  </button>
                  <div className="classification-controls">
                    <label>
                      區段標籤
                      <select
                        aria-label={`音訊區段 ${row.index + 1} 分類`}
                        value={span.label}
                        disabled={busy === identity}
                        onChange={async (e) => {
                          const label = e.target.value as AudioLabel;
                          setBusy(identity);
                          try {
                            await override(c, span, label);
                          } catch {
                            /* 呼叫端顯示錯誤，原本分類不變。 */
                          } finally {
                            setBusy("");
                          }
                        }}
                      >
                        {Object.entries(labels).map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <span className="muted">
                      {span.override_id ? "人工調整" : "模型候選"}
                      {span.requires_review ? " · 待複核" : ""}
                      <br />
                      原始：{labels[span.model_label ?? span.label]}
                    </span>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
