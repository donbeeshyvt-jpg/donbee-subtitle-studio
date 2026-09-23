// SPDX-License-Identifier: GPL-2.0-only
// 2026-09-16 移植 Timeline 的分層波形、SegmentOrMarker、獨立遊標、縮放視窗結構。媒體資料改 API，事件改 Pointer。
import { useEffect, useRef, useState } from "react";
import type { Segment } from "../api/types";
import TimelineSeg from "./TimelineSeg";
import { formatTimecode } from "../domain/time";
import { api } from "../api/client";
export default function Timeline({
  duration,
  startUs = 0,
  time,
  items,
  active,
  assetId,
  seek,
  select,
  boundary,
  inUs = null,
  outUs = null,
  move,
  onRange,
  onClearRange,
  remove,
}: {
  duration: number;
  startUs?: number;
  time: number;
  items: Segment[];
  active: string;
  assetId?: string;
  seek: (us: number) => void;
  select: (id: string) => void;
  boundary: (id: string, key: "start_us" | "end_us", us: number) => void;
  // 入點／出點（來源時間）：畫成範圍帶，讓 I／O 按了看得到
  inUs?: number | null;
  outUs?: number | null;
  move?: (id: string, startUs: number, endUs: number) => void;
  // 波形拖曳選取：按下＝入點、放開＝出點；範圍外點一下＝取消
  onRange?: (inUs: number, outUs: number) => void;
  onClearRange?: () => void;
  // 片段條右鍵刪除
  remove?: (id: string) => void;
}) {
  const [zoom, setZoom] = useState(1),
    [windowStart, setWindowStart] = useState(0),
    [peaks, setPeaks] = useState<number[]>([]),
    [waveError, setWaveError] = useState("");
  const viewport = useRef<HTMLDivElement>(null);
  const surface = useRef<HTMLDivElement>(null);
  const length = Math.max(duration - startUs, 1);
  // 依游標 X 換算成來源時間（表面寬度＝整段素材 × 縮放）
  const timeAt = (clientX: number) => {
    const rect = surface.current!.getBoundingClientRect();
    return startUs + Math.round(Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)) * length);
  };
  const scrubbing = useRef(false);
  const selecting = useRef<{ x: number; start: number; moved: boolean } | null>(null);
  const [selectPreview, setSelectPreview] = useState<{ a: number; b: number } | null>(null);
  const windowEnd = Math.min(length, windowStart + length / zoom);
  useEffect(() => {
    setWindowStart(0);
    setZoom(1);
    if (viewport.current) viewport.current.scrollLeft = 0;
  }, [assetId]);
  useEffect(() => {
    if (!assetId) return;
    let alive = true;
    const timer = setTimeout(() => {
      api
        .peaks(
          assetId,
          Math.round(startUs + windowStart),
          Math.round(startUs + windowEnd),
        )
        .then((r) => {
          if (alive) {
            setPeaks(Array.isArray(r.peaks) ? r.peaks : []);
            setWaveError("");
          }
        })
        .catch(() => {
          if (alive) {
            setPeaks([]);
            setWaveError("波形尚未就緒");
          }
        });
    }, 200);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [assetId, windowStart, windowEnd, startUs]);
  return (
    <section className="timeline-panel" aria-label="來源時間軸">
      <div className="section-heading">
        <h2>素材時間軸</h2>
        <span className="timecode">
          {formatTimecode(time, startUs)} / {formatTimecode(duration, startUs)}
        </span>
        {startUs > 0 && <span className="muted">來源 {formatTimecode(startUs)} 起</span>}
        <label className="zoom-label">
          縮放
          <input
            aria-label="時間軸縮放"
            type="range"
            min="1"
            max="30"
            value={zoom}
            onChange={(e) => setZoom(Number(e.target.value))}
          />
          <span>{zoom}×</span>
        </label>
      </div>
      <div
        className="timeline-scroll"
        ref={viewport}
        onScroll={(e) =>
          setWindowStart(
            (e.currentTarget.scrollLeft / e.currentTarget.scrollWidth) * length,
          )
        }
      >
        <div
          ref={surface}
          className="timeline-surface"
          style={{ width: `${zoom * 100}%` }}
          onClick={(e) => seek(timeAt(e.clientX))}
        >
          <div
            className="ruler"
            title="按住拖動可拉動播放頭"
            onPointerDown={(e) => {
              e.stopPropagation();
              e.currentTarget.setPointerCapture(e.pointerId);
              scrubbing.current = true;
              seek(timeAt(e.clientX));
            }}
            onPointerMove={(e) => {
              if (!scrubbing.current) return;
              seek(timeAt(e.clientX));
            }}
            onPointerUp={(e) => {
              e.stopPropagation();
              scrubbing.current = false;
              e.currentTarget.releasePointerCapture(e.pointerId);
            }}
            onPointerCancel={() => {
              scrubbing.current = false;
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {Array.from({ length: Math.ceil(zoom * 8) + 1 }, (_, i) => (
              <span key={i} style={{ left: `${(i / (zoom * 8)) * 100}%` }}>
                {formatTimecode(Math.round((length * i) / (zoom * 8))).slice(0, -4)}
              </span>
            ))}
          </div>
          <svg
            aria-label="音訊波形"
            className="waveform"
            preserveAspectRatio="none"
            viewBox="0 0 1000 60"
            style={{
              left: `${(windowStart / length) * 100}%`,
              width: `${((windowEnd - windowStart) / length) * 100}%`,
            }}
          >
            {peaks.map((p, i) => (
              <line
                key={i}
                x1={(i / peaks.length) * 1000}
                x2={(i / peaks.length) * 1000}
                y1={30 - Math.abs(p) * 28}
                y2={30 + Math.abs(p) * 28}
              />
            ))}
          </svg>
          {!peaks.length && (
            <span className="waveform-note">
              {assetId ? waveError || "載入波形…" : "加入素材後顯示波形"}
            </span>
          )}
          <div
            className="wave-area"
            aria-label="波形：按住拖曳選取入點到出點，範圍外點一下取消"
            title="按住拖曳：入點到出點；範圍外點一下：取消"
            onPointerDown={(e) => {
              e.stopPropagation();
              e.currentTarget.setPointerCapture(e.pointerId);
              selecting.current = { x: e.clientX, start: timeAt(e.clientX), moved: false };
            }}
            onPointerMove={(e) => {
              const s = selecting.current;
              if (!s) return;
              if (!s.moved && Math.abs(e.clientX - s.x) < 3) return;
              s.moved = true;
              setSelectPreview({ a: s.start, b: timeAt(e.clientX) });
            }}
            onPointerUp={(e) => {
              e.stopPropagation();
              const s = selecting.current;
              selecting.current = null;
              e.currentTarget.releasePointerCapture(e.pointerId);
              setSelectPreview(null);
              if (!s) return;
              const at = timeAt(e.clientX);
              if (s.moved) {
                const a = Math.min(s.start, at);
                const b = Math.max(s.start, at);
                if (b > a) onRange?.(a, b);
                return;
              }
              // 點一下：有選取且點在範圍外 → 取消；否則跳到該處
              const inside = inUs != null && outUs != null && at >= inUs && at <= outUs;
              if (inUs != null && outUs != null && !inside) onClearRange?.();
              else seek(at);
            }}
            onPointerCancel={() => {
              selecting.current = null;
              setSelectPreview(null);
            }}
            onClick={(e) => e.stopPropagation()}
          />
          {selectPreview && (
            <div
              className="io-range is-preview"
              style={{
                left: `${((Math.min(selectPreview.a, selectPreview.b) - startUs) / length) * 100}%`,
                width: `${(Math.abs(selectPreview.b - selectPreview.a) / length) * 100}%`,
              }}
            />
          )}
          {inUs != null && outUs != null && outUs > inUs && (
            <div
              className="io-range"
              aria-label="入點到出點"
              style={{
                left: `${((inUs - startUs) / length) * 100}%`,
                width: `${((outUs - inUs) / length) * 100}%`,
              }}
            >
              <span className="io-mark io-in">I</span>
              <span className="io-mark io-out">O</span>
            </div>
          )}
          <div className="segments-track">
            {items.map(
              (s, i) =>
                (s.end_us ?? s.start_us) >= startUs + windowStart &&
                s.start_us <= startUs + windowEnd && (
                  <TimelineSeg
                    key={s.id}
                    seg={s}
                    index={i}
                    duration={length}
                    startUs={startUs}
                    active={s.id === active}
                    onSelect={() => select(s.id)}
                    onBoundary={(k, t) => boundary(s.id, k, t)}
                    onMove={move ? (a, b) => move(s.id, a, b) : undefined}
                    onRemove={remove ? () => remove(s.id) : undefined}
                  />
                ),
            )}
          </div>
          <div
            className="playhead"
            style={{ left: `${((time - startUs) / length) * 100}%` }}
          >
            <span>▼</span>
          </div>
        </div>
      </div>
    </section>
  );
}
