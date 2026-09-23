import { useEffect, useState } from "react";
import { formatTimecode, parseTimecode } from "../domain/time";

// 可直接輸入的時間讀數（2026-09-21 真瀏覽器：入出點只能靠播放位置或拖曳，切不準）。
// 顯示與輸入都用「素材相對時間」（base＝素材在來源中的起點）；滑鼠移上去看對應的來源時間。
// Enter 或離開欄位才套用；清空＝取消這個點；格式錯或超出素材長度時說明原因並還原。
export default function TimecodeInput({
  label,
  value,
  base,
  max,
  onCommit,
  onError,
}: {
  label: string;
  value: number | null;
  base: number;
  max?: number; // 素材長度（微秒，相對）；0 或未知就不檢查上限
  onCommit: (us: number | null) => void;
  onError: (message: string) => void;
}) {
  const shown = value == null ? "" : formatTimecode(value, base);
  const [draft, setDraft] = useState(shown);
  useEffect(() => setDraft(shown), [shown]);
  const commit = () => {
    const text = draft.trim();
    if (text === shown) return;
    if (!text) {
      onCommit(null);
      return;
    }
    try {
      const us = parseTimecode(text, base);
      if (us < base || (max && us > base + max)) {
        throw Error(`${label}超出素材長度（素材 00:00:00.000–${formatTimecode(base + (max ?? 0), base)}）`);
      }
      onCommit(us);
      setDraft(formatTimecode(us, base));
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
      setDraft(shown);
    }
  };
  return (
    <input
      className="timecode timecode-input"
      aria-label={label}
      value={draft}
      placeholder="--:--:--.---"
      title={value == null ? "可直接輸入素材時間，例如 4:00 或 00:04:00.000" : `來源時間 ${formatTimecode(value)}`}
      inputMode="decimal"
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") setDraft(shown);
      }}
    />
  );
}
