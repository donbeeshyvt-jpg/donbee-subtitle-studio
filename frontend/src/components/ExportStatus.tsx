import type { Job } from "../api/types";
import { qualitySummary, type SubtitleQuality } from "../domain/terms";
// 匯出狀態顯示在「開始匯出」旁：進行中（含「等逐詞對齊」）、失敗原因、完成後的下載連結，
// 以及檔案實際存到哪個資料夾、叫什麼檔名（任務清單收合時也看得到）。
const stageText: Record<string, string> = {
  queued: "排隊中",
  export: "剪輯與字幕",
  "export.await_alignment": "等逐詞對齊完成，好讓字幕時間最準",
  waiting: "等待前置工作",
};
type SavedFile = { artifact_id?: string; path?: string; filename?: string };
const folderOf = (path: string) => path.replace(/[\\/][^\\/]*$/, "");
const nameOf = (file: SavedFile) => file.filename || (file.path || "").split(/[\\/]/).pop() || "";
export default function ExportStatus({ jobs, filter }: { jobs: Job[]; filter?: (job: Job) => boolean }) {
  const job = jobs.find((j) => j.kind === "export" && (!filter || filter(j)));
  if (!job) return null;
  if (["queued", "running", "waiting"].includes(job.status))
    return (
      <p className="export-status muted" role="status">
        匯出中…{job.stage && stageText[job.stage] ? `（${stageText[job.stage]}）` : ""}
      </p>
    );
  if (job.status === "succeeded") {
    const artifacts = job.result?.artifacts || [];
    const saved = ((job.result?.saved_files as SavedFile[] | undefined) || []).filter((f) => f.path);
    const manifest = job.result?.manifest as { warnings?: string[]; subtitle_quality?: SubtitleQuality[] } | undefined;
    const warnings = (manifest?.warnings || []) as string[];
    const quality = (manifest?.subtitle_quality || [])[0];
    // 字幕品質檢查（M4-C2）：不改字幕，只把問題數量說出來，讓人決定要不要回去修
    const summary = quality ? `${qualitySummary(quality)}${qualitySummary(quality).includes("沒有發現問題") ? "" : `（共 ${quality.entries} 則）`}` : "";
    return (
      <div className="export-status" role="status">
        <p>
          匯出完成：
          {artifacts.length
            ? artifacts.map((a) => (
                <a key={a.artifact_id} className="artifact" href={a.download_url || `/v1/artifacts/${a.artifact_id}/content`} download>
                  {a.name || (a.kind === "srt" ? "SRT 字幕" : ["audio", "m4a", "mp3", "wav", "flac", "aac", "opus"].includes(a.kind || "") ? "音訊檔" : a.kind === "mp4" ? "MP4 影片" : a.kind === "transcript_record" ? "逐字稿 JSON" : a.kind === "transcript_history" ? "修改紀錄 JSON" : a.kind || "下載成果")}
                </a>
              ))
            : "沒有產生檔案"}
        </p>
        {saved.length > 0 && (
          <p className="export-saved muted">
            已存到：{folderOf(saved[0].path!)}
            <br />
            檔名：{saved.map(nameOf).join("、")}
          </p>
        )}
        {summary && <p className="export-quality muted">{summary}</p>}
        {warnings.includes("alignment_unavailable_segment_timing") && (
          <p className="export-warning error-text">逐詞對齊沒有完成，這份字幕用的是句子時間（每句的起迄較粗）。可以稍後再按一次匯出。</p>
        )}
      </div>
    );
  }
  return (
    <p className="export-status error-text" role="alert">
      匯出失敗：{job.error?.message || job.error?.code || job.status}
    </p>
  );
}
