import type { Job } from "../api/types";

// 逐詞對齊綁定「某一個逐字稿版本」：逐字稿一改版（例如接受校字）舊對齊就不能沿用，必須重新對齊。
// jobs 由新到舊排列，所以第一個符合的就是最新的。
const boundTo = (job: Job, transcriptRevision: string) =>
  job.kind === "align" && (job.body as { transcript_revision?: string } | undefined)?.transcript_revision === transcriptRevision;

export function latestAlignment(jobs: Job[], transcriptRevision?: string | null): string | null {
  if (!transcriptRevision) return null;
  const job = jobs.find(
    (j) => boundTo(j, transcriptRevision) && j.status === "succeeded" && typeof j.result?.alignment_revision === "string",
  );
  return job ? (job.result!.alignment_revision as string) : null;
}

export function alignmentRunning(jobs: Job[], transcriptRevision?: string | null): boolean {
  if (!transcriptRevision) return false;
  return jobs.some((j) => boundTo(j, transcriptRevision) && ["queued", "running", "waiting"].includes(j.status));
}
