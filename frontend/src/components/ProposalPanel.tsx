import { useState } from "react";
import CollapsibleSection from "./CollapsibleSection";
import type { Job, Range } from "../api/types";
import { formatTimecode } from "../domain/time";
// 校字直接套用的紀錄：patches 為已寫入逐字稿的修正，before／after 為套用前後的逐字稿版本
export interface AppliedCorrection {
  patches: Correction[];
  before: string;
  after: string;
}
export interface Correction {
  cue_id: string;
  original_text: string;
  replacement_text: string;
  reason: string;
  base_revision?: string;
  rejection_reasons?: string[];
  confidence?: "high" | "low";
  glossary_term?: string;
}
const rejectionReasons: Record<string, string> = {
  stylistic_rewrite: "涉及潤飾或改寫",
  excessive_change: "修改幅度過大",
  length_expansion: "新增文字過多",
  length_reduction_requires_review: "刪減原文需人工確認",
  cross_cue_copy: "疑似把鄰句文字補入本句",
  no_change: "未提出實際修改",
  contradictory_reason: "理由與修改互相矛盾",
  possible_hallucination: "疑似辨識幻聽的字幕署名，請對照音訊決定刪除或保留",
};
export interface EditProposal {
  proposal_id?: string;
  items: {
    cue_ids: string[];
    name: string;
    reason: string;
    source_spans: Range[];
  }[];
  base_transcript_revision: string;
  base_sequence_revision: string;
  map_revision: string;
}
export default function ProposalPanel({
  jobs,
  acceptCorrection,
  acceptProposal,
  seek,
  run,
  timeBase = 0,
  applied = {},
  revertCorrection,
}: {
  jobs: Job[];
  acceptCorrection: (p: Correction, replacement?: string) => Promise<unknown>;
  acceptProposal: (p: EditProposal) => Promise<unknown>;
  seek: (us: number) => void;
  run: (fn: () => Promise<unknown>) => void;
  timeBase?: number;
  applied?: Record<string, AppliedCorrection>;
  revertCorrection?: (jobId: string) => Promise<unknown>;
}) {
  const [dismissed, setDismissed] = useState<string[]>([]);
  // 手改：卡片上的建議可先改再接受；過期：409／原文已變時在卡片上提示，不只丟到頂部橫幅
  const [editing, setEditing] = useState<string[]>([]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [stale, setStale] = useState<Record<string, string>>({});
  // 任務由新到舊：校字只看最新一次（舊提案的原文版本多半已過期），剪輯提案全部保留。
  const succeeded = jobs.filter((j) => j.status === "succeeded" && j.result);
  const latestCorrection = succeeded.find((j) => j.kind === "correct");
  const proposals = succeeded.filter((j) => j.kind === "plan_edits" || j === latestCorrection);
  const actionable = proposals.reduce((count, j) => {
    if (j.kind === "correct" && applied[j.job_id]) {
      const done = new Set(applied[j.job_id].patches.map((p) => p.cue_id));
      return count + ((j.result!.patches || []) as Correction[]).filter((p, i) => p.confidence === "low" && !done.has(p.cue_id) && !dismissed.includes(`${j.job_id}-${i}`)).length;
    }
    if (j.kind === "correct")
      return count + ((j.result!.patches || []) as Correction[]).filter((_, i) => !dismissed.includes(`${j.job_id}-${i}`)).length;
    return count + (dismissed.includes(j.job_id) ? 0 : 1);
  }, 0);
  // 單張校字卡片（未套用時逐條接受；已套用時只列低信心建議）
  const renderCard = (j: Job, p: Correction, i: number) => {
    const id = `${j.job_id}-${i}`;
    return dismissed.includes(id) ? null : (
      <article className="proposal" key={id}>
        <div className="proposal-diff">
          <div>
            <small>原文</small>
            <p>{p.original_text}</p>
          </div>
          <div>
            <small>建議</small>
            {editing.includes(id) ? (
              <textarea
                rows={2}
                value={edits[id] ?? p.replacement_text}
                onChange={(e) => setEdits((v) => ({ ...v, [id]: e.target.value }))}
              />
            ) : (
              <p>{edits[id] ?? p.replacement_text}</p>
            )}
          </div>
        </div>
        <p className="muted">
          {p.glossary_term ? `詞彙表：${p.glossary_term}。` : ""}
          {p.reason}
        </p>
        {stale[id] ? (
          <p className="stale-hint" role="status">
            {stale[id]}
          </p>
        ) : (
          <>
            <button
              onClick={() =>
                run(async () => {
                  const text = (edits[id] ?? p.replacement_text).trim();
                  if (!text) return;
                  try {
                    await acceptCorrection(p, text === p.replacement_text ? undefined : text);
                  } catch (e) {
                    const status = (e as { status?: number }).status;
                    const message = e instanceof Error ? e.message : String(e);
                    if (status === 409 || /過期|已變更/.test(message)) {
                      setStale((v) => ({ ...v, [id]: "校字提案已過期（逐字稿已更新），請重新產生校字" }));
                      return;
                    }
                    throw e;
                  }
                  setDismissed((v) => [...v, id]);
                })
              }
            >
              接受修改
            </button>
            <button
              onClick={() =>
                setEditing((v) => (v.includes(id) ? v.filter((x) => x !== id) : [...v, id]))
              }
            >
              {editing.includes(id) ? "收起手改" : "手改"}
            </button>
            <button onClick={() => setDismissed((v) => [...v, id])}>
              保留原文
            </button>
          </>
        )}
      </article>
    );
  };
  if (!proposals.length) return null;
  return (
    <CollapsibleSection
      id="proposals"
      // 全部都已直接套用時，這一區是新舊比對而不是待辦（使用者 2026-09-20）
      title={proposals.every((j) => j.kind !== "correct" || applied[j.job_id]) ? "校字結果（新舊比對）" : "待確認建議"}
      hint="原稿保留，可整批還原"
      badge={actionable || undefined}
      className="proposal-panel"
    >
      {proposals.map((j) => {
        const r = j.result!;
        const patches = (r.patches || []) as Correction[];
        if (j.kind === "correct" && applied[j.job_id]) {
          const record = applied[j.job_id];
          const rejected = (r.rejected_patches || []) as Correction[];
          const appliedIds = new Set(record.patches.map((p) => p.cue_id));
          const lowPatches = patches.map((p, i) => ({ p, i })).filter(({ p }) => p.confidence === "low" && !appliedIds.has(p.cue_id));
          return (
            <div key={j.job_id} className="applied-corrections">
              <p>
                已直接套用 {record.patches.length} 處修改
                {lowPatches.length ? `；${lowPatches.length} 處低信心建議未套用，列在下方由你決定` : ""}
                {rejected.length ? `；另有 ${rejected.length} 處建議未通過檢查` : ""}
                。逐字稿可逐句再改；不滿意可整批還原。
              </p>
              <ul className="applied-list">
                {record.patches.map((p) => (
                  <li key={p.cue_id}>
                    <span className="muted">{p.original_text}</span> → {p.replacement_text}
                    <small className="muted">（{p.reason}）</small>
                  </li>
                ))}
              </ul>
              <button onClick={() => run(() => (revertCorrection ? revertCorrection(j.job_id) : Promise.resolve()))}>
                全部還原到校字前
              </button>
              {lowPatches.map(({ p, i }) => renderCard(j, p, i))}
            </div>
          );
        }
        if (j.kind === "correct") {
          const rejected = (r.rejected_patches || []) as Correction[];
          const needsReview =
            r.validation_status === "rejected" ||
            r.quality_status === "needs_manual_review" ||
            rejected.length > 0;
          return (
            <div key={j.job_id}>
              <p className={needsReview ? "error-text" : "muted"}>
                {needsReview
                  ? "建議未通過檢查，原稿保留；需人工複核。"
                  : "語意品質尚未驗證，請對照音訊再採納建議。"}
              </p>
              {(((r.failed_chunks as { cue_count: number }[] | undefined) || []).length > 0 || Number(r.truncated_chunks || 0) > 0) && (
                <p className="error-text">
                  有 {((r.failed_chunks as { cue_count: number }[] | undefined) || []).length} 段模型輸出無法使用（
                  {((r.failed_chunks as { cue_count: number }[] | undefined) || []).reduce((n, c) => n + (c.cue_count || 0), 0)} 句未檢查）
                  {Number(r.truncated_chunks || 0) > 0 ? `，另有 ${r.truncated_chunks} 段輸出被截斷只取到部分建議` : ""}；可重試或換模型。
                </p>
              )}
              {!patches.length && (
                <p className="muted">沒有可採用的校字建議，請人工檢查原稿。</p>
              )}
              {(() => {
                const indexed = patches.map((p, i) => ({ p, i }));
                const low = indexed.filter(({ p }) => p.confidence === "low");
                return (
                  <>
                    {indexed.filter(({ p }) => p.confidence !== "low").map(({ p, i }) => renderCard(j, p, i))}
                    {low.length > 0 && (
                      <details className="rejected-suggestions low-confidence">
                        <summary>低信心建議（{low.length}）<span className="muted">依語意推測，請對照音訊再決定</span></summary>
                        {low.map(({ p, i }) => renderCard(j, p, i))}
                      </details>
                    )}
                  </>
                );
              })()}
              {rejected.length > 0 && (
                <details className="rejected-suggestions">
                  <summary>未採用的模型建議（{rejected.length}）<span className="muted">未通過保守檢查，原稿保留</span></summary>
                  {rejected.map((p, i) => (
                <article className="proposal" key={`rejected-${i}`}>
                  <strong>未採用的模型建議</strong>
                  <div className="proposal-diff">
                    <div>
                      <small>保留的原文</small>
                      <p>{p.original_text}</p>
                    </div>
                    <div>
                      <small>未通過檢查的改寫</small>
                      <p>{p.replacement_text}</p>
                    </div>
                  </div>
                  <ul>
                    {p.rejection_reasons?.map((reason) => (
                      <li key={reason}>
                        {rejectionReasons[reason] || "需要人工複核"}
                      </li>
                    ))}
                  </ul>
                </article>
                  ))}
                </details>
              )}
            </div>
          );
        }
        if (dismissed.includes(j.job_id) || !Array.isArray(r.items))
          return null;
        const p = r as unknown as EditProposal;
        return (
          <article className="proposal" key={j.job_id}>
            {p.items.map((item, i) => (
              <div key={i} className="proposal-item">
                <strong>
                  {i + 1}. {item.name}
                </strong>
                <p>{item.reason}</p>
                {item.source_spans.map((s, k) => (
                  <button
                    key={k}
                    className="text-button"
                    onClick={() => seek(s.start_us)}
                  >
                    {formatTimecode(s.start_us, timeBase)} → {formatTimecode(s.end_us, timeBase)}
                  </button>
                ))}
              </div>
            ))}
            <button
              onClick={() =>
                run(async () => {
                  await acceptProposal(p);
                  setDismissed((v) => [...v, j.job_id]);
                })
              }
            >
              採納為剪輯清單
            </button>
            <button onClick={() => setDismissed((v) => [...v, j.job_id])}>
              不採納
            </button>
          </article>
        );
      })}
    </CollapsibleSection>
  );
}
