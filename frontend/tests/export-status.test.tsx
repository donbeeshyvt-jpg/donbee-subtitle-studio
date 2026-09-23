// @vitest-environment jsdom
// 使用者 2026-09-18 第四輪：按「開始匯出」沒有任何反應（工作失敗只藏在收合的任務清單）。
// 匯出結果與失敗原因必須顯示在按鈕旁，成功時直接給下載連結。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import ExportStatus from "../src/components/ExportStatus";
import type { Job } from "../src/api/types";
afterEach(cleanup);

const job = (overrides: Partial<Job>): Job => ({ job_id: "e1", kind: "export", status: "succeeded", ...overrides }) as Job;

it("shows the failure reason of the newest export next to the button", () => {
  render(<ExportStatus jobs={[job({ status: "failed", error: { code: "SOURCE_RANGE_UNAVAILABLE", message: "選區尚未取得可匯出素材" } })]} />);
  expect(screen.getByRole("alert").textContent).toContain("匯出失敗");
  expect(screen.getByRole("alert").textContent).toContain("選區尚未取得可匯出素材");
});

it("shows progress while exporting and download links when done", () => {
  const { rerender } = render(<ExportStatus jobs={[job({ status: "running", stage: "export" })]} />);
  expect(screen.getByText(/匯出中/)).toBeTruthy();
  rerender(
    <ExportStatus
      jobs={[job({ result: { artifacts: [{ artifact_id: "art1", kind: "srt", name: "字幕.srt" }, { artifact_id: "art2", kind: "audio" }] } })]}
    />,
  );
  expect(screen.getByText(/匯出完成/)).toBeTruthy();
  const links = screen.getAllByRole("link");
  expect(links.map((l) => l.getAttribute("href"))).toEqual(["/v1/artifacts/art1/content", "/v1/artifacts/art2/content"]);
  expect(links[0].textContent).toContain("字幕.srt");
});

it("ignores non-export jobs and renders nothing before any export", () => {
  const { container } = render(<ExportStatus jobs={[job({ kind: "analyze", status: "running" })]} />);
  expect(container.textContent).toBe("");
});

// 使用者 2026-09-19：「為什麼檔名是這樣」——匯出完成要直接說存到哪個資料夾、叫什麼檔名；等對齊時也要看得出在等什麼
it("tells where the files were saved and under which names", () => {
  render(
    <ExportStatus
      jobs={[
        job({
          result: {
            artifacts: [{ artifact_id: "art1", kind: "srt" }],
            saved_files: [
              { artifact_id: "art1", path: "D:\案子\EP7\subtitle_studio\剪好的音訊.srt", filename: "剪好的音訊.srt" },
              { artifact_id: "art2", path: "D:\案子\EP7\subtitle_studio\剪好的音訊.history.json", filename: "剪好的音訊.history.json" },
            ],
          },
        }),
      ]}
    />,
  );
  const saved = document.querySelector(".export-saved")!;
  expect(saved.textContent).toContain("D:\案子\EP7\subtitle_studio");
  expect(saved.textContent).toContain("剪好的音訊.srt");
  expect(saved.textContent).toContain("剪好的音訊.history.json");
});

it("explains the wait for word alignment and warns when the SRT fell back to sentence timing", () => {
  const { rerender } = render(<ExportStatus jobs={[job({ status: "running", stage: "export.await_alignment" })]} />);
  expect(screen.getByRole("status").textContent).toContain("等逐詞對齊完成");
  rerender(<ExportStatus jobs={[job({ result: { artifacts: [{ artifact_id: "art1", kind: "srt" }], manifest: { warnings: ["alignment_unavailable_segment_timing"] } } })]} />);
  expect(document.querySelector(".export-warning")!.textContent).toContain("句子時間");
});

// M4-C2（2026-09-20）：匯出完成後顯示字幕品質檢查；沒問題就說檢查通過
it("shows the subtitle quality check after an export", () => {
  const job = {
    job_id: "e1", kind: "export", status: "succeeded",
    result: {
      artifacts: [{ artifact_id: "a1", kind: "srt" }], saved_files: [],
      manifest: { warnings: [], subtitle_quality: [{ entries: 49, overlaps: 1, short_display: 0, long_display: 2, fast_entries: 3, max_chars_per_sec: 18.2, adjacent_repeats: 1, missing_cues: 1, missing_cue_ids: ["c9"], warnings: {} }] },
    },
  };
  render(<ExportStatus jobs={[job as never]} />);
  const text = screen.getByText(/字幕檢查/).textContent || "";
  expect(text).toContain("49 則");
  expect(text).toContain("重疊 1");
  expect(text).toContain("太快 3");
  expect(text).toContain("過長 2");
  expect(text).toContain("相鄰重複 1");
  expect(text).toContain("疑似漏字 1");
  cleanup();
  const clean = { ...job, result: { ...job.result, manifest: { warnings: [], subtitle_quality: [{ entries: 20, overlaps: 0, short_display: 0, long_display: 0, fast_entries: 0, max_chars_per_sec: 8, adjacent_repeats: 0, missing_cues: 0, missing_cue_ids: [], warnings: {} }] } } };
  render(<ExportStatus jobs={[clean as never]} />);
  expect(screen.getByText(/字幕檢查：20 則，沒有發現問題/)).toBeTruthy();
});
