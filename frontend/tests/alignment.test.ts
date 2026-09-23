// 主面板匯出要能用逐詞對齊：找出「目前逐字稿版本」對應的最新對齊；逐字稿改版（例如接受校字）後舊對齊不得沿用。
import { expect, it } from "vitest";
import { alignmentRunning, latestAlignment } from "../src/domain/alignment";
import type { Job } from "../src/api/types";

const job = (overrides: Partial<Job> & { body?: Record<string, unknown> }): Job =>
  ({ job_id: "j", kind: "align", status: "succeeded", ...overrides }) as Job;

it("returns the newest succeeded alignment bound to the current transcript revision", () => {
  const jobs = [
    job({ job_id: "new", body: { transcript_revision: "tr_2" }, result: { alignment_revision: "al_new" } }),
    job({ job_id: "old", body: { transcript_revision: "tr_2" }, result: { alignment_revision: "al_old" } }),
    job({ job_id: "other", body: { transcript_revision: "tr_1" }, result: { alignment_revision: "al_other" } }),
  ];
  expect(latestAlignment(jobs, "tr_2")).toBe("al_new");
  expect(latestAlignment(jobs, "tr_1")).toBe("al_other");
});

it("ignores failed or unrelated jobs and stale revisions after the transcript changes", () => {
  const jobs = [
    job({ status: "failed", body: { transcript_revision: "tr_3" } }),
    job({ kind: "export", body: { transcript_revision: "tr_3" }, result: { alignment_revision: "x" } }),
    job({ body: { transcript_revision: "tr_2" }, result: { alignment_revision: "al_2" } }),
  ];
  expect(latestAlignment(jobs, "tr_3")).toBeNull();
  expect(latestAlignment(jobs, undefined)).toBeNull();
});

it("reports a running alignment for the current revision", () => {
  expect(alignmentRunning([job({ status: "running", body: { transcript_revision: "tr_2" } })], "tr_2")).toBe(true);
  expect(alignmentRunning([job({ status: "queued", body: { transcript_revision: "tr_1" } })], "tr_2")).toBe(false);
});
