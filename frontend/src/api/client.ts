import type {
  Project,
  Source,
  Sequence,
  Job,
  JobRequest,
  Asset,
  Transcript,
  Annotation,
  Provider,
  KeywordsResult,
  SubtitlePreview,
  SubtitleRequest,
  RealtimeEvent,
  RealtimeResult,
  EnvironmentReport,
  ModelStatus,
  ProviderInfo,
  ProviderConfig,
  ProbeResult,
  OutputRoot,
  EditMeta,
} from "./types";
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}
let session: Promise<void> | null = null;
async function ensureSession() {
  if (!session)
    session = fetch("/v1/session", { credentials: "same-origin" })
      .then(async (r) => {
        if (!r.ok)
          throw new ApiError(
            r.status,
            "SESSION_FAILED",
            "無法建立本機服務連線，請確認服務已啟動。",
          );
      })
      .catch((e) => {
        session = null;
        throw e;
      });
  return session;
}
// 工作階段 cookie 12 小時有效。頁面開著超過 12 小時（或服務換過金鑰）時請求會被拒（401）：
// 重新建立工作階段後把同一個請求重送一次；仍被拒才回報，請使用者重新整理。
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  await ensureSession();
  const headers = new Headers(options.headers);
  // JSON 字串才標 JSON；表單與音訊（ArrayBuffer）用各自的類型
  if (typeof options.body === "string" && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  const send = () =>
    fetch(`/v1${path}`, {
      ...options,
      headers,
      credentials: "same-origin",
    });
  let response = await send();
  if (response.status === 401) {
    session = null;
    await ensureSession();
    response = await send();
    if (response.status === 401)
      throw new ApiError(401, "SESSION_EXPIRED", "與本機服務的連線已失效，請重新整理頁面（F5）");
  }
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const e = payload?.error || payload?.detail || payload;
    throw new ApiError(
      response.status,
      e?.code || "REQUEST_FAILED",
      e?.message ||
        (typeof e === "string" ? e : `請求失敗 (${response.status})`),
    );
  }
  return payload as T;
}
const post = <T>(path: string, body: unknown = {}) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });
type RawTranscript = Omit<Transcript, "cues"> & {
  cues: Array<
    Transcript["cues"][number] & {
      id: string;
      accepted_text?: string;
      raw_text?: string;
    }
  >;
  next_cursor?: number | null;
};
const normalizeTranscript = (t: RawTranscript): Transcript => ({
  ...t,
  cues: t.cues.map((c) => ({
    ...c,
    cue_id: c.cue_id || c.id,
    text: c.accepted_text ?? c.text ?? c.raw_text ?? "",
  })),
});
async function loadTranscript(p: string, r: string) {
  let cursor: number | null = 0;
  let result: RawTranscript | undefined;
  while (cursor != null) {
    const page: RawTranscript = await request<RawTranscript>(
      `/projects/${p}/transcripts/${r}?limit=200&cursor=${cursor}`,
    );
    result = result
      ? { ...result, cues: [...result.cues, ...page.cues] }
      : page;
    cursor = page.next_cursor ?? null;
  }
  return normalizeTranscript(result!);
}
async function listAll<T>(path: string): Promise<{ items: T[] }> {
  let cursor: number | null = 0;
  const items: T[] = [];
  while (cursor != null) {
    const page: { items: T[]; next_cursor?: number | null } = await request<{
      items: T[];
      next_cursor?: number | null;
    }>(`${path}?limit=200&cursor=${cursor}`);
    items.push(...page.items);
    cursor = page.next_cursor ?? null;
  }
  return { items };
}
export const api = {
  health: () => request<{ status: string }>("/health"),
  projects: () => request<{ items: Project[] }>("/projects"),
  createProject: (name: string) => post<Project>("/projects", { name }),
  // 刪除整個專案（資料、工作、成果檔）：不可復原，呼叫端一定要先二次確認
  deleteProject: (p: string) =>
    request<{ project_id: string; removed_entities: number; removed_jobs: number; removed_artifacts: number; freed_bytes: number }>(
      `/projects/${p}`,
      { method: "DELETE" },
    ),
  sources: (p: string) => listAll<Source>(`/projects/${p}/sources`),
  source: (p: string, url: string) =>
    post<Source>(`/projects/${p}/sources`, { kind: "youtube", url }),
  upload: (p: string, file: File) => {
    const f = new FormData();
    f.set("file", file);
    return request<{
      source_id: string;
      asset_id?: string;
      source?: Source;
      asset?: Asset;
    }>(`/projects/${p}/uploads`, { method: "POST", body: f });
  },
  sequence: (p: string) => request<Sequence>(`/projects/${p}/sequence`),
  save: (p: string, seq: Sequence) =>
    request<Sequence>(`/projects/${p}/sequence`, {
      method: "PUT",
      body: JSON.stringify({
        base_revision: seq.revision,
        source_id: seq.source_id,
        items: seq.items,
      }),
    }),
  jobs: (p: string) => request<{ items: Job[] }>(`/projects/${p}/jobs`),
  job: (p: string, body: JobRequest) => post<Job>(`/projects/${p}/jobs`, body),
  cancel: (j: string) => post<Job>(`/jobs/${j}/cancel`),
  retry: (j: string) => post<Job>(`/jobs/${j}/retry`),
  control: (
    j: string,
    body: { priority?: number; dispatch_paused?: boolean },
  ) =>
    request<Job>(`/jobs/${j}/control`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  asset: async (id: string) => {
    const a = await request<Asset & { id: string }>(`/assets/${id}`);
    return { ...a, asset_id: a.asset_id || a.id };
  },
  peaks: (id: string, start: number, end: number) =>
    request<{ peaks: number[]; start_us?: number; end_us?: number }>(
      `/assets/${id}/peaks?start_us=${start}&end_us=${end}&level=1024`,
    ),
  transcript: loadTranscript,
  edits: async (
    p: string,
    r: string,
    edits: { cue_id: string; text: string }[],
    meta: EditMeta = {},
  ) =>
    normalizeTranscript(
      await post<RawTranscript>(`/projects/${p}/transcripts/${r}/edits`, {
        base_revision: r,
        edits,
        ...meta, // origin／job_id／note：每一步修改都留紀錄，history 可整段匯出
      }),
    ),
  transcriptHistory: (p: string, r: string) => request<Record<string, unknown>>(`/projects/${p}/transcripts/${r}/history`),
  // 原生視窗（後端開）：選資料夾／選檔；瀏覽器拿不到本機絕對路徑，所以由本機服務代開
  pickFolder: (title = "") => post<{ path: string | null; cancelled: boolean }>("/dialogs/folder", { title }),
  pickFile: (title = "") => post<{ path: string | null; cancelled: boolean }>("/dialogs/file", { title }),
  importLocal: (p: string, path: string) =>
    post<{ source_id: string; job_id?: string; asset_id?: string; default_output_root_id?: string | null }>(`/projects/${p}/sources/local-file`, { path }),
  addOutputRoot: (path: string, name: string, create: boolean) =>
    post<{ items: OutputRoot[]; id: string }>("/output-roots", { path, ...(name ? { name } : {}), create }),
  annotations: async (p: string) => {
    const r = await listAll<
      Annotation & { body?: string; source_spans?: Annotation["spans"] }
    >(`/projects/${p}/annotations`);
    return {
      items: r.items.map((a) => ({
        ...a,
        text: a.text ?? a.body,
        spans: a.spans ?? a.source_spans,
      })),
    };
  },
  providers: () => request<{ items: Provider[] }>("/providers"),
  probe: (p: string) => post(`/providers/${p}/probe`),
  environment: () => request<EnvironmentReport>("/environment"),
  recheckEnvironment: () => post<EnvironmentReport>("/environment/recheck"),
  modelStatus: () => request<ModelStatus>("/models/status"),
  downloadModels: (ids: string[]) => post("/models/download", { ids, confirm: true }),
  providerList: () => request<{ items: ProviderInfo[] }>("/providers"),
  providerSave: (config: ProviderConfig, isNew: boolean) =>
    isNew
      ? post("/providers", config)
      : request(`/providers/${config.id}`, { method: "PUT", body: JSON.stringify(config) }),
  providerRemove: (id: string) => request(`/providers/${id}`, { method: "DELETE" }),
  providerSecretSet: (id: string, secret: string) =>
    request(`/providers/${id}/secret`, { method: "PUT", body: JSON.stringify({ secret }) }),
  providerSecretDelete: (id: string) => request(`/providers/${id}/secret`, { method: "DELETE" }),
  probeProvider: (id: string) => post<ProbeResult>(`/providers/${id}/probe`),
  // 內容拆解單詞（2026-09-20）：用目前的文字模型把內容整理成關鍵詞；模型不可用時後端退回本機規則（method 標明）
  // 即時字幕與翻譯（M7，2026-09-20）：建立工作階段 → 每段 PCM16 16 kHz 音訊 → 結束取回全部字幕與 SRT
  realtimeCreate: (body: { model: string; language: string; translate_to?: string; provider_id?: string; remote_consent: boolean; hints?: string }) =>
    post<{ session_id: string; sample_rate: number; model: string }>("/realtime/sessions", body),
  realtimeAudio: (sid: string, data: ArrayBuffer) =>
    request<{ events: RealtimeEvent[]; received_sec: number }>(`/realtime/sessions/${sid}/audio`, {
      method: "POST",
      body: data,
      headers: { "Content-Type": "application/octet-stream" },
    }),
  realtimeFinish: (sid: string) => post<RealtimeResult>(`/realtime/sessions/${sid}/finish`),
  realtimeClose: (sid: string) => request<null>(`/realtime/sessions/${sid}`, { method: "DELETE" }),
  // 字幕預覽（2026-09-20）：與「匯出 SRT」同一段計算，回傳每一則的 SRT 時間與文字
  subtitlePreview: (p: string, body: SubtitleRequest) => post<SubtitlePreview>(`/projects/${p}/subtitles/preview`, body),
  keywords: (body: { text: string; provider_id: string | null; remote_consent: boolean }) =>
    post<KeywordsResult>("/text/keywords", body),
};
