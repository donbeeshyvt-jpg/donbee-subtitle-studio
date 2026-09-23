// 校字結果摘要（2026-09-20）：參考資料超過文字模型上下文預算一半時被截斷，摘要要講清楚送了多少字
import { expect, it } from "vitest";
import { correctionSummary } from "../src/domain/terms";

it("mentions a reference text that was cut to fit the text model", () => {
  const job = {
    job_id: "cj", kind: "correct", status: "succeeded",
    result: { patches: [], rejected_patches: [], ignored_no_change: 0, sent_cue_ids: [["a", "b"]], reference: { chars_total: 3000, chars_sent: 980, truncated: true } },
  };
  expect(correctionSummary(job)).toBe("校字完成：檢查 2 句，沒有找到需要修正的地方；參考資料太長，只送了前 980 字（共 3000 字）");
});

// 2026-09-20 使用者：走 API 要「整份送一次、收回結果再套用」，介面要看得出這次送了幾次
it("says whether the transcript went out in one request or in segments", () => {
  const base = { job_id: "cj", kind: "correct", status: "succeeded" };
  const once = { ...base, result: { patches: [], rejected_patches: [], ignored_no_change: 0, sent_cue_ids: [["a", "b"]], requests: 1, segmented: false } };
  expect(correctionSummary(once)).toBe("校字完成：檢查 2 句（整份一次送出），沒有找到需要修正的地方");
  const split = { ...base, result: { patches: [], rejected_patches: [], ignored_no_change: 0, sent_cue_ids: [["a"], ["b"]], requests: 2, segmented: true, context_window_cues: 6 } };
  expect(correctionSummary(split)).toBe("校字完成：檢查 2 句（分 2 段送出，每段附前後 6 句作參考），沒有找到需要修正的地方");
});
