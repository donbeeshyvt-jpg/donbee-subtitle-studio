import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import JobList from "../src/components/JobList";
afterEach(cleanup);
it("names source caption jobs and explains dependency failures", () => {
  render(<JobList run={() => {}} jobs={[
    { job_id: "caption", kind: "acquire_subtitles", status: "failed", error: { message: "沒有字幕" } },
    { job_id: "root", kind: "workflow", status: "failed", error: { code: "DEPENDENCY_FAILED", message: "前置工作未完成", details: { error: { message: "請改用生成字幕" } } } },
  ]} />);
  expect(screen.getByText("取得來源字幕")).toBeTruthy();
  expect(screen.getByText("前置工作未完成：請改用生成字幕")).toBeTruthy();
});
it("falls back safely when dependency details are malformed", () => {
  render(<JobList run={() => {}} jobs={[
    { job_id: "root", kind: "workflow", status: "failed", error: { code: "DEPENDENCY_FAILED", message: "前置工作未完成", details: { error: { message: { invalid: true } } } } },
  ]} />);
  expect(screen.getByText("前置工作未完成")).toBeTruthy();
});

// 2026-09-21 真瀏覽器：重疊的下載範圍被合併成一段，畫面沒說 → 任務清單顯示後端的說明
it("shows the note when overlapping download ranges were merged", () => {
  const notice = "你填的 2 段範圍有重疊，已合併成 1 段下載：00:05:00–00:15:00。要分開的片段，請在時間軸用入點／出點「加入片段」。";
  render(<JobList run={() => {}} jobs={[{ job_id: "a1", kind: "acquire", status: "succeeded", result: { asset_ids: ["x"], notice } }]} />);
  expect(screen.getByText(notice)).toBeTruthy();
});
