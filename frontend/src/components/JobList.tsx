import CollapsibleSection from "./CollapsibleSection";
import type { Job } from "../api/types";
import { api } from "../api/client";
const status: Record<string, string> = {
  queued: "排隊中",
  running: "執行中",
  succeeded: "已完成",
  failed: "失敗",
  cancelled: "已取消",
  cancelling: "取消中",
  waiting: "等待依賴",
  interrupted: "工作中斷",
};
const kind: Record<string, string> = {
  probe: "讀取來源",
  acquire: "取得素材",
  acquire_subtitles: "取得來源字幕",
  analyze: "音訊辨識",
  classify: "音訊分類",
  refine: "精修",
  align: "字幕對齊",
  correct: "校字提案",
  summarize: "摘要",
  export: "匯出",
  workflow: "工作流程",
  plan_edits: "剪輯提案",
};
const stages: Record<string, string> = {
  queued: "等待派工",
  starting: "準備工作",
  download: "下載中",
  probe: "讀取媒體資訊",
  asr: "辨識語音",
  align: "對齊字幕",
  export: "輸出檔案",
  waiting_gpu: "等待顯示卡",
  succeeded: "",
  failed: "",
  cancelled: "",
  decode: "解析音訊",
  peaks: "建立波形",
};
function errorText(error: NonNullable<Job["error"]>) {
  const message = error.message || error.code || "工作失敗";
  const details = error.details;
  if (error.code !== "DEPENDENCY_FAILED" || !details || typeof details !== "object" || !("error" in details)) return message;
  const cause = details.error;
  if (!cause || typeof cause !== "object" || !("message" in cause) || typeof cause.message !== "string") return message;
  return cause.message ? `${message}：${cause.message}` : message;
}
export default function JobList({
  jobs,
  run,
}: {
  jobs: Job[];
  run: (fn: () => Promise<unknown>) => void;
}) {
  return (
    <CollapsibleSection
      id="jobs"
      title="任務"
      badge={jobs.length || undefined}
      hint={`${jobs.filter((j) => ["queued", "running", "waiting"].includes(j.status)).length} 個進行中`}
      className="jobs-panel"
    >
      {!jobs.length ? (
        <div className="empty small">
          下載、辨識與匯出會顯示在這裡。關閉頁面後，服務仍會保留工作狀態。
        </div>
      ) : (
        jobs.map((j) => {
          const terminal = [
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
          ].includes(j.status);
          const percent =
            typeof j.progress === "number" ? j.progress : j.progress?.percent;
          return (
            <article className="job-row" key={j.job_id}>
              <div>
                <strong>{kind[j.kind] || j.kind}</strong>
                <span className={`status ${j.status}`}>
                  {j.kind === "correct" && j.status === "succeeded"
                    ? j.result?.validation_status === "rejected" ||
                      j.result?.quality_status === "needs_manual_review"
                      ? "已完成，需人工複核"
                      : !Array.isArray(j.result?.patches) ||
                          !j.result.patches.length
                        ? "未提出可採用修改"
                        : "建議待確認"
                    : status[j.status] || j.status}
                </span>
                <small>
                  {j.stage ? (stages[j.stage] ?? j.stage) : ""}{" "}
                  {j.dispatch_paused ? "・已暫停新階段" : ""}
                </small>
                {percent != null && <progress max="100" value={percent} />}{" "}
                {j.error && (
                  <p className="error-text">
                    {errorText(j.error)}
                  </p>
                )}
                {/* 後端給使用者的說明（例如重疊的下載範圍已合併，2026-09-21） */}
                {typeof j.result?.notice === "string" && <p className="muted">{j.result.notice}</p>}
                {typeof j.result?.source_subtitle_revision === "string" && (
                  <p className="muted">
                    來源字幕已另行保留，可在來源字幕紀錄檢視。
                  </p>
                )}
                {typeof j.result?.comparison_revision === "string" && (
                  <p className="muted">
                    字幕比對已產生，請檢視成果並人工確認差異。
                  </p>
                )}
                {j.result?.artifacts?.map((a) => (
                  <a
                    className="artifact"
                    key={a.artifact_id}
                    href={
                      a.download_url || `/v1/artifacts/${a.artifact_id}/content`
                    }
                    download
                  >
                    {a.kind === "subtitle_comparison"
                      ? "字幕差異比對（需人工複核）"
                      : a.subtitle_origin === "source"
                        ? `來源字幕 · ${a.name || "下載 SRT"}`
                        : a.name || a.kind || "下載成果"}
                  </a>
                ))}
              </div>
              <div className="job-actions">
                {!terminal && (
                  <>
                    <label>
                      優先序
                      <input
                        aria-label={`任務 ${j.job_id} 優先序`}
                        type="number"
                        min="0"
                        max="100"
                        defaultValue={j.priority ?? 50}
                        onBlur={(e) =>
                          run(() =>
                            api.control(j.job_id, {
                              priority: Number(e.target.value),
                            }),
                          )
                        }
                      />
                    </label>
                    <button
                      onClick={() =>
                        run(() =>
                          api.control(j.job_id, {
                            dispatch_paused: !j.dispatch_paused,
                          }),
                        )
                      }
                    >
                      {j.dispatch_paused ? "恢復派工" : "暫停派工"}
                    </button>
                    <button onClick={() => run(() => api.cancel(j.job_id))}>
                      取消
                    </button>
                  </>
                )}
                {["failed", "cancelled", "interrupted"].includes(j.status) && (
                  <button onClick={() => run(() => api.retry(j.job_id))}>
                    重試
                  </button>
                )}
              </div>
            </article>
          );
        })
      )}
    </CollapsibleSection>
  );
}
