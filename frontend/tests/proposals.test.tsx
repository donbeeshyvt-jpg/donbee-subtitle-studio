import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import ProposalPanel from "../src/components/ProposalPanel";
import JobList from "../src/components/JobList";
import type { Job } from "../src/api/types";
afterEach(cleanup);
const patch = {
  cue_id: "cue",
  original_text: "原本的話",
  replacement_text: "不忠實的新句子",
  reason: "潤飾",
  rejection_reasons: ["excessive_change"],
};
const job = (result: Record<string, unknown>) =>
  ({ job_id: "j", kind: "correct", status: "succeeded", result }) as Job;
const props = {
  acceptCorrection: vi.fn(),
  acceptProposal: vi.fn(),
  seek: vi.fn(),
  run: vi.fn(),
};
it("keeps rejected rewrites visible without an acceptance action", () => {
  render(
    <ProposalPanel
      {...props}
      jobs={[
        job({
          patches: [],
          rejected_patches: [patch],
          validation_status: "rejected",
          quality_status: "needs_manual_review",
        }),
      ]}
    />,
  );
  expect(screen.getByText("原本的話")).toBeTruthy();
  expect(screen.getByText("不忠實的新句子")).toBeTruthy();
  expect(screen.getByText("修改幅度過大")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "接受修改" })).toBeNull();
  expect(screen.getByText(/建議未通過檢查/)).toBeTruthy();
});
it("does not equate empty suggestions to a verified transcript", () => {
  render(
    <ProposalPanel
      {...props}
      jobs={[job({ patches: [], quality_status: "unverified" })]}
    />,
  );
  expect(screen.getByText(/沒有可採用的校字建議/)).toBeTruthy();
  expect(screen.getByText(/語意品質尚未驗證/)).toBeTruthy();
});
it("allows manual adoption of valid candidates with unverified quality", () => {
  render(
    <ProposalPanel
      {...props}
      jobs={[job({ patches: [patch], quality_status: "unverified" })]}
    />,
  );
  expect(screen.getByRole("button", { name: "接受修改" })).toBeTruthy();
  expect(screen.getByText(/語意品質尚未驗證/)).toBeTruthy();
});
it("distinguishes completed inference from correction quality in the queue", () => {
  render(
    <JobList
      run={vi.fn()}
      jobs={[
        job({
          patches: [],
          validation_status: "rejected",
          quality_status: "needs_manual_review",
        }),
      ]}
    />,
  );
  expect(screen.getByText("已完成，需人工複核")).toBeTruthy();
});

it("shows only the newest correction job and folds rejected suggestions away", () => {
  const usable = { ...patch, original_text: "新的原文", replacement_text: "新的建議", rejection_reasons: undefined };
  const newest = { job_id: "new", kind: "correct", status: "succeeded",
    result: { patches: [usable], rejected_patches: [patch, patch], quality_status: "needs_manual_review" } } as unknown as Job;
  const older = { job_id: "old", kind: "correct", status: "succeeded",
    result: { patches: [{ ...usable, original_text: "舊的原文", replacement_text: "舊的建議" }] } } as unknown as Job;
  render(<ProposalPanel {...props} jobs={[newest, older]} />);
  expect(screen.getByText("新的建議")).toBeTruthy();
  expect(screen.queryByText("舊的建議")).toBeNull();
  const folded = screen.getByText(/未採用的模型建議（2）/).closest("details")!;
  expect(folded.open).toBe(false);
  expect(screen.getByRole("button", { name: /待確認建議/ }).getAttribute("aria-expanded")).toBe("true");
});
it("warns when some chunks of the transcript could not be checked", () => {
  render(
    <ProposalPanel
      {...props}
      jobs={[job({ patches: [], failed_chunks: [{ chunk_index: 6, cue_count: 40 }], truncated_chunks: 1, quality_status: "needs_manual_review" })]}
    />,
  );
  expect(screen.getByText(/1 段模型輸出無法使用/)).toBeTruthy();
  expect(screen.getByText(/40 句未檢查/)).toBeTruthy();
});

it("lists high-confidence corrections first and folds low-confidence suggestions", () => {
  const high = { cue_id: "a", original_text: "彈步遊戲", replacement_text: "彈幕遊戲", reason: "同音錯字", confidence: "high" };
  const low = { cue_id: "b", original_text: "你不要撞下去", replacement_text: "你不要掉下去", reason: "語意推測", confidence: "low" };
  render(<ProposalPanel {...props} jobs={[job({ patches: [low, high], quality_status: "unverified" })]} />);
  const folded = screen.getByText(/低信心建議（1）/).closest("details")!;
  expect(folded.open).toBe(false);
  expect(folded.textContent).toContain("你不要掉下去");
  expect(folded.textContent).not.toContain("彈幕遊戲");
  expect(screen.getAllByRole("button", { name: "接受修改" })).toHaveLength(2);
});

// ---- M3-5：提案採納前可手改；409／過期在卡片上以中文提示 ----
const usable = { ...patch, replacement_text: "彈幕遊戲好難", reason: "同音錯字", rejection_reasons: undefined, confidence: "high" };
const realRun = () =>
  vi.fn(async (fn: () => Promise<unknown>) => {
    try {
      await fn();
    } catch {
      /* App 會把未處理的錯誤顯示在橫幅；卡片層級的提示由 ProposalPanel 自己處理 */
    }
  });
it("lets the user hand-edit a suggestion before accepting it", async () => {
  const acceptCorrection = vi.fn().mockResolvedValue(undefined);
  render(
    <ProposalPanel
      {...props}
      acceptCorrection={acceptCorrection}
      run={realRun()}
      jobs={[job({ patches: [usable], quality_status: "unverified" })]}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "手改" }));
  const box = screen.getByRole("textbox") as HTMLTextAreaElement;
  expect(box.value).toBe("彈幕遊戲好難");
  fireEvent.change(box, { target: { value: "彈幕射擊遊戲好難" } });
  fireEvent.click(screen.getByRole("button", { name: "接受修改" }));
  await waitFor(() => expect(acceptCorrection).toHaveBeenCalledTimes(1));
  expect(acceptCorrection.mock.calls[0][1]).toBe("彈幕射擊遊戲好難");
  expect(screen.queryByRole("button", { name: "接受修改" })).toBeNull();
});
it("shows a Chinese stale hint on the card when acceptance hits a revision conflict", async () => {
  const { ApiError } = await import("../src/api/client");
  const acceptCorrection = vi.fn().mockRejectedValue(new ApiError(409, "REVISION_CONFLICT", "請提供目前文字版本"));
  render(
    <ProposalPanel
      {...props}
      acceptCorrection={acceptCorrection}
      run={realRun()}
      jobs={[job({ patches: [usable], quality_status: "unverified" })]}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "接受修改" }));
  await waitFor(() => expect(screen.getByText(/校字提案已過期/)).toBeTruthy());
  expect(screen.getByText(/重新產生/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "接受修改" })).toBeNull();
});
