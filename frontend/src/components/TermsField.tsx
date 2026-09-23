// 詞彙／參考資料文字框（2026-09-20）：可直接輸入，也可「匯入文字檔」；actions 放額外按鈕（例如「內容拆解單詞」）。
import { useRef, type ReactNode } from "react";
import { readTextFile } from "../domain/terms";

export default function TermsField({
  label,
  ariaLabel,
  importAriaLabel,
  value,
  onChange,
  onImport,
  placeholder,
  rows = 2,
  maxLength,
  actions,
  status,
  help,
  className = "",
}: {
  label: ReactNode;
  ariaLabel: string;
  importAriaLabel: string;
  value: string;
  onChange: (value: string) => void;
  onImport: (text: string) => void;
  placeholder?: string;
  rows?: number;
  maxLength?: number;
  actions?: ReactNode;
  status?: ReactNode;
  help?: ReactNode;
  className?: string;
}) {
  const file = useRef<HTMLInputElement>(null);
  return (
    <div className={`terms-field ${className}`.trim()}>
      <label>
        <span className="terms-label">{label}</span>
        <textarea
          aria-label={ariaLabel}
          rows={rows}
          placeholder={placeholder}
          value={value}
          maxLength={maxLength}
          onChange={(e) => onChange(e.target.value)}
        />
      </label>
      <div className="terms-actions">
        <button type="button" onClick={() => file.current?.click()}>
          匯入文字檔
        </button>
        <input
          ref={file}
          type="file"
          accept=".txt,.md,.csv,text/plain"
          hidden
          aria-label={importAriaLabel}
          onChange={async (e) => {
            const picked = e.target.files?.[0];
            e.target.value = "";
            if (picked) onImport(await readTextFile(picked));
          }}
        />
        {actions}
        {maxLength ? (
          <span className="muted terms-count">
            {value.length}／{maxLength} 字
          </span>
        ) : null}
        {status ? (
          <span className="muted terms-status" role="status">
            {status}
          </span>
        ) : null}
      </div>
      {help}
    </div>
  );
}
