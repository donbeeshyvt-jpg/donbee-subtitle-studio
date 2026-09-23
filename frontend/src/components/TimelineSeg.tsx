// SPDX-License-Identifier: GPL-2.0-only
// 2026-09-16 移植 TimelineSeg 的 SegmentOrMarker 分支、百分比幾何與 active/selected 狀態；移除 native context/Motion。
import { memo, useRef, useState } from "react";
import type { Segment } from "../api/types";
import { formatTimecode } from "../domain/time";
interface Props {
  seg: Segment;
  index: number;
  duration: number;
  startUs?: number;
  active: boolean;
  onSelect: () => void;
  onBoundary: (key: "start_us" | "end_us", value: number) => void;
  // 按住片段中間拖動：整段平移（保持長度）
  onMove?: (startUs: number, endUs: number) => void;
  // 右鍵：刪除這個片段（不跳出瀏覽器選單）
  onRemove?: () => void;
}
export default memo(function TimelineSeg({
  seg,
  index,
  duration,
  startUs = 0,
  active,
  onSelect,
  onBoundary,
  onMove,
  onRemove,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [preview, setPreview] = useState<Partial<Segment>>({});
  // 拖動中的最新位置放在 ref（不依賴 state 重新渲染的時機），放開時直接用
  const drag = useRef<{ x: number; start: number; end: number; moved: boolean; now?: { start: number; end: number } } | null>(null);
  const autoFull = seg.tags?.auto === "full";
  const s = { ...seg, ...preview };
  const pct = (t: number) => `${(t / duration) * 100}%`;
  // 邊界拖曳中的最新值放 ref：放開時直接用，不依賴 state 重新渲染的時機（快速拖曳也不會漏掉最後位置）
  const trimming = useRef<{ key: "start_us" | "end_us"; value: number | null } | null>(null);
  function handle(key: "start_us" | "end_us") {
    return (
      <button
        className="trim-handle"
        aria-label={`${seg.name || index + 1} ${key === "start_us" ? "入點" : "出點"}`}
        onPointerDown={(e) => {
          e.stopPropagation();
          e.currentTarget.setPointerCapture(e.pointerId);
          trimming.current = { key, value: null };
          onSelect();
        }}
        onPointerMove={(e) => {
          const trim = trimming.current;
          if (!trim || trim.key !== key) return;
          const rect = ref.current!.parentElement!.getBoundingClientRect();
          const time =
            startUs +
            Math.round(
              Math.max(
                0,
                Math.min(
                  duration,
                  ((e.clientX - rect.left) / rect.width) * duration,
                ),
              ),
            );
          const value =
            key === "start_us"
              ? Math.min(time, (seg.end_us ?? startUs + duration) - 1)
              : Math.max(seg.start_us + 1, time);
          trim.value = value;
          setPreview({ [key]: value });
        }}
        onPointerUp={(e) => {
          e.stopPropagation();
          e.currentTarget.releasePointerCapture(e.pointerId);
          const trim = trimming.current;
          trimming.current = null;
          if (trim && trim.value != null) onBoundary(key, trim.value);
          setPreview({});
        }}
        onPointerCancel={() => {
          trimming.current = null;
          setPreview({});
        }}
        onKeyDown={(e) => {
          if (["ArrowLeft", "ArrowRight"].includes(e.key)) {
            e.preventDefault();
            onBoundary(
              key,
              (seg[key] ?? seg.start_us) +
                (e.key === "ArrowLeft" ? -100000 : 100000),
            );
          }
        }}
      >
        │
      </button>
    );
  }
  return s.end_us == null ? (
    <button
      className={`marker ${active ? "active" : ""}`}
      style={{ left: pct(s.start_us - startUs) }}
      onClick={(e) => {
        e.stopPropagation();
        onSelect();
      }}
      title={`${formatTimecode(s.start_us, startUs)} ${s.name}`}
      aria-label={`標記 ${index + 1} ${s.name}`}
    >
      ◆
    </button>
  ) : (
    <div
      ref={ref}
      className={`timeline-segment ${active ? "active" : ""} ${s.selected ? "" : "excluded"} ${autoFull ? "auto-full" : ""}`}
      style={{
        left: pct(s.start_us - startUs),
        width: pct(s.end_us - s.start_us),
      }}
      title={`${formatTimecode(s.start_us, startUs)} → ${formatTimecode(s.end_us, startUs)}（右鍵刪除）`}
      onClick={(e) => {
        e.stopPropagation();
        onSelect();
      }}
      onContextMenu={(e) => {
        if (!onRemove) return;
        e.preventDefault();
        e.stopPropagation();
        onRemove();
      }}
    >
      {handle("start_us")}
      <button
        className="segment-title"
        title="按住拖動可整段移動"
        onPointerDown={(e) => {
          if (!onMove || seg.end_us == null) return;
          e.stopPropagation();
          e.currentTarget.setPointerCapture(e.pointerId);
          drag.current = { x: e.clientX, start: seg.start_us, end: seg.end_us, moved: false };
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d || !e.currentTarget.hasPointerCapture(e.pointerId)) return;
          const rect = ref.current!.parentElement!.getBoundingClientRect();
          const delta = Math.round(((e.clientX - d.x) / rect.width) * duration);
          if (Math.abs(e.clientX - d.x) < 3 && !d.moved) return;
          d.moved = true;
          const length = d.end - d.start;
          const start = Math.max(startUs, Math.min(startUs + duration - length, d.start + delta));
          d.now = { start, end: start + length };
          setPreview({ start_us: start, end_us: start + length });
        }}
        onPointerUp={(e) => {
          const d = drag.current;
          drag.current = null;
          if (!d) return;
          e.stopPropagation();
          e.currentTarget.releasePointerCapture(e.pointerId);
          if (d.moved && d.now) onMove!(d.now.start, d.now.end);
          else onSelect();
          setPreview({});
        }}
        onPointerCancel={() => {
          drag.current = null;
          setPreview({});
        }}
        onClick={(e) => {
          // pointer 已處理選取；避免拖動結束後再觸發一次
          e.stopPropagation();
          if (!onMove) onSelect();
        }}
      >
        {String(index + 1).padStart(2, "0")} {s.name}
      </button>
      {handle("end_us")}
    </div>
  );
});
