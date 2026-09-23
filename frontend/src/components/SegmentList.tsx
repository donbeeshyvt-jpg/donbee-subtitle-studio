// SPDX-License-Identifier: GPL-2.0-only
// 2026-09-16 移植 SegmentList 的 memo row、dnd-kit SortableContext 與 TanStack 虛擬列表；native 選單改明確按鈕。
import { memo, useRef } from "react";
import {
  DndContext,
  closestCenter,
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
  sortableKeyboardCoordinates,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useVirtualizer } from "@tanstack/react-virtual";
import { GripVertical, Copy, Trash2, ArrowUp, ArrowDown } from "lucide-react";
import type { Segment } from "../api/types";
import { formatTimecode, parseTimecode } from "../domain/time";
interface Props {
  items: Segment[];
  active: string;
  select: (id: string) => void;
  update: (s: Segment) => void;
  move: (from: number, to: number) => void;
  duplicate: (s: Segment) => void;
  remove: (id: string) => void;
  error: (message: string) => void;
  timeBase?: number;
}
const Row = memo(function Row({
  s,
  index,
  p,
}: {
  s: Segment;
  index: number;
  p: Props;
}) {
  const { attributes, listeners, setNodeRef, transform, transition } =
    useSortable({ id: s.id });
  return (
    <div
      ref={setNodeRef}
      className={`segment-row ${p.active === s.id ? "active" : ""}`}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      onClick={() => p.select(s.id)}
    >
      <button
        {...attributes}
        {...listeners}
        aria-label={`拖曳片段 ${index + 1}`}
        className="icon-button"
      >
        <GripVertical size={16} />
      </button>
      <input
        type="checkbox"
        aria-label={`輸出片段 ${index + 1}`}
        checked={s.selected}
        onChange={(e) => p.update({ ...s, selected: e.target.checked })}
      />
      <span className="row-number">{String(index + 1).padStart(2, "0")}</span>
      <input
        aria-label={`片段 ${index + 1} 名稱`}
        value={s.name}
        placeholder={s.kind === "marker" ? "標記" : "未命名片段"}
        onChange={(e) => p.update({ ...s, name: e.target.value })}
      />
      {(["start_us", "end_us"] as const).map((k) => (
        <input
          className="time-input"
          key={`${s.id}-${k}-${s[k]}`}
          aria-label={`片段 ${index + 1} ${k === "start_us" ? "入點" : "出點"}`}
          defaultValue={s[k] == null ? "" : formatTimecode(s[k]!, p.timeBase ?? 0)}
          placeholder="標記"
          onBlur={(e) => {
            try {
              const t = e.target.value
                ? parseTimecode(e.target.value, p.timeBase ?? 0)
                : undefined;
              if (k === "start_us" && t == null) throw Error("入點不可空白");
              const next = { ...s, [k]: t };
              if (next.end_us != null && next.end_us <= next.start_us)
                throw Error("出點必須晚於入點");
              p.update({
                ...next,
                kind: next.end_us == null ? "marker" : "clip",
              });
            } catch (err) {
              p.error((err as Error).message);
              e.target.value = s[k] == null ? "" : formatTimecode(s[k]!, p.timeBase ?? 0);
            }
          }}
        />
      ))}
      <span className="duration-label">
        {s.end_us == null
          ? "標記"
          : `${((s.end_us - s.start_us) / 1e6).toFixed(2)} 秒`}
      </span>
      <button
        className="icon-button"
        aria-label={`片段 ${index + 1} 上移`}
        disabled={index === 0}
        onClick={() => p.move(index, index - 1)}
      >
        <ArrowUp size={15} />
      </button>
      <button
        className="icon-button"
        aria-label={`片段 ${index + 1} 下移`}
        disabled={index === p.items.length - 1}
        onClick={() => p.move(index, index + 1)}
      >
        <ArrowDown size={15} />
      </button>
      <button
        className="icon-button"
        aria-label={`複製片段 ${index + 1}`}
        onClick={() => p.duplicate(s)}
      >
        <Copy size={15} />
      </button>
      <button
        className="icon-button"
        aria-label={`刪除片段 ${index + 1}`}
        onClick={() => p.remove(s.id)}
      >
        <Trash2 size={15} />
      </button>
    </div>
  );
});
export default function SegmentList(p: Props) {
  const parent = useRef<HTMLDivElement>(null);
  const virtual = useVirtualizer({
    count: p.items.length,
    getScrollElement: () => parent.current,
    estimateSize: () => 48,
    overscan: 6,
  });
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );
  return (
    <div>
      <div className="section-heading">
        <h2>
          匯出順序{" "}
          <span className="count">
            {p.items.filter((x) => x.selected && x.kind === "clip").length}
          </span>
        </h2>
        <span className="muted">拖曳排序，原片保持完整</span>
      </div>
      {!p.items.length ? (
        <div className="empty small">
          播放素材後，使用 I 設定入點、O 設定出點，再加入片段。
        </div>
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={({ active, over }) => {
            if (over)
              p.move(
                p.items.findIndex((s) => s.id === active.id),
                p.items.findIndex((s) => s.id === over.id),
              );
          }}
        >
          <SortableContext
            items={p.items.map((s) => s.id)}
            strategy={verticalListSortingStrategy}
          >
            <div className="segment-list" ref={parent}>
              <div
                style={{ height: virtual.getTotalSize(), position: "relative" }}
              >
                {virtual.getVirtualItems().map((row) => (
                  <div
                    key={p.items[row.index].id}
                    style={{
                      position: "absolute",
                      top: 0,
                      left: 0,
                      width: "100%",
                      transform: `translateY(${row.start}px)`,
                    }}
                  >
                    <Row s={p.items[row.index]} index={row.index} p={p} />
                  </div>
                ))}
              </div>
            </div>
          </SortableContext>
        </DndContext>
      )}
    </div>
  );
}
