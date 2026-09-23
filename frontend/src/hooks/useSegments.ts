// SPDX-License-Identifier: GPL-2.0-only
// 2026-09-16 拆分自上游 useSegments 的純片段狀態與 100 步 history；移除 native/FFmpeg effects。
import { useReducer } from "react";
import type { Segment } from "../api/types";
export interface History {
  past: Segment[][];
  present: Segment[];
  future: Segment[][];
}
export const initialHistory: History = { past: [], present: [], future: [] };
type Action =
  { type: "set" | "reset"; items: Segment[] } | { type: "undo" | "redo" };
export function historyReducer(h: History, a: Action): History {
  switch (a.type) {
    case "reset":
      return { ...initialHistory, present: a.items };
    case "set":
      return {
        past: [...h.past, h.present].slice(-100),
        present: a.items,
        future: [],
      };
    case "undo":
      return h.past.length
        ? {
            past: h.past.slice(0, -1),
            present: h.past.at(-1)!,
            future: [h.present, ...h.future],
          }
        : h;
    case "redo":
      return h.future.length
        ? {
            past: [...h.past, h.present].slice(-100),
            present: h.future[0],
            future: h.future.slice(1),
          }
        : h;
  }
}
export function useSegments() {
  const [history, dispatch] = useReducer(historyReducer, initialHistory);
  return {
    history,
    items: history.present,
    set: (items: Segment[]) => dispatch({ type: "set", items }),
    reset: (items: Segment[]) => dispatch({ type: "reset", items }),
    undo: () => dispatch({ type: "undo" }),
    redo: () => dispatch({ type: "redo" }),
  };
}
