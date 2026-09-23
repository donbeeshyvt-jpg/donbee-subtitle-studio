import { useState } from "react";
import type { OutputRoot } from "../api/types";
import { errorMessage, isDialogUnavailable } from "../domain/errors";
// 指定本地輸出資料夾：用原生視窗選（後端開視窗、回傳絕對路徑），不用打字；
// 這台電腦開不了視窗時才顯示手動輸入。匯入檔旁的 subtitle_studio 會自動出現在清單並成為預設。
// 自己選的資料夾也一樣：檔案放在 <選的資料夾>/subtitle_studio（後端回傳 target），檔名跟著素材檔名。
export default function OutputRootPicker({
  label,
  roots,
  value,
  onChange,
  addRoot,
  pickFolder,
  disabled,
}: {
  label: string;
  roots: OutputRoot[];
  value: string;
  onChange: (id: string) => void;
  addRoot: (path: string, name: string, create: boolean) => Promise<{ items: OutputRoot[]; id: string }>;
  pickFolder: (title: string) => Promise<{ path: string | null; cancelled: boolean }>;
  disabled?: boolean;
}) {
  const [path, setPath] = useState("");
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [manual, setManual] = useState(false);
  const selected = roots.find((r) => r.id === value);
  async function add(target: string) {
    const result = await addRoot(target, "", true);
    onChange(result.id);
  }
  return (
    <div className="output-root-picker">
      <label>
        {label}儲存位置
        <select aria-label={`${label}儲存位置`} value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
          <option value="">專案資料目錄（data/artifacts）</option>
          {roots.map((r) => (
            <option key={r.id} value={r.id} title={r.target || r.path}>
              {r.kind === "sidecar" ? "匯入檔旁：" : ""}
              {r.name}
            </option>
          ))}
        </select>
      </label>
      <div className="output-root-add">
        <button
          type="button"
          aria-label={`選擇${label}資料夾`}
          disabled={disabled || working}
          onClick={async () => {
            setWorking(true);
            setError("");
            try {
              const picked = await pickFolder(`選擇${label}資料夾`);
              if (picked.path) await add(picked.path);
            } catch (e) {
              // 只有「這台電腦開不了視窗」才改成手動輸入；連線失效等其他錯誤只顯示訊息
              if (isDialogUnavailable(e)) setManual(true);
              setError(errorMessage(e));
            } finally {
              setWorking(false);
            }
          }}
        >
          {working ? "等待選擇…" : "選擇資料夾…"}
        </button>
        {manual && (
          <>
            <input
              aria-label={`${label}資料夾路徑`}
              placeholder="貼上本機資料夾完整路徑"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              disabled={disabled || working}
            />
            <button
              type="button"
              aria-label={`加入${label}資料夾`}
              disabled={disabled || working || !path.trim()}
              onClick={async () => {
                setWorking(true);
                setError("");
                try {
                  await add(path.trim());
                  setPath("");
                } catch (e) {
                  setError(e instanceof Error ? e.message : String(e));
                } finally {
                  setWorking(false);
                }
              }}
            >
              加入
            </button>
          </>
        )}
      </div>
      <small className="muted output-root-path">輸出到：{selected?.target || selected?.path || "專案資料目錄（data/artifacts）"}</small>
      {error && (
        <small className="error-text" role="alert">
          {error}
        </small>
      )}
    </div>
  );
}
