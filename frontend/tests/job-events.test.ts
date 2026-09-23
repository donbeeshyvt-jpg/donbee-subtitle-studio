// @vitest-environment jsdom
// M5-2 I03：事件串流重連後（瀏覽器會帶 Last-Event-ID 續傳）可能再收到同一個事件編號；網頁要去重，不重複重新整理。
import { renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useJobEvents } from "../src/hooks/useJobEvents";
import type { Job } from "../src/api/types";

type Listener = (event: MessageEvent) => void;
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners: Record<string, Listener[]> = {};
  closed = false;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(name: string, listener: Listener) {
    (this.listeners[name] ||= []).push(listener);
  }
  close() {
    this.closed = true;
  }
  emit(name: string, id: string) {
    for (const listener of this.listeners[name] || []) listener(new MessageEvent(name, { lastEventId: id, data: "{}" }));
  }
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  FakeEventSource.instances = [];
});

it("ignores events it has already seen after a reconnect and refreshes once per new event burst", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("EventSource", FakeEventSource);
  const refresh = vi.fn().mockResolvedValue(undefined);
  const jobs = [{ job_id: "j1", kind: "analyze", status: "running" } as Job];
  const { unmount } = renderHook(() => useJobEvents(jobs, refresh));
  const [stream] = FakeEventSource.instances;
  expect(stream.url).toBe("/v1/jobs/j1/events");
  stream.emit("stage.progress", "5");
  stream.emit("stage.progress", "6");
  await vi.advanceTimersByTimeAsync(250);
  expect(refresh).toHaveBeenCalledTimes(1); // 短時間內多個事件合併成一次重新整理
  stream.emit("stage.progress", "5"); // 重連後重送的舊事件
  stream.emit("stage.progress", "6");
  await vi.advanceTimersByTimeAsync(250);
  expect(refresh).toHaveBeenCalledTimes(1); // 已看過：不再重新整理
  stream.emit("job.succeeded", "7");
  await vi.advanceTimersByTimeAsync(250);
  expect(refresh).toHaveBeenCalledTimes(2);
  unmount();
  expect(stream.closed).toBe(true);
});
