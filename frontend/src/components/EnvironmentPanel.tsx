import { useCallback, useEffect, useRef, useState } from "react";
import type {
  EnvironmentReport,
  EnvironmentStatus,
  ModelItemStatus,
  ModelStatus,
  ModelStatusItem,
} from "../api/types";

// 環境檢查分頁：只顯示服務回報的狀態與中文指引；環境類軟體由使用者自行安裝，這裡不執行安裝。
// 模型：必備模型由啟動器處理；選用模型需使用者確認後才複製或下載，進度以輪詢顯示。
const statusText: Record<EnvironmentStatus, string> = {
  ok: "正常",
  missing: "缺少",
  outdated: "版本過舊",
  unreachable: "無法連線",
};
const modelText: Record<ModelItemStatus, string> = {
  present: "已就緒",
  missing: "缺少",
  size_mismatch: "大小不符",
  revision_mismatch: "版本不符",
  skipped_optional: "略過",
};
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const gigabytes = (bytes?: number) => `${((bytes ?? 0) / 1e9).toFixed(1)} GB`;

export default function EnvironmentPanel({
  load,
  recheck,
  models,
  download,
  pollMs = 2000,
}: {
  load: () => Promise<EnvironmentReport>;
  recheck: () => Promise<EnvironmentReport>;
  models?: () => Promise<ModelStatus>;
  download?: (ids: string[]) => Promise<unknown>;
  pollMs?: number;
}) {
  const [report, setReport] = useState<EnvironmentReport | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null);
  const [modelError, setModelError] = useState("");
  const [polling, setPolling] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const run = useCallback(async (action: () => Promise<EnvironmentReport>) => {
    setBusy(true);
    setError("");
    try {
      setReport(await action());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);
  useEffect(() => {
    void run(load);
  }, [load, run]);
  const refreshModels = useCallback(async () => {
    if (!models) return null;
    try {
      const status = await models();
      if (mounted.current) setModelStatus(status);
      return status;
    } catch (e) {
      if (mounted.current) setModelError(e instanceof Error ? e.message : String(e));
      return null;
    }
  }, [models]);
  useEffect(() => {
    void refreshModels();
  }, [refreshModels]);
  // 下載進行中時輪詢狀態，直到 done／failed 或超過上限
  const pollUntilSettled = useCallback(async () => {
    setPolling(true);
    try {
      for (let i = 0; i < 3600; i += 1) {
        if (!mounted.current) return;
        const status = await refreshModels();
        if (!status || status.download?.status !== "running") return;
        await sleep(pollMs);
      }
    } finally {
      if (mounted.current) setPolling(false);
    }
  }, [refreshModels, pollMs]);
  const startDownload = useCallback(
    async (item: ModelStatusItem) => {
      if (!download) return;
      const id = item.id;
      // ct2 類（Breeze-ASR）：確認時寫清楚官方來源、下載量與授權，並說明會在本機轉換
      const ok = window.confirm(
        item.kind === "ct2"
          ? `將從 Hugging Face 的 ${item.repo}（官方）下載約 ${gigabytes(item.download_bytes)} 的權重（授權 ${item.license ?? "見模型頁"}），在本機轉成可用格式後放進專案的 models 資料夾（轉換後約 3.1 GB，下載的原始權重轉完會刪除）。需要一段時間，確定開始？`
          : `將複製或下載 ${id}。大型模型可能需要數 GB 磁碟空間與較長時間，確定開始？`,
      );
      if (!ok) return;
      setModelError("");
      try {
        await download([id]);
      } catch (e) {
        setModelError(e instanceof Error ? e.message : String(e));
        return;
      }
      await pollUntilSettled();
    },
    [download, pollUntilSettled],
  );
  const problems = report?.packages?.items.filter((p) => p.status !== "ok" && p.status !== "optional_missing") ?? [];
  const optionalMissing = report?.packages?.items.filter((p) => p.status === "optional_missing") ?? [];
  const items = report ? Object.entries(report.items) : [];
  const downloading = modelStatus?.download?.status === "running" || polling;
  return (
    <section className="environment-panel" aria-label="環境檢查">
      <div className="environment-head">
        <h3>環境檢查</h3>
        <span>最後檢查：{report?.checked_at ?? "尚未檢查"}</span>
        <button disabled={busy} onClick={() => void run(recheck)}>
          重新檢查
        </button>
      </div>
      {error && <p role="alert">無法取得環境檢查：{error}</p>}
      {report && (
        <>
          <table className="environment-table">
            <thead>
              <tr>
                <th>項目</th>
                <th>狀態</th>
                <th>版本</th>
                <th>說明</th>
              </tr>
            </thead>
            <tbody>
              {items.map(([key, item]) => (
                <tr key={key} data-status={item.status}>
                  <td>{item.label}</td>
                  <td>
                    <span className={`badge badge-${item.status}`}>{statusText[item.status]}</span>
                  </td>
                  <td>{item.version ?? ""}</td>
                  <td>{item.status !== "ok" && item.guidance ? item.guidance : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="environment-section">
            <h4>Python 套件</h4>
            {problems.length ? (
              <ul>
                {problems.map((p) => (
                  <li key={p.name}>
                    {p.name}：{p.status === "missing" ? "未安裝" : `已安裝 ${p.installed}`}，需要 {p.required ?? "可編輯安裝"}
                  </li>
                ))}
              </ul>
            ) : (
              <p>套件符合鎖定清單。</p>
            )}
            {optionalMissing.length > 0 && (
              <p className="muted">選用群組未安裝：{optionalMissing.map((p) => `${p.name}（${p.group}）`).join("、")}（需要時執行 python -m bootstrap --groups core,models,dev,{[...new Set(optionalMissing.map((p) => p.group))].join(",")}）</p>
            )}
          </div>
          <div className="environment-section">
            <h4>模型</h4>
            {modelStatus ? (
              <>
                {modelError && <p role="alert">模型狀態錯誤：{modelError}</p>}
                <ul className="model-list">
                  {modelStatus.items
                    .filter((m) => m.status !== "skipped_optional")
                    .map((m) => (
                      <li key={m.id} data-status={m.status}>
                        {m.kind === "ct2" && m.label ? (
                          <>
                            <strong>{m.label}</strong>
                            {m.status !== "present" && m.download_bytes ? <span className="muted">下載約 {gigabytes(m.download_bytes)}</span> : null}
                          </>
                        ) : (
                          <code>{m.id}</code>
                        )}
                        {!m.required && <span className="badge badge-outdated">選用</span>}
                        <span className={`badge badge-${m.status === "present" ? "ok" : "missing"}`}>{modelText[m.status]}</span>
                        {!m.required && m.status !== "present" && download && (
                          <button disabled={downloading} onClick={() => void startDownload(m)}>
                            {m.kind === "ct2" ? "下載並轉換" : "複製或下載"}
                          </button>
                        )}
                      </li>
                    ))}
                </ul>
                {modelStatus.download?.status === "running" && (
                  <p>進行中：{modelStatus.download.ids.join("、")}（可先繼續其他操作）</p>
                )}
                {modelStatus.download?.status === "failed" && (
                  <p role="alert">複製或下載失敗：{modelStatus.download.error ?? "未知錯誤"}</p>
                )}
              </>
            ) : report.models?.missing.length ? (
              <ul>
                {report.models.missing.map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            ) : (
              <p>必備模型齊備。</p>
            )}
            {!modelStatus && report.models?.optional_missing?.length ? (
              <p>選用未安裝：{report.models.optional_missing.join("、")}</p>
            ) : null}
          </div>
          {report.paths && (
            <div className="environment-section">
              <h4>路徑</h4>
              <ul>
                {Object.entries(report.paths).map(([key, value]) => (
                  <li key={key}>
                    {key}：{value}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </section>
  );
}
