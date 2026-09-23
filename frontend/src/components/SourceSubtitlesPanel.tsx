import { useEffect, useState } from "react";
import { request } from "../api/client";
import { formatTimecode } from "../domain/time";
interface SourceSubtitle {
  id?: string;
  transcript_revision?: string;
  language?: string;
  origin?: string;
  status?: string;
  coverage?: { start_us: number; end_us: number }[];
  original_artifact_id?: string;
}
export default function SourceSubtitlesPanel({
  project,
  sourceId,
  enabled,
  refreshKey,
  timeBase = 0,
}: {
  project: string;
  sourceId: string;
  enabled: boolean;
  refreshKey: string;
  timeBase?: number;
}) {
  const [rows, setRows] = useState<SourceSubtitle[]>([]),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setRows([]);
    setError("");
    if (!enabled || !project || !sourceId) return;
    (async () => {
      let cursor: string | number | null = null;
      const result: SourceSubtitle[] = [];
      const visited = new Set<string>();
      do {
        const r: {
          items: SourceSubtitle[];
          next_cursor?: string | number | null;
        } = await request(
          `/projects/${project}/source-subtitles?source_id=${encodeURIComponent(sourceId)}&limit=200${cursor == null ? "" : `&cursor=${encodeURIComponent(String(cursor))}`}`,
        );
        if (!active) return;
        result.push(...r.items);
        cursor = r.next_cursor ?? null;
        if (cursor != null && visited.has(String(cursor)))
          throw Error("來源字幕分頁游標重複");
        if (cursor != null) visited.add(String(cursor));
      } while (cursor != null);
      setRows(result);
    })().catch((e) => {
      if (active) setError(e instanceof Error ? e.message : String(e));
    });
    return () => {
      active = false;
    };
  }, [project, sourceId, enabled, refreshKey]);
  if (!enabled || !sourceId) return null;
  return (
    <details className="workflow-panel">
      <summary>
        來源字幕紀錄{" "}
        <span className="muted">{rows.length} 份 · 與辨識原稿分開保留</span>
      </summary>
      {error && <p className="error-text">來源字幕紀錄讀取失敗：{error}</p>}
      {!error && !rows.length && (
        <p className="muted">
          尚未取得來源字幕。可先建立使用來源字幕或比對字幕的流程。
        </p>
      )}
      {rows.map((row, index) => (
        <article
          className="proposal"
          key={row.id || row.transcript_revision || index}
        >
          <strong>來源字幕 {index + 1}</strong>
          <p>
            語言：
            {(
              {
                auto: "自動",
                "zh-Hant": "繁體中文",
                zh: "中文",
                ja: "日文",
                en: "英文",
              } as Record<string, string>
            )[row.language || ""] ||
              row.language ||
              "未提供"}
          </p>
          <p className="muted">來源字幕的時間與文字仍需人工複核。</p>
          <p>
            {row.origin === "manual"
              ? "人工來源字幕"
              : row.origin === "automatic"
                ? "平台自動字幕"
                : "字幕種類未提供"}{" "}
            ·{" "}
            {row.status === "ready"
              ? "已取得"
              : row.status === "empty_selection"
                ? "選定範圍沒有字幕"
                : "狀態未提供"}
          </p>
          {Boolean(row.coverage?.length) && (
            <details>
              <summary>涵蓋 {row.coverage!.length} 個來源區段</summary>
              {row.coverage!.map((range, i) => (
                <p className="timecode" key={i}>
                  {formatTimecode(range.start_us, timeBase)} →{" "}
                  {formatTimecode(range.end_us, timeBase)}
                </p>
              ))}
            </details>
          )}
          {row.original_artifact_id && (
            <a
              className="artifact"
              href={`/v1/artifacts/${encodeURIComponent(row.original_artifact_id)}/content`}
              download
            >
              下載來源原始字幕
            </a>
          )}
        </article>
      ))}
    </details>
  );
}
