export interface Range {
  start_us: number;
  end_us: number;
}
export interface Segment {
  id: string;
  kind: "clip" | "marker";
  start_us: number;
  end_us?: number;
  name: string;
  selected: boolean;
  tags: Record<string, string>;
}
export interface Sequence {
  revision: string;
  source_id: string | null;
  items: Segment[];
}
export interface Project {
  project_id: string;
  name: string;
  sequence_revision?: string;
  transcript_revision?: string;
}
export interface Source {
  source_id: string;
  kind: string;
  title?: string;
  url?: string;
  duration_us?: number;
  asset_ids?: string[];
  transcript_revision?: string;
  // 依路徑匯入的本機檔：原始資料夾與預設輸出（同資料夾的 subtitle_studio）
  origin_dir?: string;
  default_output_root_id?: string | null;
}
export interface SourceMap {
  source_start_us: number;
  asset_start_us?: number;
  source_end_us?: number;
  status?: string;
}
export interface Asset {
  asset_id: string;
  source_id: string;
  duration_us: number;
  kind?: string;
  asset_kind?: string;
  source_map?: SourceMap | SourceMap[];
  content_url?: string;
}
export interface Cue {
  cue_id: string;
  start_us: number | null;
  end_us: number | null;
  text: string;
  language?: string;
  review_flags?: string[];
}
export interface Transcript {
  revision: string;
  source_id: string;
  cues: Cue[];
  alignment_revision?: string;
  map_revision?: string;
}
export interface Artifact {
  artifact_id: string;
  kind?: string;
  name?: string;
  download_url?: string;
  subtitle_origin?: string;
}
export interface Job {
  job_id: string;
  kind: string;
  status: string;
  stage?: string;
  progress?: number | { percent?: number };
  priority?: number;
  dispatch_paused?: boolean;
  error?: { code?: string; message?: string; details?: unknown };
  body?: Record<string, unknown>;
  result?: {
    asset_ids?: string[];
    transcript_revision?: string;
    alignment_revision?: string;
    artifacts?: Artifact[];
    [key: string]: unknown;
  };
}
export interface Annotation {
  id: string;
  kind?: string;
  method?: string;
  transcript_revision?: string;
  title?: string;
  text?: string;
  cue_ids: string[];
  spans?: Range[];
}
export interface OutputRoot {
  id: string;
  name: string;
  path?: string;
  // 檔案實際會放的資料夾：<path>/subtitle_studio（path 本身就叫 subtitle_studio 時就是 path）
  target?: string;
  kind?: "folder" | "sidecar";
}
export interface EditMeta {
  origin?: "manual" | "llm_correction" | "llm_revert" | "cli" | "import" | "other";
  job_id?: string;
  note?: string;
}
// 字幕預覽（POST /projects/{id}/subtitles/preview）：與匯出 SRT 同一段計算
export interface SubtitleEntry {
  index: number;
  start: string;
  end: string;
  start_us: number;
  end_us: number;
  source_start_us: number;
  source_end_us: number;
  text: string;
  origin_cue_ids: string[];
  warnings: string[];
}
export interface SubtitlePreview {
  quality?: import("../domain/terms").SubtitleQuality;
  transcript_revision: string;
  alignment: "word" | "pending" | "failed" | "not_applicable";
  alignment_revision: string | null;
  timebase: string;
  entries: SubtitleEntry[];
  srt: string;
  warnings: string[];
}
export interface SubtitleRequest {
  source_id: string;
  ranges: { start_us: number; end_us: number }[];
  transcript_revision: string;
  alignment_revision?: string;
  sentences_per_cue: number;
  keep_punctuation: boolean;
  subtitle_timebase: "sequence" | "source" | "clip";
  grouping: "merge";
}
// 即時字幕與翻譯（M7，/v1/realtime/sessions）
export interface RealtimeEvent {
  type: "line" | "tentative" | "translation" | "translation_error";
  id?: string;
  line_id?: string;
  line_ids?: string[];
  text?: string;
  start?: number;
  end?: number;
  code?: string;
  latency_sec?: number;
}
export interface RealtimeLine {
  id: string;
  start: number;
  end: number;
  text: string;
  translation?: string;
}
export interface RealtimeResult {
  events: RealtimeEvent[];
  lines: RealtimeLine[];
  srt: string;
  stats: Record<string, unknown>;
}
// 內容拆解單詞（POST /v1/text/keywords）的結果：method 標明是文字模型還是本機規則拆的
export interface KeywordsResult {
  keywords: string[];
  joined: string;
  method: "llm" | "rules";
  model?: string;
  warning?: string;
}
export interface Provider {
  id: string;
  model?: string;
  status?: string;
  local?: boolean;
  allow_remote?: boolean;
  secret_configured?: boolean;
}
export type ProbeStatusCode =
  | "ready"
  | "service_unreachable"
  | "secret_missing"
  | "auth_failed"
  | "model_not_loaded"
  | "rate_limited"
  | "structured_output_unsupported"
  | "credits_exhausted"
  | "invalid_config"
  | "provider_error";
export interface ProbeResult {
  model?: string;
  requested_model?: string;
  auto_model?: boolean;
  models?: string[];
  status: ProbeStatusCode;
  detail?: string | null;
  checked_at?: string;
  elapsed_ms?: number;
  model_available?: boolean | null;
  structured_output?: string;
}
export interface ProviderConfig {
  id: string;
  adapter: "openai_compatible" | "elevenlabs"; // elevenlabs：只做轉錄（2026-09-21）
  base_url: string;
  model: string;
  gpu_ownership: "external" | "managed" | "cpu";
  response_format_mode: "text" | "json_object" | "json_schema";
  reasoning_effort?: "none" | "low" | "medium" | "high" | null;
  timeout_sec: number;
  max_retries: 0 | 1;
  max_context_chars: number;
  max_output_tokens: number;
  allow_remote: boolean;
  api_key_env?: string | null;
  // 語音轉錄端口的模型（OpenRouter /audio/transcriptions），與語言模型分開；null＝不用轉錄端口
  transcription_model?: string | null;
  // 同一個來源登記的全部轉錄模型（主要的排第一）；精修模型清單每個各一個選項（2026-09-21）
  transcription_models?: string[] | null;
}
export interface ProviderInfo extends ProviderConfig {
  local: boolean;
  secret_configured: boolean;
  last_probe?: ProbeResult | null;
}
export type EnvironmentStatus = "ok" | "missing" | "outdated" | "unreachable";
export interface EnvironmentItem {
  label: string;
  status: EnvironmentStatus;
  version?: string | null;
  details?: Record<string, unknown>;
  guidance?: string;
}
export interface EnvironmentPackage {
  name: string;
  group: string;
  required: string | null;
  installed: string | null;
  status: "ok" | "missing" | "version_mismatch" | "optional_missing";
  optional?: boolean;
}
export type ModelItemStatus = "present" | "missing" | "size_mismatch" | "revision_mismatch" | "skipped_optional";
export interface ModelStatusItem {
  id: string;
  kind: "hf" | "torch" | "gguf" | "ct2";
  required: boolean;
  status: ModelItemStatus;
  bytes?: number;
  revision?: string | null;
  // ct2 類（Breeze-ASR 等）：顯示名稱、官方來源、下載量與授權
  label?: string | null;
  repo?: string;
  download_bytes?: number;
  license?: string | null;
}
// 精修可選的辨識模型（/capabilities 的 asr_models）
export interface AsrModelOption {
  key: string;
  label: string;
  installed: boolean;
  languages?: string[] | null;
  download_bytes?: number;
  license?: string | null;
  repo?: string;
  // 遠端轉錄（OpenRouter、ElevenLabs）：聲音會送出本機；missing＝secret（沒金鑰）或 remote_disabled（供應者沒開遠端）
  remote?: boolean;
  provider_label?: string; // 遠端服務名稱（OpenRouter／ElevenLabs）
  remote_model?: string; // 遠端轉錄用的模型名稱
  timestamps?: boolean;
  missing?: string | null;
}
export interface ModelDownloadState {
  status: "running" | "done" | "failed";
  ids: string[];
  events?: Array<{ id?: string; stage?: string; status?: string; bytes_done?: number; bytes_total?: number }>;
  error?: string | null;
}
export interface ModelStatus {
  items: ModelStatusItem[];
  required_missing: string[];
  offline_ready: boolean;
  download?: ModelDownloadState | null;
}
export interface EnvironmentReport {
  checked_at: string;
  items: Record<string, EnvironmentItem>;
  packages?: { lock: string | null; items: EnvironmentPackage[] };
  models?: { manifest: string | null; missing: string[]; optional_missing?: string[] };
  paths?: Record<string, string>;
  python_executable?: string;
}
export interface SubtitleSettings {
  sentences_per_cue: 1 | 2;
  preserve_punctuation: boolean;
}
export type AudioLabel = "speech" | "music" | "mixed" | "uncertain";
export interface ClassificationSpan {
  id: string;
  start_us: number;
  end_us: number;
  label: AudioLabel;
  model_label?: AudioLabel;
  speech_score?: number | null;
  music_score?: number | null;
  override_id?: string;
  requires_review?: boolean;
  reason?: string;
}
export interface Classification {
  id: string;
  classification_id?: string;
  asset_id: string;
  source_id: string;
  raw_spans: ClassificationSpan[];
  spans: ClassificationSpan[];
  settings?: { calibrated?: boolean; warnings?: string[] };
}
export type JobRequest = { kind: string; [key: string]: unknown };
