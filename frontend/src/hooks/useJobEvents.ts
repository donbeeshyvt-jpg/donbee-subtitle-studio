import { useEffect, useRef } from "react";
import type { Job } from "../api/types";
// SSE 作更新通知，GET 工作仍是真相。瀏覽器原生 EventSource 重連會帶 Last-Event-ID。
export function useJobEvents(jobs: Job[], refresh: () => Promise<void>) {
  const callback = useRef(refresh);
  callback.current = refresh;
  const ids = jobs
    .filter(
      (j) =>
        !["succeeded", "failed", "cancelled", "interrupted"].includes(j.status),
    )
    .slice(0, 2)
    .map((j) => j.job_id)
    .sort()
    .join(",");
  useEffect(() => {
    if (!ids || typeof EventSource === "undefined") return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const seen = new Map<string, number>();
    const streams = ids.split(",").map((id) => {
      const stream = new EventSource(`/v1/jobs/${id}/events`, {
        withCredentials: true,
      });
      const onEvent = (e: MessageEvent) => {
        const n = Number(e.lastEventId);
        if (n && n <= (seen.get(id) || 0)) return;
        if (n) seen.set(id, n);
        if (timer) return;
        timer = setTimeout(() => {
          timer = null;
          void callback.current().catch(() => {});
        }, 200);
      };
      for (const name of [
        "job.started",
        "job.succeeded",
        "job.failed",
        "job.cancelled",
        "job.interrupted",
        "stage.progress",
        "asset.ready",
        "artifact.ready",
        "transcript.partial",
      ])
        stream.addEventListener(name, onEvent as EventListener);
      return stream;
    });
    return () => {
      streams.forEach((s) => s.close());
      if (timer) clearTimeout(timer);
    };
  }, [ids]);
}
