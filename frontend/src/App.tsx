import type { AsrModelOption, Classification } from "./api/types";
import AnalysisOptions, {
  defaultAnalysisPolicies,
} from "./components/AnalysisOptions";
import { useJobEvents } from "./hooks/useJobEvents";
import { ACTIVE_JOB_STATUSES, createPoller, pollDelay } from "./domain/polling";
import ProposalPanel, { type AppliedCorrection, type Correction } from "./components/ProposalPanel";
import EnvironmentPanel from "./components/EnvironmentPanel";
import ProviderPanel from "./components/ProviderPanel";
import { request } from "./api/client";
import { useCallback, useEffect, useMemo, useRef, useState, type ComponentProps } from "react";
import {
  Plus,
  Trash2,
  Upload,
  Scissors,
  Undo2,
  Redo2,
  Save,
  Flag,
  Film,
  Music2,
  Download,
  Settings2,
} from "lucide-react";
import { api, ApiError } from "./api/client";
import { keep } from "./domain/same";
import { alignmentRunning, latestAlignment } from "./domain/alignment";
import CollapsibleSection from "./components/CollapsibleSection";
import type {
  Project,
  Source,
  Asset,
  Job,
  Sequence,
  Transcript,
  Annotation,
  Provider,
  Segment,
  SubtitleSettings,
} from "./api/types";
import { useSegments } from "./hooks/useSegments";
import {
  createSegment,
  withoutAutoFull,
  moveSegment,
  splitSegment,
  updateBoundary,
} from "./domain/segments";
import { formatTimecode, parseRanges } from "./domain/time";
import Timeline from "./components/Timeline";
import SegmentList from "./components/SegmentList";
import ExportStatus from "./components/ExportStatus";
import OutputRootPicker from "./components/OutputRootPicker";
import { downloadJson } from "./domain/download";
import { isDialogUnavailable } from "./domain/errors";
import TranscriptPanel from "./components/TranscriptPanel";
import JobList from "./components/JobList";
import TermsField from "./components/TermsField";
import TimecodeInput from "./components/TimecodeInput";
import RealtimePanel, { browserSource } from "./components/RealtimePanel";
import type { SubtitlePreview, SubtitleRequest } from "./api/types";
import { correctionSummary, joinTerms, mergeTerms, splitTerms } from "./domain/terms";
// 校字用詞彙提示：逗號、頓號、分號、換行或空格分隔（domain/terms.splitTerms，與後端 terms.split_terms 相同），去重、最多 100 個、每個最多 80 字。
export function glossaryTerms(text: string): string[] {
  return splitTerms(text);
}
// 文字模型的三個來源（2026-09-20 使用者：「只需要選 lm 跟 llamacpp 還有 openrouter」）
const MODEL_PORTS = [
  { id: "local-lmstudio", label: "LM Studio（本機，跟著載入的模型）" },
  { id: "local-llamacpp", label: "llama.cpp（本機，跟著載入的模型）" },
  { id: "api-openrouter", label: "OpenRouter（遠端 API，需金鑰）" },
];
const AUDIT_WINDOW_US = 500000; // 入出點抽查：前後各 0.5 秒
const REFERENCE_MAX = 6000; // 校字參考資料上限（與後端 JobRequest.reference_text 一致）
const ASR_HINTS_MAX = 2000; // 轉錄術語提示上限（與後端 JobRequest.asr_hints 一致）
// 轉錄術語提示送出前整理成「詞, 詞」：超過上限就少送幾個完整的詞，不把詞切一半
function asrHintsPayload(text: string): string {
  const kept: string[] = [];
  for (const term of splitTerms(text)) {
    if (joinTerms([...kept, term]).length > ASR_HINTS_MAX) break;
    kept.push(term);
  }
  return joinTerms(kept);
}

export default function App() {
  const [classifications, setClassifications] = useState<Classification[]>([]);
  const [analysisPolicies, setAnalysisPolicies] = useState(
    defaultAnalysisPolicies,
  );
  const [sourceSubtitlesEnabled, setSourceSubtitlesEnabled] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]),
    [project, setProject] = useState(""),
    [projectName, setProjectName] = useState("未命名專案"),
    [sources, setSources] = useState<Source[]>([]),
    [sourceId, setSourceId] = useState(""),
    [assets, setAssets] = useState<Asset[]>([]),
    [asset, setAsset] = useState<Asset | null>(null),
    [jobs, setJobs] = useState<Job[]>([]),
    [sequence, setSequence] = useState<Sequence>({
      revision: "seq_00",
      source_id: null,
      items: [],
    }),
    [transcript, setTranscript] = useState<Transcript | null>(null),
    [annotations, setAnnotations] = useState<Annotation[]>([]),
    [providers, setProviders] = useState<Provider[]>([]),
    [provider, setProviderState] = useState(() => {
      try {
        return localStorage.getItem("dongbi.provider") || "";
      } catch {
        return "";
      }
    }),
    // 遠端供應者需明示同意才列入可選；記在瀏覽器本機，不上傳
    [remoteConsent, setRemoteConsentState] = useState<boolean>(() => {
      try {
        return localStorage.getItem("dongbi.remote_consent") === "1";
      } catch {
        return false;
      }
    }),
    [connected, setConnected] = useState(false),
    [error, setError] = useState(""),
    [conflict, setConflict] = useState(false),
    [saveFailed, setSaveFailed] = useState(false),
    [outputRoots, setOutputRoots] = useState<{ id: string; name: string; path?: string }[]>(
      [],
    ),
    [busy, setBusy] = useState(false),
    [dirty, setDirty] = useState(false),
    [active, setActive] = useState(""),
    [time, setTime] = useState(0),
    [duration, setDuration] = useState(0),
    // 入出點：預設未設定；兩者都設且出點在入點後才算有效範圍（時間軸畫範圍帶、才能加入片段）
    [inPoint, setInPoint] = useState<number | null>(null),
    [outPoint, setOutPoint] = useState<number | null>(null),
    [showSettings, setShowSettings] = useState(false);
  const [intent, setIntent] = useState("挑出三個重點，總長約三分鐘"),
    [profile, setProfile] = useState("quality"); // 使用者：WhisperX large-v3 精修是主要路徑
  // 分頁：第 1 頁下載與剪輯、第 2 頁字幕與校字；兩頁共用同一份素材與播放器
  const [page, setPageState] = useState<"edit" | "subtitles">(() => {
    try {
      return localStorage.getItem("dongbi.page") === "subtitles" ? "subtitles" : "edit";
    } catch {
      return "edit";
    }
  });
  const setPage = (value: "edit" | "subtitles") => {
    setPageState(value);
    try {
      localStorage.setItem("dongbi.page", value);
    } catch {
      /* 只保留在記憶體 */
    }
  };
  const [playing, setPlaying] = useState(false);
  // 播放到出點自動暫停：記住上一個播放位置，只在「自然播放經過出點」時觸發（大跳＝使用者拖動，不算）
  const lastPlayhead = useRef<number | null>(null);
  // 摘要完成後把逐字稿面板切到「摘要與重點」；nonce 讓同一分頁可重複要求
  const [tabRequest, setTabRequest] = useState<{ tab: string; nonce: number } | null>(null);
  const seenSummaries = useRef<Set<string>>(new Set());
  const alignRequested = useRef<Set<string>>(new Set());
  // 校字直接套用：送出的校字工作 id → 完成後自動把守門通過的修正寫進逐字稿；applied 記錄可還原的內容
  const [autoApply, setAutoApplyState] = useState(() => {
    try {
      return localStorage.getItem("dongbi.correct.autoApply") !== "0";
    } catch {
      return true;
    }
  });
  const setAutoApply = (value: boolean) => {
    setAutoApplyState(value);
    try {
      localStorage.setItem("dongbi.correct.autoApply", value ? "1" : "0");
    } catch {
      /* 只保留在記憶體 */
    }
  };
  const [pendingApply, setPendingApply] = useState<string[]>([]);
  const [applied, setApplied] = useState<Record<string, AppliedCorrection>>({});
  const applying = useRef<Set<string>>(new Set());
  const [engine, setEngine] = useState<"whisperx" | "vibevoice">("whisperx");
  // 精修那一趟的模型（2026-09-19）：預設跟著服務（2026-09-20 起已安裝 Qwen3-ASR-1.7B 就用它，否則 large-v3）；
  // 使用者選過就記在瀏覽器；選的模型沒安裝時退回預設
  const [asrModels, setAsrModels] = useState<AsrModelOption[]>([]);
  const [defaultAsrModel, setDefaultAsrModel] = useState("large-v3");
  const [asrModel, setAsrModelState] = useState(() => {
    try {
      return localStorage.getItem("dongbi.asrModel") || "";
    } catch {
      return "";
    }
  });
  // 遠端轉錄（OpenRouter、ElevenLabs）另外要使用者同意使用遠端服務（聲音會送出本機）
  const asrModelReady = (key: string) =>
    key === "large-v3" || asrModels.some((m) => m.key === key && m.installed && (!m.remote || remoteConsent));
  const asrModelNote = (m: AsrModelOption) =>
    m.remote
      ? !m.installed
        ? m.missing === "remote_disabled"
          ? "（未開啟遠端服務：模型設定→文字模型供應者）"
          : "（未設定金鑰：模型設定→文字模型供應者）"
        : !remoteConsent
          ? "（需先在模型設定同意使用遠端服務）"
          : `（遠端：音訊會送到 ${m.provider_label ?? "OpenRouter"}）`
      : m.installed
        ? ""
        : `（未安裝：模型設定→環境與模型，下載約 ${((m.download_bytes ?? 0) / 1e9).toFixed(1)} GB）`;
  const asrModelDefault = asrModelReady(defaultAsrModel) ? defaultAsrModel : "large-v3";
  const effectiveAsrModel = asrModel && asrModelReady(asrModel) ? asrModel : asrModelDefault;
  const asrModelName = (key?: unknown) => {
    if (typeof key !== "string" || key === "large-v3") return "large-v3";
    const found = asrModels.find((m) => m.key === key);
    // 遠端轉錄同一個服務可能有好幾個模型：顯示「服務 模型名稱」才分得出是哪一個
    if (found?.remote && found.remote_model) return `${found.provider_label ?? "OpenRouter"} ${found.remote_model}`;
    return found?.label.split("（")[0] ?? key;
  };
  const setAsrModel = (value: string) => {
    setAsrModelState(value);
    try {
      localStorage.setItem("dongbi.asrModel", value);
    } catch {
      /* 只保留在記憶體 */
    }
  };
  // 校字與摘要用的文字模型：選擇記在瀏覽器；從未選過時自動選本機 LM Studio（不需同意遠端）。
  const setProvider = (id: string) => {
    setProviderState(id);
    setProbeText("");  // 換來源就清掉上一次的測試結果
    try {
      localStorage.setItem("dongbi.provider", id);
    } catch {
      /* 只保留在記憶體 */
    }
  };
  // 詞彙提示（校字）與轉錄術語提示：每個專案各自一份（2026-09-21 真瀏覽器：換一支影片開新專案，上一支的人名還在，
  // 會送進新影片的轉錄與校字）。舊版存的全域值不再帶進任何專案，讀到就清掉。
  const [glossaryText, setGlossaryTextState] = useState("");
  const [asrHints, setAsrHintsState] = useState("");
  useEffect(() => {
    try {
      localStorage.removeItem("dongbi.glossary");
      localStorage.removeItem("dongbi.asrHints");
      setGlossaryTextState(project ? localStorage.getItem(`dongbi.glossary.${project}`) || "" : "");
      setAsrHintsState(project ? localStorage.getItem(`dongbi.asrHints.${project}`) || "" : "");
    } catch {
      setGlossaryTextState("");
      setAsrHintsState("");
    }
  }, [project]);
  const setGlossaryText = (value: string) => {
    setGlossaryTextState(value);
    try {
      if (project) localStorage.setItem(`dongbi.glossary.${project}`, value);
    } catch {
      /* 只保留在記憶體 */
    }
  };
  // 校字參考資料（故事大綱、角色表）：每個專案各自一份，換專案不會把上一集的大綱送出去
  const [referenceText, setReferenceTextState] = useState("");
  useEffect(() => {
    try {
      setReferenceTextState(project ? localStorage.getItem(`dongbi.reference.${project}`) || "" : "");
    } catch {
      setReferenceTextState("");
    }
  }, [project]);
  const setReferenceText = (value: string) => {
    setReferenceTextState(value);
    try {
      if (project) localStorage.setItem(`dongbi.reference.${project}`, value);
    } catch {
      /* 只保留在記憶體 */
    }
  };
  // 轉錄術語提示：送給轉錄模型（Whisper hotwords、Qwen context），與校字詞彙分開；每個專案一份（見上）
  const setAsrHints = (value: string) => {
    setAsrHintsState(value);
    try {
      if (project) localStorage.setItem(`dongbi.asrHints.${project}`, value);
    } catch {
      /* 只保留在記憶體 */
    }
  };
  const [keywordStatus, setKeywordStatus] = useState("");
  const [correctRequested, setCorrectRequested] = useState(false);
  // 文字模型只給三個來源（2026-09-20 使用者）：本機兩個跟著當前載入的模型，遠端看後台的金鑰與模型設定
  const [probeText, setProbeText] = useState("");
  // 刪除專案後的提示（顯示在儲存狀態旁，換專案就清掉）
  const [projectNote, setProjectNote] = useState("");
  const [probing, setProbing] = useState(false);
  // 校字強度（2026-09-20 使用者：「我要的是一上下文處理逐字稿就會每一句套上」）
  const [correctionMode, setCorrectionModeState] = useState<"conservative" | "rewrite">(() => {
    try {
      return localStorage.getItem("dongbi.correctionMode") === "rewrite" ? "rewrite" : "conservative";
    } catch {
      return "conservative";
    }
  });
  const setCorrectionMode = (value: "conservative" | "rewrite") => {
    setCorrectionModeState(value);
    try {
      localStorage.setItem("dongbi.correctionMode", value);
    } catch {
      /* 只保留在記憶體 */
    }
  };
  const [splittingKeywords, setSplittingKeywords] = useState(false);
  const [url, setUrl] = useState(""),
    [rangeText, setRangeText] = useState(""),
    [assetKind, setAssetKind] = useState("audio"),
    [container, setContainer] = useState("source"),
    [height, setHeight] = useState(""),
    [bitrate, setBitrate] = useState(""),
    [fps, setFps] = useState(""),
    [rootId, setRootId] = useState(""), // 下載儲存位置
    [exportRootId, setExportRootId] = useState(""), // 影音與字幕輸出儲存位置
    [attachSrt, setAttachSrt] = useState(false), // 第 1 頁影音輸出是否附上對應剪輯的 SRT
    [autoOutput, setAutoOutputState] = useState(() => {
      try {
        return localStorage.getItem("dongbi.autoOutput") !== "0";
      } catch {
        return true;
      }
    }),
    [analyze, setAnalyze] = useState(true),
    [grouping, setGrouping] = useState("separate"),
    [cutMode, setCutMode] = useState("accurate"),
    [exportMedia, setExportMedia] = useState(true),
    [mediaFormat, setMediaFormat] = useState("mp4"),
    [subtitleSettings, setSubtitleSettings] = useState<SubtitleSettings>({
      sentences_per_cue: 1,
      preserve_punctuation: false,
    });
  const editor = useSegments();
  const player = useRef<HTMLVideoElement>(null);
  const auditTimer = useRef<number | undefined>(undefined);
  // 即時字幕的音訊來源（麥克風，或擷取這個播放器正在播的聲音）
  const realtimeSource = useMemo(() => browserSource(() => player.current), []);
  const inputFile = useRef<HTMLInputElement>(null);
  const importFile = useRef<HTMLInputElement>(null);
  const loadedRevision = useRef("");
  // 專案清單的「版本」：新建／刪除／切換專案時加一。重抓清單的回應若是在這之前送出的，就是舊資料，不採用
  // （2026-09-21 真瀏覽器：按「新專案」同時視窗取得焦點 → 舊清單晚回來，把剛建好的專案當成被刪掉）
  const projectEpoch = useRef(0);
  const latestItems = useRef(editor.items);
  latestItems.current = editor.items;
  const currentProject = useRef(project);
  currentProject.current = project;
  const currentSourceId = useRef(sourceId);
  currentSourceId.current = sourceId;
  // 輪詢用：判斷能否自動載入後端改寫的剪輯清單（有本機修改、儲存中或衝突中都不能動）
  const checkedWorkflows = useRef("");
  const editState = useRef({ dirty, busy, conflict, saveFailed, revision: sequence.revision });
  editState.current = { dirty, busy, conflict, saveFailed, revision: sequence.revision };
  const currentSource = sources.find((s) => s.source_id === sourceId);
  // 輸出位置：使用者選過就用選的；沒選過時，依路徑匯入的素材預設輸出到匯入檔旁的 subtitle_studio
  const effectiveExportRoot = exportRootId || currentSource?.default_output_root_id || "";
  const setAutoOutput = (value: boolean) => {
    setAutoOutputState(value);
    try {
      localStorage.setItem("dongbi.autoOutput", value ? "1" : "0");
    } catch {
      /* 只保留在記憶體 */
    }
  };
  const addOutputRoot = async (path: string, name: string, create: boolean) => {
    const result = await api.addOutputRoot(path, name, create);
    setOutputRoots(result.items);
    return result;
  };
  const sourceDuration = currentSource?.duration_us || duration;
  const offset = (() => {
    const m = Array.isArray(asset?.source_map)
      ? asset.source_map[0]
      : asset?.source_map;
    return (m?.source_start_us ?? 0) - (m?.asset_start_us ?? 0);
  })();
  function fail(e: unknown) {
    setError(e instanceof Error ? e.message : String(e));
    if (e instanceof ApiError && e.code === "REVISION_CONFLICT")
      setConflict(true);
  }
  async function run(fn: () => Promise<unknown>) {
    setError("");
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }
  async function refresh(p = project) {
    if (!p) return;
    const [s, j] = await Promise.all([api.sources(p), api.jobs(p)]);
    if (currentProject.current !== p) return;
    setSources(keep(s.items));
    setJobs(keep(j.items));
    // 流程快照執行會在後端改寫剪輯清單；沒有本機修改時自動載入最新版本，避免之後儲存撞 409
    const finishedWorkflows = j.items.filter((job) => job.kind === "workflow" && job.status === "succeeded").map((job) => job.job_id).sort().join(",");
    if (finishedWorkflows && finishedWorkflows !== checkedWorkflows.current) {
      checkedWorkflows.current = finishedWorkflows; // 只在完成的流程集合改變時多查一次剪輯清單，輪詢不多打 API
      const state = editState.current;
      if (!state.dirty && !state.busy && !state.conflict && !state.saveFailed) {
        const latest = await api.sequence(p);
        const after = editState.current;
        if (
          currentProject.current === p &&
          latest.revision !== after.revision &&
          !after.dirty && !after.busy && !after.conflict && !after.saveFailed
        ) {
          setSequence(latest);
          editor.reset(latest.items);
          setSourceId(latest.source_id || currentSourceId.current);
        }
      }
    }
    const ids = [
      ...new Set([
        ...s.items.flatMap((s) => s.asset_ids || []),
        ...j.items.flatMap((j) => j.result?.asset_ids || []),
      ]),
    ];
    const loaded = await Promise.all(ids.map((id) => api.asset(id)));
    if (currentProject.current !== p) return;
    setAssets(keep<Asset[]>(loaded));
    const selectedSource =
      s.items.find((x) => x.source_id === currentSourceId.current) ||
      s.items[0];
    if (selectedSource)
      request<{ items: Classification[] }>(
        `/projects/${p}/classifications?source_id=${selectedSource.source_id}&limit=200`,
      )
        .then((r) => {
          if (
            currentProject.current === p &&
            (!currentSourceId.current ||
              currentSourceId.current === selectedSource.source_id)
          )
            setClassifications(keep(r.items));
        })
        .catch(() => {});
    const tr = selectedSource?.transcript_revision;
    if (tr && loadedRevision.current !== tr) {
      const t = await api.transcript(p, tr);
      if (
        currentProject.current !== p ||
        (currentSourceId.current && t.source_id !== currentSourceId.current)
      )
        return;
      setTranscript(t);
      loadedRevision.current = tr;
    }
    api
      .annotations(p)
      .then((r) => {
        if (currentProject.current === p)
          setAnnotations(
            r.items.filter(
              (a) =>
                !a.transcript_revision ||
                a.transcript_revision === loadedRevision.current,
            ),
          );
      })
      .catch(() => {});
  }
  useJobEvents(jobs, () => refresh(project));
  async function openProject(p: string) {
    projectEpoch.current += 1;
    if (dirty && !window.confirm("有尚未儲存的剪輯，確定切換專案？")) return;
    setProjectNote("");
    setProject(p);
    localStorage.setItem("dongbi.project", p);
    setProjectName(
      projects.find((x) => x.project_id === p)?.name || "未命名專案",
    );
    setAsset(null);
    setClassifications([]);
    setAssets([]);
    setSources([]);
    setJobs([]);
    setSourceId("");
    setTranscript(null);
    loadedRevision.current = "";
    checkedWorkflows.current = "";
    setConflict(false);
    setDirty(false);
    const seq = await api.sequence(p);
    setSequence(seq);
    editor.reset(seq.items);
    setSourceId(seq.source_id || "");
    await refresh(p);
  }
  useEffect(() => {
    let alive = true;
    Promise.all([api.health(), api.projects()])
      .then(async ([, p]) => {
        if (!alive) return;
        setConnected(true);
        setProjects(p.items);
        const last = localStorage.getItem("dongbi.project");
        if (last && p.items.some((x) => x.project_id === last)) {
          setProject(last);
          setProjectName(p.items.find((x) => x.project_id === last)!.name);
          const seq = await api.sequence(last);
          setSequence(seq);
          editor.reset(seq.items);
          setSourceId(seq.source_id || "");
          await refresh(last);
        }
      })
      .catch(fail);
    api
      .providers()
      .then((r) => {
        setProviders(r.items);
        let saved: string | null = null;
        try {
          saved = localStorage.getItem("dongbi.provider");
        } catch {
          saved = null;
        }
        const ids = r.items.map((p) => p.id);
        if (saved === null) {
          const fallback =
            r.items.find((p) => p.id === "local-lmstudio" && p.local !== false) ||
            r.items.find((p) => p.local !== false);
          if (fallback) setProviderState(fallback.id);
        } else if (saved && !ids.includes(saved)) setProviderState("");
      })
      .catch(() => {});
    request<{
      output_roots: { id: string; name: string }[];
      source_subtitles?: boolean;
      asr_models?: AsrModelOption[];
      default_asr_model?: string;
    }>("/capabilities")
      .then((r) => {
        setOutputRoots(r.output_roots);
        setSourceSubtitlesEnabled(r.source_subtitles === true);
        setAsrModels(r.asr_models ?? []);
        if (r.default_asr_model) setDefaultAsrModel(r.default_asr_model);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);
  // 輪詢看狀態（domain/polling）：有工作在跑才 2 秒一次、閒置 15 秒、分頁看不見就停；回到分頁立刻更新一次
  const jobsActive = jobs.some((j) => ACTIVE_JOB_STATUSES.includes(j.status));
  useEffect(() => {
    if (!project) return;
    const poller = createPoller(
      () => refresh(project).catch(fail),
      () => pollDelay(jobsActive ? [{ status: "running" }] : [], document.hidden),
    );
    const onVisibility = () => (document.hidden ? poller.stop() : (poller.start(), poller.wake()));
    poller.start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      poller.stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, sourceId, jobsActive]);
  useEffect(() => {
    if (asset) return;
    const candidate = assets.find((a) => !sourceId || a.source_id === sourceId);
    if (candidate) {
      setAsset(candidate);
      setSourceId(candidate.source_id);
    }
  }, [assets, sourceId, asset]);
  function change(items: Segment[]) {
    editor.set(items);
    setDirty(true);
  }
  function selectSource(id: string) {
    if (editor.items.length && sourceId && id !== sourceId) {
      setError(
        "目前剪輯清單屬於另一個來源。請建立新專案編輯此來源，或先清空目前片段。",
      );
      return false;
    }
    setSourceId(id);
    setTranscript(null);
    setClassifications([]);
    loadedRevision.current = "";
    checkedWorkflows.current = "";
    return true;
  }
  const assetDurationUs = asset?.duration_us;
  const seek = useCallback(
    (us: number) => {
      if (!player.current) return;
      const seconds = (us - offset) / 1e6;
      if (seconds < 0 || seconds > (assetDurationUs ?? Infinity) / 1e6) {
        setError("這個時間不在目前素材內，請切換涵蓋該段的素材。");
        return;
      }
      player.current.currentTime = seconds;
      setTime(us);
    },
    [offset, assetDurationUs],
  );
  // 逐字稿面板為 memo 元件：以下 callback 的識別只在 project／逐字稿版本改變時更新，其餘狀態變動不會重繪長列表。
  const latest = useRef({ refresh, fail, jobs });
  latest.current = { refresh, fail, jobs };
  const transcriptRevision = transcript?.revision;
  const overrideClassification = useCallback<
    ComponentProps<typeof TranscriptPanel>["overrideClassification"]
  >(
    async (c, span, label) => {
      try {
        await request(
          `/projects/${project}/classifications/${c.classification_id || c.id}/overrides`,
          {
            method: "POST",
            body: JSON.stringify({
              overrides: [{ start_us: span.start_us, end_us: span.end_us, label }],
            }),
          },
        );
        await latest.current.refresh();
      } catch (e) {
        latest.current.fail(e);
        throw e;
      }
    },
    [project],
  );
  const editCue = useCallback<ComponentProps<typeof TranscriptPanel>["edit"]>(
    async (cue, text) => {
      try {
        const t = await api.edits(project, transcriptRevision!, [{ cue_id: cue.cue_id, text }], { origin: "manual" });
        setTranscript(t);
        loadedRevision.current = t.revision;
        // 逐句編輯後自動重新對齊（同一版本只排一次）
        void api.job(project, { kind: "align", transcript_revision: t.revision }).then(() => latest.current.refresh()).catch(() => undefined);
      } catch (e) {
        latest.current.fail(e);
        throw e;
      }
    },
    [project, transcriptRevision],
  );
  const ioValid = inPoint != null && outPoint != null && outPoint > inPoint;
  function clearInOut() {
    setInPoint(null);
    setOutPoint(null);
  }
  function addClip() {
    if (!ioValid) return; // 沒有有效的入出點範圍：按鈕已停用，這裡只是保險
    // 使用者自己加片段後，先前為了匯出自動補的「整段」片段就不需要了（它會蓋住整條時間軸）
    const kept = withoutAutoFull(editor.items);
    const s = createSegment({
      start_us: inPoint!,
      end_us: outPoint!,
      name: `片段 ${kept.length + 1}`,
    });
    change([...kept, s]);
    setActive(s.id);
    clearInOut(); // 加入後清掉範圍，避免重複加入同一段
  }
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      // 目標可能不是元素（例如 window）：只在真的落在輸入框時忽略快捷鍵
      const target = e.target as HTMLElement | null;
      if (target && typeof target.closest === "function" && target.closest("input,textarea,select,[contenteditable=true]")) return;
      if (e.ctrlKey || e.metaKey) {
        if (e.key.toLowerCase() === "z") {
          e.preventDefault();
          e.shiftKey ? editor.redo() : editor.undo();
          setDirty(true);
        }
        return;
      }
      if (e.key.toLowerCase() === "i") setInPoint(time);
      if (e.key.toLowerCase() === "o") setOutPoint(time);
      if (e.key === "Escape") clearInOut();
      if (e.key.toLowerCase() === "p" && page === "edit" && ioValid) {
        e.preventDefault();
        addClip(); // 有選取範圍時 P＝加入片段
      }
      if (e.key === " " && player.current) {
        e.preventDefault();
        player.current.paused
          ? void player.current.play().catch(fail)
          : player.current.pause();
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [time, editor]);
  useEffect(() => {
    const before = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", before);
    return () => window.removeEventListener("beforeunload", before);
  }, [dirty]);
  async function save(itemsOverride?: Segment[]) {
    setSaveFailed(false);
    const snapshot = itemsOverride ?? editor.items;
    const savedProject = project;
    const result = await api
      .save(project, {
        ...sequence,
        source_id: sourceId,
        items: snapshot,
      })
      .catch((e) => {
        setSaveFailed(true);
        throw e;
      });
    if (currentProject.current === savedProject) {
      setSequence(result);
      if (latestItems.current === snapshot) setDirty(false);
      setConflict(false);
    }
    return result;
  }
  useEffect(() => {
    if (!dirty || !project || !sourceId || conflict || busy || saveFailed)
      return;
    const timer = setTimeout(() => {
      void run(save);
    }, 1500);
    return () => clearTimeout(timer);
  }, [
    editor.items,
    dirty,
    project,
    sourceId,
    conflict,
    busy,
    sequence.revision,
    saveFailed,
  ]);
  // 專案清單只在開頁時抓一次，用 CLI／API 刪掉的專案會變成幽靈（2026-09-20 使用者回報）：
  // 回到這個視窗就重抓；開著的專案若已不存在，退回未開啟狀態，不要停在不存在的專案上。
  useEffect(() => {
    if (!connected) return;
    const resync = async () => {
      const epoch = projectEpoch.current;
      try {
        const listing = await api.projects();
        if (epoch !== projectEpoch.current) return; // 等回應的這段時間專案有變動：這份清單已經過時
        setProjects(listing.items);
        setProject((current) => {
          if (!current || listing.items.some((p) => p.project_id === current)) return current;
          localStorage.removeItem("dongbi.project");
          setProjectName("未命名專案");
          setProjectNote("這個專案已經不在了（可能在別的視窗或命令列刪掉），請重新選一個。");
          return "";
        });
      } catch {
        // 重抓失敗就維持現狀：真的斷線時由連線狀態負責提示
      }
    };
    window.addEventListener("focus", resync);
    return () => window.removeEventListener("focus", resync);
  }, [connected]);

  // 刪除專案（2026-09-20 使用者要整理工作室）：不可復原，所以先講清楚會刪掉什麼再問一次
  async function deleteProject() {
    if (!project) return;
    projectEpoch.current += 1;
    const name = projects.find((p) => p.project_id === project)?.name || "這個專案";
    if (!window.confirm(`確定刪除「${name}」？這個專案的逐字稿、字幕與成果檔都會一起刪掉，無法復原。（你的原始影音與輸出資料夾不受影響）`))
      return;
    const report = await api.deleteProject(project);
    setProjects(projects.filter((p) => p.project_id !== project));
    setProject("");
    setProjectName("未命名專案");
    localStorage.removeItem("dongbi.project");
    setSequence({ revision: "seq_00", source_id: null, items: [] });
    editor.reset([]);
    setSources([]);
    setAssets([]);
    setJobs([]);
    setAsset(null);
    setSourceId("");
    setTranscript(null);
    setAnnotations([]);
    setClassifications([]);
    loadedRevision.current = "";
    checkedWorkflows.current = "";
    setDirty(false);
    setProjectNote(`已刪除「${name}」：成果 ${report.removed_artifacts} 個、工作 ${report.removed_jobs} 個，釋出 ${(report.freed_bytes / 1e6).toFixed(1)} MB`);
  }

  async function createProject() {
    setProjectNote("");
    projectEpoch.current += 1;
    const p = await api.createProject(projectName.trim() || "未命名專案");
    projectEpoch.current += 1;
    setProjects([...projects, p]);
    setProject(p.project_id);
    setSequence({ revision: "seq_00", source_id: null, items: [] });
    editor.reset([]);
    setSources([]);
    setAssets([]);
    setJobs([]);
    setAsset(null);
    setSourceId("");
    setDirty(false);
    // 新專案是空的：上一個專案的逐字稿、摘要與分類不能留在面板上（2026-09-18 真瀏覽器發現）
    setTranscript(null);
    setAnnotations([]);
    setClassifications([]);
    loadedRevision.current = "";
    checkedWorkflows.current = "";
    localStorage.setItem("dongbi.project", p.project_id);
    // 新專案還沒有素材：回到「① 下載與剪輯」（下載範圍、格式都在這一頁；2026-09-21 真瀏覽器）
    setPage("edit");
  }
  async function acquire() {
    if (!project) throw Error("請先建立或開啟專案");
    const ranges = parseRanges(rangeText);
    // 2026-09-21 真瀏覽器 B02：沒填範圍就會下載整支（長片可能好幾小時）並自動轉錄，先問一次
    if (!ranges.length && !window.confirm(`沒有指定下載範圍：會下載整支${assetKind === "audio" ? "音訊" : "影片"}${analyze ? "，下載完還會自動轉錄" : ""}。長影片可能要很久，確定要整支下載嗎？（要只抓一段，請在「下載設定」填範圍，例如 1:50:00-2:00:00）`))
      return;
    let id = sourceId;
    if (url.trim()) {
      const source = await api.source(project, url.trim());
      id = source.source_id;
      if (!editor.items.length) selectSource(id);
      setUrl("");
    }
    if (!id) throw Error("請貼上來源連結或選擇素材");
    await api.job(project, {
      kind: "acquire",
      source_id: id,
      asset_kind: assetKind,
      ranges,
      quality: "source",
      boundary_policy: "accurate", // 2026-09-18：關鍵影格尋址會讓起點提前約 10 秒，一律精準切
      format_policy: {
        container,
        ...(height ? { max_height: Number(height) } : {}),
        ...(fps ? { max_fps: Number(fps) } : {}),
        ...(bitrate ? { audio_bitrate_kbps: Number(bitrate) } : {}),
        allow_transcode: container === "mp3",
      },
      ...(rootId ? { output_root_id: rootId } : {}),
      ...(analyze
        ? {
            ...analysisPolicies,
            follow_up: {
              kind: "analyze",
              profile,
              engine,
              fallback_engine: engine === "vibevoice" ? "whisperx" : null,
              // 與「轉錄」按鈕同一組設定：精修模型、遠端同意、轉錄術語提示（2026-09-21 真瀏覽器：以前沒帶，一律用預設）
              ...(profile !== "draft" ? { asr_model: effectiveAsrModel } : {}),
              ...(profile !== "draft" && asrModels.some((m) => m.key === effectiveAsrModel && m.remote) ? { remote_consent: true } : {}),
              ...(asrHintsPayload(asrHints) ? { asr_hints: asrHintsPayload(asrHints) } : {}),
            },
          }
        : {}),
    });
    await refresh();
  }
  // 逐詞對齊綁定目前的逐字稿版本；接受校字後版本改變，需重新對齊才會再用逐詞時間。
  const alignmentRevision = latestAlignment(jobs, transcript?.revision);
  const aligning = alignmentRunning(jobs, transcript?.revision);
  const alignFailed = !!transcript && jobs.some((j) => j.kind === "align" && j.status === "failed" && (j.body as { transcript_revision?: string } | undefined)?.transcript_revision === transcript.revision);
  // 轉錄狀態：目前來源最新一次 analyze 工作
  const transcribeJob = jobs.find((j) => j.kind === "analyze" && (!j.body?.source_id || j.body.source_id === sourceId));
  const transcribing = !!transcribeJob && ["queued", "running", "waiting"].includes(transcribeJob.status);
  const requestAlignment = useCallback(
    async (revision: string) => {
      if (alignRequested.current.has(revision)) return;
      alignRequested.current.add(revision);
      await api.job(project, { kind: "align", transcript_revision: revision });
      await refresh();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [project],
  );
  // 沒有對齊、也沒有對齊工作在跑（例如舊逐字稿或錯過的情況）：載入 3 秒後自動補排一次
  useEffect(() => {
    const revision = transcript?.revision;
    // 轉錄進行中不補排：草稿版本馬上會被精修版本取代，後端會在轉錄完成時自己排對齊（2026-09-18 真跑看到重複對齊）
    if (!revision || alignmentRevision || aligning || alignFailed || transcribing || alignRequested.current.has(revision)) return;
    const timer = setTimeout(() => {
      if (!alignmentRunning(latest.current.jobs, revision) && !latestAlignment(latest.current.jobs, revision)) void requestAlignment(revision).catch(() => undefined);
    }, 3000);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transcript?.revision, alignmentRevision, aligning, alignFailed, transcribing]);
  // 摘要：最新一次 summarize 工作的狀態；完成時切到「摘要與重點」分頁（每個工作只切一次）
  const summaryJob = jobs.find((j) => j.kind === "summarize");
  // 最近一次校字：按鈕旁說清楚結果（0 處修改也要講，不然看起來像沒作用）
  const correctJob = jobs.find((j) => j.kind === "correct" && (!j.body?.source_id || j.body.source_id === sourceId));
  // 內容拆解單詞：用「校字與摘要模型」選的文字模型把貼上的內容整理成逗號分隔關鍵詞；沒選或模型不可用時後端改用本機規則
  const splitKeywords = async () => {
    const text = asrHints.trim();
    if (!text) return;
    setSplittingKeywords(true);
    setKeywordStatus("");
    try {
      const chosen = providers.find((p) => p.id === provider);
      const result = await api.keywords({ text, provider_id: provider || null, remote_consent: chosen?.local === false });
      if (!result.keywords.length) {
        setKeywordStatus("沒有找到可用的關鍵詞，內容保持不變");
        return;
      }
      setAsrHints(result.joined);
      setKeywordStatus(
        result.method === "llm"
          ? `用 ${provider} · ${result.model || "模型"} 拆出 ${result.keywords.length} 個詞`
          : `用本機規則拆出 ${result.keywords.length} 個詞${result.warning ? `（文字模型無法使用：${result.warning}）` : provider ? "" : "（沒有選文字模型）"}`,
      );
    } catch (e) {
      setKeywordStatus(`拆解失敗：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSplittingKeywords(false);
    }
  };
  const summarizing = !!summaryJob && ["queued", "running", "waiting"].includes(summaryJob.status);
  useEffect(() => {
    if (!summaryJob || summaryJob.status !== "succeeded" || seenSummaries.current.has(summaryJob.job_id)) return;
    seenSummaries.current.add(summaryJob.job_id);
    if (page === "subtitles") setTabRequest({ tab: "summary", nonce: Date.now() });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summaryJob?.job_id, summaryJob?.status]);
  const [exportError, setExportError] = useState("");
  async function exportOutput() {
    setExportError("");
    try {
      await exportSelection();
    } catch (e) {
      setExportError(e instanceof Error ? e.message : String(e)); // 也顯示在按鈕旁，不只在頂部橫幅
      throw e;
    }
  }
  async function exportSubtitles() {
    // 字幕頁：整段素材的 SRT，直接指定範圍，不動第 1 頁的剪輯清單
    if (!transcript) throw Error("請先完成轉錄");
    if (!asset) throw Error("請先下載或匯入素材");
    setExportError("");
    await api.job(project, {
      kind: "export",
      // 與字幕預覽同一組參數（subtitleRequest）：預覽看到的就是檔案內容
      ...subtitleRequest()!,
      // 這一版已對齊就直接用；還沒（剛編輯／校字完）就請後端等對齊完成再輸出，失敗才退回句子時間並在清單加警告
      ...(alignmentRevision ? { alignment_revision: alignmentRevision } : { await_alignment: true }),
      formats: ["srt"],
      alignment_policy: "allow_segment",
      include_records: true,
      ...(effectiveExportRoot ? { output_root_id: effectiveExportRoot } : {}),
    });
    await refresh();
  }
  async function exportHistory() {
    if (!transcript) throw Error("請先完成轉錄");
    const history = await api.transcriptHistory(project, transcript.revision);
    downloadJson(`${projectName || "專案"}_逐字稿修改紀錄_${transcript.revision.slice(-6)}.json`, history);
  }
  async function exportSelection() {
    let items = editor.items;
    if (!items.some((x) => x.selected && x.end_us != null)) {
      // 沒有加片段：直接輸出目前素材的整段（轉錄完成即可匯出帶時間軸的 SRT）
      if (!asset) throw Error("請先加入要輸出的片段，或先下載／匯入素材");
      const whole = createSegment({ start_us: offset, end_us: offset + asset.duration_us, name: "整段（自動）", selected: true, tags: { auto: "full" } });
      items = [...items, whole];
      editor.set(items);
      setDirty(true);
    }
    const seq = dirty || items !== editor.items ? await save(items) : sequence;
    if (attachSrt && !transcript) throw Error("請先完成逐字稿，或取消附上 SRT");
    if (!exportMedia) throw Error("請勾選影音輸出");
    await api.job(project, {
      kind: "export",
      sequence_revision: seq.revision,
      ...(transcript ? { transcript_revision: transcript.revision } : {}),
      ...(transcript && alignmentRevision ? { alignment_revision: alignmentRevision } : {}),
      ...(attachSrt && transcript && !alignmentRevision ? { await_alignment: true } : {}),
      formats: [
        ...(exportMedia ? [mediaFormat] : []),
        ...(attachSrt ? ["srt"] : []),
      ],
      grouping,
      cut_mode: cutMode,
      ...(effectiveExportRoot ? { output_root_id: effectiveExportRoot } : {}),
      subtitle_timebase: grouping === "merge" ? "sequence" : "clip",
      alignment_policy: "allow_segment",
      sentences_per_cue: subtitleSettings.sentences_per_cue,
      keep_punctuation: subtitleSettings.preserve_punctuation,
    });
    await refresh();
  }
  const submitAnalysis = (kind: string) =>
    run(async () => {
      if (!sourceId) throw Error("請先選擇素材");
      if (kind === "analyze" && !asset) throw Error("請先下載或匯入素材，再轉錄");
      const created = await api.job(project, {
        kind,
        source_id: sourceId,
        ...(kind === "analyze"
          ? {
              // 只轉錄播放中的這一份素材：同一範圍可能有多份下載（時間軸不同），混在一起會互相覆蓋
              asset_ids: [asset!.asset_id],
              // 精修模型寫明送出（畫面顯示哪個就用哪個）；快速草稿不做精修，不送
              ...(profile !== "draft" ? { asr_model: effectiveAsrModel } : {}),
              ...(profile !== "draft" && asrModels.some((m) => m.key === effectiveAsrModel && m.remote) ? { remote_consent: true } : {}),
              // 轉錄術語提示：草稿與精修都會用到（Whisper hotwords、Qwen context）
              ...(asrHintsPayload(asrHints) ? { asr_hints: asrHintsPayload(asrHints) } : {}),
              ...(autoOutput
                ? {
                    auto_export: {
                      ...(effectiveExportRoot ? { output_root_id: effectiveExportRoot } : {}),
                      sentences_per_cue: subtitleSettings.sentences_per_cue,
                      keep_punctuation: subtitleSettings.preserve_punctuation,
                    },
                  }
                : {}),
              profile,
              engine,
              fallback_engine: engine === "vibevoice" ? "whisperx" : null,
              ...analysisPolicies,
            }
          : {
              transcript_revision: transcript?.revision,
              provider_id: provider || null,
              outputs: ["summary", "chapters", "highlights"],
              mode: "suggest",
              ...(kind === "correct"
                ? {
                    glossary: glossaryTerms(glossaryText),
                    correction_mode: correctionMode,
                    // 字幕格式交給提示詞（2026-09-21 使用者：根本問題是送出的提示詞沒說要什麼格式）
                    keep_punctuation: subtitleSettings.preserve_punctuation,
                    ...(referenceText.trim() ? { reference_text: referenceText.trim().slice(0, REFERENCE_MAX) } : {}),
                  }
                : {}),
            }),
      });
      if (kind === "correct" && created?.job_id) {
        setCorrectRequested(true);  // 送出後立刻有提示，不用等輪詢
        if (autoApply) setPendingApply((v) => [...v, created.job_id]);
      }
      await refresh();
    });
  // 校字直接套用：工作完成後把守門通過的修正一次寫進逐字稿（同一 base_revision），失敗則留在待確認建議
  useEffect(() => {
    if (!pendingApply.length || !transcript) return;
    for (const id of pendingApply) {
      const job = jobs.find((j) => j.job_id === id);
      if (!job || ["queued", "running", "waiting"].includes(job.status)) continue;
      if (applying.current.has(id)) continue;
      applying.current.add(id);
      setPendingApply((v) => v.filter((x) => x !== id));
      // 2026-09-20 使用者：「一上下文處理逐字稿就會每一句套上，而不是還要我確認」→ 守門通過的修正全部套用（含低信心），
      // 只留新舊比對與整批還原；被規則擋下的仍列在下方由人決定
      const patches = ((job.result?.patches || []) as Correction[]).filter(
        (p) => p.replacement_text && p.replacement_text !== p.original_text,
      );
      if (job.status !== "succeeded" || !patches.length) continue;
      const base = patches[0].base_revision || transcript.revision;
      if (base !== transcript.revision) {
        setError("校字結果對應的逐字稿版本已變更，未自動套用；請在待確認建議逐條處理或重新校字");
        continue;
      }
      void run(async () => {
        const t = await api.edits(project, base, patches.map((p) => ({ cue_id: p.cue_id, text: p.replacement_text })), { origin: "llm_correction", job_id: id });
        setTranscript(t);
        loadedRevision.current = t.revision;
        setApplied((v) => ({ ...v, [id]: { patches, before: base, after: t.revision } }));
        await requestAlignment(t.revision); // 文字改了：自動重新逐詞對齊
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs, pendingApply, transcript?.revision]);
  // 重新載入後 applied 狀態不在了：從逐字稿內容推回「哪些高信心修正已經在稿裡」，照樣可還原
  const appliedView = useMemo(() => {
    const view: Record<string, AppliedCorrection> = { ...applied };
    if (!transcript) return view;
    const newest = jobs.find((j) => j.kind === "correct" && j.status === "succeeded" && j.result);
    if (!newest || view[newest.job_id]) return view;
    const byId = new Map(transcript.cues.map((c) => [c.cue_id, c.text]));
    // 任何信心等級：只要修正文字已在稿裡，就算已套用（還原時一起還原）；沒在稿裡的低信心建議留成卡片
    const present = ((newest.result?.patches || []) as Correction[]).filter(
      (p) => p.replacement_text !== p.original_text && byId.get(p.cue_id) === p.replacement_text,
    );
    if (present.length) view[newest.job_id] = { patches: present, before: present[0].base_revision || "", after: transcript.revision };
    return view;
  }, [applied, jobs, transcript]);
  // 字幕預覽＝匯出 SRT 的參數（2026-09-20 使用者第 6 點）
  const subtitleRequest = (): SubtitleRequest | null =>
    asset && transcript
      ? {
          source_id: asset.source_id,
          ranges: [{ start_us: offset, end_us: offset + asset.duration_us }],
          transcript_revision: transcript.revision,
          sentences_per_cue: subtitleSettings.sentences_per_cue,
          keep_punctuation: subtitleSettings.preserve_punctuation,
          subtitle_timebase: "sequence",
          grouping: "merge",
        }
      : null;
  const [subtitlePreview, setSubtitlePreview] = useState<SubtitlePreview | null>(null);
  const [subtitlePreviewError, setSubtitlePreviewError] = useState("");
  useEffect(() => {
    const body = subtitleRequest();
    if (!project || !body || transcript?.source_id !== asset?.source_id) {
      setSubtitlePreview(null);
      return;
    }
    let cancelled = false;
    // 連續編輯時只算最後一次；換版本、換設定、對齊完成都重算
    const timer = setTimeout(async () => {
      try {
        const result = await api.subtitlePreview(project, { ...body, ...(alignmentRevision ? { alignment_revision: alignmentRevision } : {}) });
        if (!cancelled) {
          setSubtitlePreview(result);
          setSubtitlePreviewError("");
        }
      } catch (e) {
        if (!cancelled) setSubtitlePreviewError(e instanceof Error ? e.message : String(e));
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, transcript?.revision, asset?.asset_id, offset, alignmentRevision, subtitleSettings.sentences_per_cue, subtitleSettings.preserve_punctuation]);
  const currentSubtitle = asset ? subtitlePreview?.entries.find((e) => e.source_start_us <= time && time < e.source_end_us) : undefined;
  // 入出點抽查（M4-C5）：只播這個時間點前後各 0.5 秒，聽得出字幕早了還是晚了
  const auditPoint = useCallback((us: number) => {
    const media = player.current;
    if (!media) return;
    const center = (us - offset) / 1e6;
    if (center < 0 || center > (assetDurationUs ?? Infinity) / 1e6) {
      setError("這個時間不在目前素材內，請切換涵蓋該段的素材。");
      return;
    }
    window.clearTimeout(auditTimer.current);
    media.currentTime = Math.max(0, center - AUDIT_WINDOW_US / 1e6);
    void media.play();
    auditTimer.current = window.setTimeout(() => media.pause(), (AUDIT_WINDOW_US * 2) / 1000);
  }, [offset, assetDurationUs]);
  // 被校字改過的句子（cue_id → 原文）：逐字稿那一句下面顯示，看得出改了什麼
  const correctedCues = useMemo(() => {
    const map: Record<string, string> = {};
    for (const record of Object.values(appliedView)) for (const patch of record.patches) map[patch.cue_id] = patch.original_text;
    return map;
  }, [appliedView]);
  const chosenProvider = providers.find((p) => p.id === provider);
  // 沒測試前先講現況：本機＝跟著載入的模型；遠端＝後台有沒有金鑰、用哪一個模型
  const providerState = !provider
    ? "沒有選文字模型：摘要改用抽取式，校字與拆詞用本機規則"
    : chosenProvider?.local === false
      ? chosenProvider.secret_configured
        ? `後台已填金鑰，使用 ${chosenProvider.model || "未設定模型"}`
        : "後台未填金鑰：到右上角齒輪「模型設定」填入"
      : chosenProvider?.model === "auto"
        ? "跟著服務目前載入的模型；按「測試連線」確認"
        : `目前設定：${chosenProvider?.model || "未設定模型"}`;
  const testProvider = async () => {
    if (!provider) return;
    setProbing(true);
    setProbeText("");
    try {
      const result = await api.probeProvider(provider);
      const remote = chosenProvider?.local === false;
      setProbeText(
        result.status === "ready"
          ? remote
            ? `已連線：後台金鑰可用，使用 ${result.model}`
            : `已連線：${result.model}${result.auto_model ? "（目前載入）" : ""}`
          : result.status === "secret_missing"
            ? `後台未填金鑰：到「模型設定」填入（後台設定的模型是 ${result.model || chosenProvider?.model || "未設定"}）`
            : result.status === "model_not_loaded"
              ? "服務有回應，但沒有載入任何對話模型：請先在 LM Studio／llama.cpp 載入"
              : `無法使用：${result.status}${result.detail ? `（${result.detail}）` : ""}`,
      );
    } catch (e) {
      setProbeText(`測試失敗：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setProbing(false);
    }
  };
  const providerHint = provider
    ? `拆解會用「AI 分析、摘要與校字」選的模型：${MODEL_PORTS.find((p) => p.id === provider)?.label || provider}`
    : "拆解會用「AI 分析、摘要與校字」選的模型；目前沒選，會用本機規則";
  const correctionText = correctionSummary(correctJob, correctJob ? appliedView[correctJob.job_id]?.patches.length || 0 : 0);
  async function revertCorrection(jobId: string) {
    const record = appliedView[jobId];
    if (!record || !transcript) return;
    if (transcript.revision !== record.after) throw Error("逐字稿在套用後又被修改，無法一鍵還原；請用逐句編輯調整");
    const t = await api.edits(project, transcript.revision, record.patches.map((p) => ({ cue_id: p.cue_id, text: p.original_text })), { origin: "llm_revert", job_id: jobId });
    setTranscript(t);
    loadedRevision.current = t.revision;
    await requestAlignment(t.revision);
    setApplied((v) => {
      const next = { ...v };
      delete next[jobId];
      return next;
    });
  }
  // 音訊素材沒有畫面：影音輸出改為音訊檔，避免匯出失敗「選區尚未取得可匯出素材」
  const assetIsAudioOnly = !!asset && (asset.kind === "audio" || asset.asset_kind === "audio");
  useEffect(() => {
    if (assetIsAudioOnly && mediaFormat === "mp4") setMediaFormat("audio");
  }, [assetIsAudioOnly, mediaFormat]);
  const transcribeStage: Record<string, string> = {
    queued: "排隊",
    decode: "解碼音訊",
    asr: "草稿辨識",
    refine: `${asrModelName(transcribeJob?.body?.asr_model)} 精修`,
    classify: "音樂分類",
  };
  return (
    <>
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark">
            <Scissors size={20} />
          </span>
          <h1>冬比字幕工作室</h1>
        </div>
        <nav className="page-tabs" role="tablist" aria-label="工作頁">
          <button type="button" role="tab" aria-selected={page === "edit"} className={page === "edit" ? "selected" : ""} onClick={() => setPage("edit")}>
            ① 下載與剪輯
          </button>
          <button type="button" role="tab" aria-selected={page === "subtitles"} className={page === "subtitles" ? "selected" : ""} onClick={() => setPage("subtitles")}>
            ② 字幕與校字
          </button>
        </nav>
        <div className="project-controls">
          <input
            aria-label="專案名稱"
            value={projectName}
            onChange={(e) => setProjectName(e.target.value)}
          />
          <button
            onClick={() => run(createProject)}
            disabled={busy || !connected}
          >
            <Plus size={15} />
            新專案
          </button>
          <select
            aria-label="開啟專案"
            value={project}
            onChange={(e) => run(() => openProject(e.target.value))}
          >
            <option value="">開啟專案</option>
            {projects.map((p) => (
              <option key={p.project_id} value={p.project_id}>
                {p.name}
              </option>
            ))}
          </select>
          <button
            className="danger"
            onClick={() => run(deleteProject)}
            disabled={!project || busy}
            title="刪除這個專案的資料與成果檔（不可復原）"
          >
            <Trash2 size={15} />
            刪除專案
          </button>
          <button
            onClick={() => run(save)}
            disabled={!project || busy || !dirty}
          >
            <Save size={15} />
            儲存
          </button>
          <span className="save-status" title={projectNote || undefined}>
            {projectNote
              ? projectNote
              : busy && dirty
              ? "儲存中…"
              : dirty
                ? "等待自動儲存"
                : project
                  ? "已儲存"
                  : ""}
          </span>
        </div>
        <button
          className="icon-button"
          aria-label="模型設定"
          onClick={() => setShowSettings(!showSettings)}
        >
          <Settings2 size={19} />
        </button>
        <span className={`connection ${connected ? "online" : ""}`}>
          {connected ? "本機服務已連線" : "服務未連線"}
        </span>
      </header>
      {error && (
        <div className="error-banner" role="alert">
          <span>{error}</span>
          {conflict && (
            <button
              onClick={() =>
                run(async () => {
                  const latest = await api.sequence(project);
                  setSequence(latest);
                  editor.reset(latest.items);
                  setDirty(false);
                  setConflict(false);
                  setError("");
                })
              }
            >
              捨棄本機修改並載入最新版本
            </button>
          )}
          <button onClick={() => setError("")}>關閉</button>
        </div>
      )}
      {showSettings && (
        <section className="settings-panel">
          <h2>文字模型服務</h2>
          <p className="muted">
            這裡只做兩件事：確認本機服務有沒有連上、填 OpenRouter／ElevenLabs 金鑰。要用哪一個來源，在「② 字幕與校字 → AI 分析、摘要與校字」選。
          </p>
          <CollapsibleSection id="providers" title="文字模型供應者" hint="本機與遠端服務、金鑰、測試連線">
          <ProviderPanel
            list={api.providerList}
            probe={api.probeProvider}
            setSecret={api.providerSecretSet}
            deleteSecret={api.providerSecretDelete}
            save={api.providerSave}
            consent={remoteConsent}
            onConsent={(value) => {
              setRemoteConsentState(value);
              try {
                localStorage.setItem("dongbi.remote_consent", value ? "1" : "0");
              } catch {
                /* 瀏覽器儲存不可用時只保留在記憶體 */
              }
              if (!value) setProvider("");
            }}
            onChanged={() =>
              api
                .providers()
                .then((r) => setProviders(r.items))
                .catch(() => undefined)
            }
          />
          </CollapsibleSection>
          <CollapsibleSection id="environment" title="環境與模型" hint="FFmpeg、Node、顯示卡、套件、模型下載與路徑" defaultOpen={false} lazy>
          <EnvironmentPanel
            load={api.environment}
            recheck={api.recheckEnvironment}
            models={api.modelStatus}
            download={api.downloadModels}
          />
          </CollapsibleSection>
        </section>
      )}
      <main className={page === "edit" ? "mode-edit" : "mode-subtitles"}>
        {/* 來源列：貼連結下載或匯入本機影音；兩頁共用、永遠顯示（不放在可摺疊區塊裡） */}
        <section className="source-bar" aria-label="來源">
          <div className="source-entry">
            <input
              aria-label="影片來源連結"
              type="url"
              placeholder="貼上 YouTube 連結，從想看的段落開始"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
            <button
              className="primary"
              onClick={() => run(acquire)}
              disabled={!project || busy}
            >
              <Download size={17} />
              加入下載
            </button>
            <button
              disabled={!project || busy}
              title="用視窗選檔：不複製、不改原檔，輸出預設放在同資料夾的 subtitle_studio"
              onClick={() =>
                run(async () => {
                  let picked: { path: string | null; cancelled: boolean };
                  try {
                    picked = await api.pickFile("選擇要匯入的影音檔");
                  } catch (e) {
                    // 只有這台電腦開不了原生視窗才退回瀏覽器上傳（會複製一份到專案資料目錄）；連線失效等錯誤照常顯示
                    if (!isDialogUnavailable(e)) throw e;
                    inputFile.current?.click();
                    return;
                  }
                  if (!picked.path) return;
                  const r = await api.importLocal(project, picked.path);
                  selectSource(r.source_id);
                  setExportRootId("");
                  const roots = await request<{ output_roots: { id: string; name: string; path?: string }[] }>("/capabilities");
                  setOutputRoots(roots.output_roots);
                  await refresh();
                })
              }
            >
              <Upload size={16} />
              匯入本機檔案
            </button>
            <input
              ref={inputFile}
              type="file"
              accept="video/*,audio/*"
              hidden
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f)
                  run(async () => {
                    const r = await api.upload(project, f);
                    const selected = selectSource(
                      r.source_id || r.source!.source_id,
                    );
                    if (selected && r.asset) setAsset(r.asset);
                    else if (selected && r.asset_id)
                      setAsset(await api.asset(r.asset_id));
                    await refresh();
                  });
                e.target.value = "";
              }}
            />
          </div>
          <span className="muted source-bar-state">
            {currentSource ? `目前素材：${currentSource.title || "未命名"}${asset ? "" : "（尚未取得檔案）"}` : "貼上連結下載，或匯入已剪好的影音；兩頁共用同一份素材"}
          </span>
        </section>
        <div className="page" data-page="edit" hidden={page !== "edit"}>
        <CollapsibleSection id="acquire" title="下載設定" hint="取得內容、指定下載範圍、格式與儲存位置（連結與匯入在頁面最上方）" className="acquisition-panel">
          <div className="download-options">
            <label>
              取得內容
              <select
                value={assetKind}
                onChange={(e) => setAssetKind(e.target.value)}
              >
                <option value="audio">音訊</option>
                <option value="video">影片含音訊</option>
              </select>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={analyze}
                onChange={(e) => setAnalyze(e.target.checked)}
              />
              下載完成後自動轉錄（轉錄設定在「字幕與校字」頁）
            </label>
          </div>
          <details className="advanced-options">
            <summary>
              進階下載設定 <span className="muted">格式、畫質、幀率、位元率、儲存位置</span>
            </summary>
            <div className="download-options">
            <label>
              格式
              <select
                value={container}
                onChange={(e) => setContainer(e.target.value)}
              >
                {["source", "mp4", "mkv", "m4a", "mp3"].map((f) => (
                  <option key={f} value={f}>
                    {f === "source" ? "保留來源" : f.toUpperCase()}
                  </option>
                ))}
              </select>
            </label>
            <label>
              畫質上限
              <select
                value={height}
                onChange={(e) => setHeight(e.target.value)}
              >
                <option value="">來源最高</option>
                {[720, 1080, 1440, 2160].map((v) => (
                  <option key={v} value={v}>
                    {v}p 上限
                  </option>
                ))}
              </select>
            </label>
            <label>
              幀率上限
              <select value={fps} onChange={(e) => setFps(e.target.value)}>
                <option value="">保留來源</option>
                <option value="30">30 fps</option>
                <option value="60">60 fps</option>
              </select>
            </label>
            <label>
              音訊位元率
              <select
                value={bitrate}
                onChange={(e) => setBitrate(e.target.value)}
              >
                <option value="">保留來源</option>
                {[128, 192, 256].map((v) => (
                  <option key={v} value={v}>
                    {v} kbps
                  </option>
                ))}
              </select>
            </label>
            </div>
          </details>
          <details>
            <summary>
              指定下載範圍{" "}
              <span className="muted">留空下載整支；每行一個區段</span>
            </summary>
            <textarea
              aria-label="下載時間範圍"
              placeholder={
                "01:50:00-01:51:00\n01:54:00-01:55:00\n01:59:00-02:00:00"
              }
              value={rangeText}
              onChange={(e) => setRangeText(e.target.value)}
            />
          </details>
          <OutputRootPicker
            label="下載"
            roots={outputRoots}
            value={rootId}
            onChange={setRootId}
            addRoot={addOutputRoot}
            pickFolder={api.pickFolder}
          />
        </CollapsibleSection>
        <section className="edit-toolbar">
          <div className="toolbar-group">
            <button disabled={!asset} onClick={() => setInPoint(time)}>
              入點 <kbd>I</kbd>
            </button>
            <TimecodeInput label="入點時間" value={inPoint} base={offset} max={duration > offset ? duration - offset : undefined} onCommit={setInPoint} onError={setError} />
            <button disabled={!asset} onClick={() => setOutPoint(time)}>
              出點 <kbd>O</kbd>
            </button>
            <TimecodeInput label="出點時間" value={outPoint} base={offset} max={duration > offset ? duration - offset : undefined} onCommit={setOutPoint} onError={setError} />
            <button
              disabled={!asset || !ioValid}
              title={!ioValid ? "先設定入點與出點（出點要在入點之後）" : undefined}
              onClick={addClip}
            >
              <Plus size={16} />
              加入片段
            </button>
            <button disabled={inPoint == null && outPoint == null} onClick={clearInOut} title="Esc 也可以清除">
              清除入出點
            </button>
          </div>
          <div className="toolbar-group">
            <button
              disabled={!active}
              onClick={() =>
                change(
                  editor.items.flatMap((s) =>
                    s.id === active ? splitSegment(s, time) : [s],
                  ),
                )
              }
            >
              <Scissors size={16} />
              分割
            </button>
            <button
              disabled={!asset}
              onClick={() => {
                const s = createSegment({ start_us: time, name: "標記" });
                change([...editor.items, s]);
                setActive(s.id);
              }}
            >
              <Flag size={15} />
              標記
            </button>
            <button
              aria-label="復原"
              disabled={!editor.history.past.length}
              onClick={() => {
                editor.undo();
                setDirty(true);
              }}
            >
              <Undo2 size={17} />
            </button>
            <button
              aria-label="重做"
              disabled={!editor.history.future.length}
              onClick={() => {
                editor.redo();
                setDirty(true);
              }}
            >
              <Redo2 size={17} />
            </button>
          </div>
        </section>
        <CollapsibleSection id="segments" title="片段清單" badge={editor.items.length || undefined} hint="拖曳排序、匯入剪輯清單" className="sequence-panel">
          <div className="import-toolbar">
            <button
              disabled={!asset || busy}
              onClick={() => importFile.current?.click()}
            >
              匯入剪輯清單
            </button>
            <button
              disabled={!project || dirty || busy}
              onClick={() =>
                run(async () => {
                  const seq = await api.sequence(project);
                  setSequence(seq);
                  editor.reset(seq.items);
                  setSourceId(seq.source_id || sourceId);
                })
              }
            >
              載入最新剪輯
            </button>
            <input
              type="file"
              hidden
              ref={importFile}
              accept=".llc,.csv,.json"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file)
                  run(async () => {
                    if (dirty) throw Error("請先儲存目前剪輯");
                    if (file.size > 10000000)
                      throw Error("剪輯清單不可超過 10 MB");
                    const content = await file.text();
                    const format = file.name.endsWith(".csv")
                      ? "csv"
                      : file.name.endsWith(".llc")
                        ? "llc"
                        : "studio_json";
                    await request(`/projects/${project}/imports`, {
                      method: "POST",
                      body: JSON.stringify({
                        asset_id: asset!.asset_id,
                        format,
                        content,
                      }),
                    });
                    await refresh();
                  });
                e.target.value = "";
              }}
            />
            <span className="muted">
              匯入工作完成後，按「載入最新剪輯」開啟
            </span>
          </div>
          <SegmentList
            items={editor.items}
            active={active}
            select={setActive}
            update={(s) =>
              change(editor.items.map((x) => (x.id === s.id ? s : x)))
            }
            move={(a, b) => change(moveSegment(editor.items, a, b))}
            duplicate={(s) => change([...editor.items, createSegment(s)])}
            remove={(id) => change(editor.items.filter((s) => s.id !== id))}
            error={setError}
            timeBase={offset}
          />
        </CollapsibleSection>
        <CollapsibleSection id="export" title="影音輸出" hint="依片段清單順序：合併成一個檔案或各片段獨立檔案，原畫質裁切或精準重編碼" className="export-bar">
          <label>
            檔案組合
            <select
              value={grouping}
              onChange={(e) => setGrouping(e.target.value)}
            >
              <option value="separate">各片段獨立檔案</option>
              <option value="merge">合併為一個檔案</option>
            </select>
          </label>
          <label>
            剪輯方式
            <select
              value={cutMode}
              onChange={(e) => setCutMode(e.target.value)}
            >
              <option value="accurate">精準剪輯（重編碼）</option>
              <option value="copy">快速剪輯（關鍵影格）</option>
            </select>
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={exportMedia}
              onChange={(e) => setExportMedia(e.target.checked)}
            />
            影音
          </label>
          <label>
            影音格式
            <select
              aria-label="影音格式"
              value={mediaFormat}
              onChange={(e) => setMediaFormat(e.target.value)}
            >
              <option value="mp4" disabled={assetIsAudioOnly}>
                {assetIsAudioOnly ? "MP4 影片（目前素材只有音訊）" : "MP4 影片"}
              </option>
              <option value="audio">音訊檔案</option>
            </select>
          </label>
          <label className="check" title="字幕的完整匯出在「字幕與校字」頁；這裡只在需要時附上對應這次剪輯時間的 SRT">
            <input
              type="checkbox"
              checked={attachSrt}
              onChange={(e) => setAttachSrt(e.target.checked)}
            />
            附上對應剪輯的 SRT
          </label>
          <OutputRootPicker
            label="影音輸出"
            roots={outputRoots}
            value={effectiveExportRoot}
            onChange={setExportRootId}
            addRoot={addOutputRoot}
            pickFolder={api.pickFolder}
          />
          <button
            className="primary"
            disabled={!project || busy}
            onClick={() => run(exportOutput)}
          >
            <Download size={17} />
            開始匯出
          </button>
          {exportError && (
            <p className="export-status error-text" role="alert">
              {exportError}
            </p>
          )}
          <ExportStatus jobs={jobs} filter={(job) => !(job.body?.formats as string[] | undefined)?.every((f) => f === "srt")} />
        </CollapsibleSection>
        </div>
        {/* 字幕頁上排：轉錄與 AI 分析（整列） */}
        <div className="page page-top" data-page="subtitles" hidden={page !== "subtitles"}>
          <CollapsibleSection id="transcribe" title="轉錄" hint="草稿用 WhisperX，精修用下面選的模型；完成後自動逐詞對齊" className="transcribe-panel">
          <div className="transcribe-row">
            <div className="transcribe-settings">
              <AnalysisOptions
                prefix="轉錄"
                value={analysisPolicies}
                change={setAnalysisPolicies}
              />
              <label>
                辨識品質
                <select
                  aria-label="辨識品質"
                  value={profile}
                  onChange={(e) => setProfile(e.target.value)}
                >
                  <option value="draft">快速草稿</option>
                  <option value="balanced">平衡：草稿＋選段精修</option>
                  <option value="quality">精修：草稿＋全段精修</option>
                </select>
              </label>
              <label>
                精修引擎
                <select
                  aria-label="精修引擎"
                  value={engine}
                  onChange={(e) =>
                    setEngine(e.target.value as "whisperx" | "vibevoice")
                  }
                >
                  <option value="whisperx">WhisperX（預設）</option>
                  <option value="vibevoice">VibeVoice（可選，約 11.5 GB VRAM，較慢）</option>
                </select>
              </label>
              <label title="Breeze 只處理中文段落；日文、英文段落照舊用 large-v3。未安裝的模型到右上角齒輪「模型設定」→「環境與模型」下載並轉換">
                精修模型
                <select
                  aria-label="精修模型"
                  value={effectiveAsrModel}
                  onChange={(e) => setAsrModel(e.target.value)}
                  disabled={profile === "draft"}
                  title={profile === "draft" ? "快速草稿不做精修；選「平衡」或「精修」才會用到精修模型" : undefined}
                >
                  {(asrModels.length ? asrModels : [{ key: "large-v3", label: "Whisper large-v3", installed: true, download_bytes: 0 }]).map((m) => (
                    <option key={m.key} value={m.key} disabled={!asrModelReady(m.key)}>
                      {m.label}
                      {m.key === asrModelDefault ? "（預設）" : ""}
                      {asrModelNote(m)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="transcribe-actions">
              <button
                className="primary"
                disabled={!asset || busy || transcribing}
                title={!asset ? "先下載或匯入素材" : undefined}
                onClick={() => submitAnalysis("analyze")}
              >
                {transcribing ? "轉錄中…" : transcript ? "重新轉錄" : "轉錄"}
              </button>
              {/* 這次實際會用哪些模型（2026-09-21 真瀏覽器：精修可以是 Breeze／Qwen／ElevenLabs，按鈕卻一直寫 WhisperX） */}
              <span className="muted transcribe-plan" aria-label="這次轉錄會用">
                {profile === "draft"
                  ? "只做草稿：WhisperX turbo（不精修）"
                  : `草稿 WhisperX turbo → 精修 ${asrModelName(effectiveAsrModel)}${
                      asrModels.find((m) => m.key === effectiveAsrModel)?.remote
                        ? `（音訊會送到 ${asrModels.find((m) => m.key === effectiveAsrModel)?.provider_label ?? "OpenRouter"}）`
                        : "（本機）"
                    }`}
              </span>
              <span className="muted transcribe-state" role="status">
                {transcribing
                  ? `轉錄中：${transcribeStage[transcribeJob!.stage || ""] || transcribeJob!.stage || "處理中"}`
                  : transcribeJob?.status === "failed"
                    ? `轉錄失敗：${transcribeJob.error?.message || transcribeJob.error?.code || "未知原因"}`
                    : transcript
                      ? `逐字稿已載入：${transcript.cues.length} 句，可到下方「AI 分析、摘要與校字」處理`
                      : asset
                        ? "素材已就緒，按「轉錄」交給 WhisperX 產生帶時間軸的逐字稿"
                        : ""}
              </span>
            </div>
          </div>
            <TermsField
              className="asr-hints-field"
              label="轉錄術語提示（給轉錄模型）"
              ariaLabel="轉錄術語提示"
              importAriaLabel="匯入術語文字檔"
              value={asrHints}
              onChange={setAsrHints}
              onImport={(text) => setAsrHints(mergeTerms(asrHints, text))}
              placeholder="人名、作品名，用逗號分隔；也可以貼一段介紹，再按「內容拆解單詞」整理成關鍵詞"
              maxLength={ASR_HINTS_MAX}
              actions={
                <button type="button" disabled={!asrHints.trim() || splittingKeywords} onClick={() => void splitKeywords()}>
                  {splittingKeywords ? "拆解中…" : "內容拆解單詞"}
                </button>
              }
              status={keywordStatus || providerHint}
              help={
                <details className="terms-help">
                  <summary>怎麼送進轉錄模型？</summary>
                  <p>
                    按「轉錄」時，這些詞整理成「詞, 詞」一起送出。Whisper（turbo 草稿、large-v3、Breeze-ASR-25）把它當提示詞（hotwords），Qwen3-ASR
                    把它當 context，讓專有名詞照這個寫法。Whisper 的提示詞大約 200 字以內有效，太長的部分會被截掉；OpenRouter
                    遠端轉錄沒有提示詞欄位，不會送。「內容拆解單詞」用「AI 分析、摘要與校字」選的文字模型挑出專有名詞；沒選模型或模型無法使用時改用本機規則，並在旁邊註明。
                  </p>
                </details>
              }
            />
            <div className="subtitle-export" aria-label="字幕輸出">
              <h3>字幕輸出</h3>
              <p className="muted">
                輸出位置：匯入的本機檔預設放在同一個資料夾的 subtitle_studio；YouTube 下載的預設放在專案資料目錄（可從網頁直接下載）。自己選資料夾時，放進那個資料夾裡的 subtitle_studio。檔名跟著素材檔名：SRT、逐字稿
                JSON、修改紀錄 JSON、匯出清單，每次輸出覆寫成最新。
              </p>
              <div className="subtitle-export-controls">
                <label>
                  每則字幕
                  <select
                    aria-label="每則字幕句數"
                    value={subtitleSettings.sentences_per_cue}
                    onChange={(e) =>
                      setSubtitleSettings({
                        ...subtitleSettings,
                        sentences_per_cue: Number(e.target.value) as 1 | 2,
                      })
                    }
                  >
                    <option value="1">1 句</option>
                    <option value="2">2 句</option>
                  </select>
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    aria-label="保留標點"
                    checked={subtitleSettings.preserve_punctuation}
                    onChange={(e) =>
                      setSubtitleSettings({
                        ...subtitleSettings,
                        preserve_punctuation: e.target.checked,
                      })
                    }
                  />
                  保留標點
                </label>
                <label className="check">
                  <input type="checkbox" checked={autoOutput} onChange={(e) => setAutoOutput(e.target.checked)} />
                  轉錄完成後自動輸出 SRT 與紀錄
                </label>
                <OutputRootPicker
                  label="字幕輸出"
                  roots={outputRoots}
                  value={effectiveExportRoot}
                  onChange={setExportRootId}
                  addRoot={addOutputRoot}
                  pickFolder={api.pickFolder}
                />
              </div>
              <div className="subtitle-export-actions">
                <button disabled={!transcript || !asset || busy} onClick={() => run(exportSubtitles)}>
                  <Download size={17} />
                  匯出 SRT
                </button>
                <button disabled={!transcript || busy} onClick={() => run(exportHistory)} title="從 WhisperX 輸出到現在的每一步修改（逐句編輯、LLM 套用、還原）">
                  匯出修改紀錄（JSON）
                </button>
                <span className="muted srt-timing-hint">
                  {transcript && !alignmentRevision
                    ? alignFailed
                      ? "逐詞對齊失敗：匯出時會再試一次，仍失敗才用句子時間"
                      : "這一版還沒逐詞對齊：匯出會先等逐詞對齊完成"
                    : ""}
                </span>
              </div>
              <ExportStatus jobs={jobs} filter={(job) => !!(job.body?.formats as string[] | undefined)?.every((f) => f === "srt")} />
            </div>
          </CollapsibleSection>
        <CollapsibleSection id="ai" title="AI 分析、摘要與校字" hint="辨識品質、精修引擎、詞彙提示、校字與剪輯提案" className="ai-section">
            <div className="analysis-actions">
              <button
                disabled={!transcript || busy}
                onClick={() => submitAnalysis("summarize")}
              >
                產生摘要
              </button>
              <button
                disabled={!transcript || !provider || busy}
                title={!provider ? "先在下方選擇校字與摘要模型" : undefined}
                onClick={() => submitAnalysis("correct")}
              >
                {correctionMode === "rewrite"
                  ? autoApply
                    ? "依上下文逐句改寫並直接套用"
                    : "依上下文逐句改寫（產生提案）"
                  : autoApply
                    ? "依上下文校字並直接套用"
                    : "依上下文校字（產生提案）"}
              </button>
              <label className="check">
                校字強度
                <select aria-label="校字強度" value={correctionMode} onChange={(e) => setCorrectionMode(e.target.value as "conservative" | "rewrite")}>
                  <option value="conservative">保守：只改明顯錯字</option>
                  <option value="rewrite">積極：依上下文逐句改寫</option>
                </select>
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={autoApply}
                  onChange={(e) => setAutoApply(e.target.checked)}
                />
                校字結果直接套用（可還原）
              </label>
              <span className="muted correction-state" role="status">
                {correctionText || (correctRequested ? "校字已送出，等模型回覆…" : "")}
              </span>
              <span className="muted alignment-state" role="status">
                {!transcript
                  ? ""
                  : aligning
                    ? "字幕時間：逐詞對齊中…（轉錄或修改後自動進行）"
                    : alignmentRevision
                      ? "字幕時間：已逐詞對齊"
                      : alignFailed
                        ? "字幕時間：逐詞對齊失敗，匯出將用句子時間"
                        : "字幕時間：等待逐詞對齊…"}
              </span>
              <span className="muted summary-state" role="status">
                {summarizing
                  ? "摘要產生中…"
                  : summaryJob?.status === "succeeded"
                    ? `摘要完成：${annotations.length} 項，見下方「摘要與重點」分頁`
                    : summaryJob?.status === "failed"
                      ? `摘要失敗：${summaryJob.error?.message || summaryJob.error?.code || ""}`
                      : ""}
              </span>
            </div>
            <div className="ai-controls">
              <label>
                校字與摘要模型
                <select
                  aria-label="校字與摘要模型"
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                >
                  <option value="">抽取式摘要（不使用文字模型）</option>
                  {MODEL_PORTS.filter((port) => providers.some((p) => p.id === port.id)).map((port) => (
                    <option key={port.id} value={port.id}>
                      {port.label}
                    </option>
                  ))}
                </select>
                <div className="provider-check">
                  <button type="button" disabled={!provider || probing} onClick={() => void testProvider()}>
                    {probing ? "測試中…" : "測試連線"}
                  </button>
                  <span className="muted" role="status">
                    {probeText || providerState}
                  </span>
                </div>
                <small className="muted">
                  校字、摘要、內容拆解單詞與即時翻譯都用這個模型。本機兩個跟著你在 LM Studio／llama.cpp 目前載入的模型；OpenRouter 的金鑰與模型在右上角齒輪「模型設定」填。
                </small>
              </label>
              <TermsField
                className="glossary-field"
                label="詞彙提示（校字用）"
                ariaLabel="詞彙提示"
                importAriaLabel="匯入詞彙文字檔"
                value={glossaryText}
                onChange={setGlossaryText}
                onImport={(text) => setGlossaryText(mergeTerms(glossaryText, text))}
                placeholder="人名、作品名、常見錯字的正確寫法；用逗號、頓號、換行或空格分隔"
                status={glossaryText.trim() ? `${glossaryTerms(glossaryText).length} 個詞` : ""}
                help={
                  <details className="terms-help">
                    <summary>詞彙與參考資料怎麼送進模型？</summary>
                    <p>
                      按「依上下文校字」時，逐字稿分段送給上面選的文字模型（本機 LM Studio 或遠端 API 都一樣），每一段都附上影片標題（context.title）、這裡的詞彙（context.glossary）與下面的參考資料（context.reference）。模型只用它們判斷詞彙的正確寫法，不會把參考資料的句子加進字幕。修正用到詞彙或參考資料裡的詞，會標為高信心並直接套用。參考資料最多
                      6000 字，超過文字模型上下文預算一半的部分會截掉，並在校字結果註明。
                    </p>
                  </details>
                }
              />
              <TermsField
                className="reference-field"
                label="校字參考資料（故事大綱、角色表）"
                ariaLabel="校字參考資料"
                importAriaLabel="匯入參考資料文字檔"
                rows={3}
                value={referenceText}
                onChange={setReferenceText}
                onImport={(text) => setReferenceText(text.trim().slice(0, REFERENCE_MAX))}
                placeholder="貼上這一集的大綱、角色與專有名詞；校字時模型會參考裡面的用詞（只存在這個專案）"
                maxLength={REFERENCE_MAX}
              />
              <div className="ai-intent">
                <input
                  aria-label="AI 剪輯需求"
                  value={intent}
                  onChange={(e) => setIntent(e.target.value)}
                />
                <button
                  disabled={!transcript || busy}
                  onClick={() =>
                    run(async () => {
                      const seq = dirty ? await save() : sequence;
                      await api.job(project, {
                        kind: "plan_edits",
                        transcript_revision: transcript!.revision,
                        base_sequence_revision: seq.revision,
                        map_revision: transcript!.map_revision,
                        intent,
                        provider_id: provider || null,
                        target_duration_us: 180000000,
                      });
                      await refresh();
                    })
                  }
                >
                  剪輯提案
                </button>
              </div>
            </div>
        </CollapsibleSection>
        <CollapsibleSection id="realtime" title="即時字幕與翻譯（實驗）" hint="麥克風或播放中的素材 → 即時字幕，可同時翻譯；停止後可下載 SRT" className="realtime-section">
          <RealtimePanel providers={providers} provider={provider} remoteConsent={remoteConsent} openSource={realtimeSource} />
        </CollapsibleSection>
        </div>
        {/* 兩頁共用：素材、預覽與素材時間軸（只有一個播放器，切頁不重載） */}
        <section className={asset ? "media-stage" : "media-stage is-empty"}>
        <div className="workspace">
          <aside className="sources-panel">
            <div className="section-heading">
              <h2>素材</h2>
              <span className="count">{sources.length}</span>
            </div>
            {sources.length ? (
              sources.map((s) => (
                <div className="source-group" key={s.source_id}>
                  <button
                    className={`source-item ${sourceId === s.source_id ? "selected" : ""}`}
                    onClick={() => {
                      if (!selectSource(s.source_id)) return;
                      setAsset(
                        assets.find((a) => a.source_id === s.source_id) || null,
                      );
                    }}
                  >
                    <Film size={18} />
                    <span>
                      {s.title || s.url || "本機素材"}
                      <small>
                        {s.duration_us
                          ? formatTimecode(s.duration_us)
                          : "等待來源資訊"}
                      </small>
                    </span>
                  </button>
                  {assets
                    .filter((a) => a.source_id === s.source_id)
                    .map((a) => (
                      <button
                        className={`asset-item ${asset?.asset_id === a.asset_id ? "selected" : ""}`}
                        key={a.asset_id}
                        onClick={() => {
                          if (selectSource(s.source_id)) setAsset(a);
                        }}
                      >
                        <Music2 size={14} />
                        {a.asset_kind || a.kind || "影音"}{" "}
                        {formatTimecode(a.duration_us)}
                      </button>
                    ))}
                </div>
              ))
            ) : (
              <div className="empty small">
                <p>先建立專案，再貼上連結或匯入影音。</p>
              </div>
            )}
          </aside>
          <section className="preview-panel">
            <div className="preview-heading">
              <span>{currentSource?.title || "素材預覽"}</span>
              <span className="muted">
                {asset ? "來源時間" : "尚未選取素材"}
              </span>
            </div>
            {asset ? (
              <video
                ref={player}
                controls
                src={
                  asset.content_url || `/v1/assets/${asset.asset_id}/content`
                }
                onLoadedMetadata={() => {
                  const d = Math.round((player.current?.duration || 0) * 1e6);
                  setDuration(d + offset);
                  setInPoint(null);
                  setOutPoint(null);
                  setTime(offset);
                }}
                onTimeUpdate={() => {
                  const media = player.current;
                  if (!media) return;
                  const next = Math.round((media.currentTime || 0) * 1e6) + offset;
                  const previous = lastPlayhead.current;
                  lastPlayhead.current = next;
                  if (
                    ioValid &&
                    !media.paused &&
                    previous != null &&
                    previous >= inPoint! &&
                    previous < outPoint! &&
                    next >= outPoint! &&
                    next - previous < 1_000_000
                  ) {
                    // 在入出點範圍內播放、碰到出點：暫停並停在出點
                    media.pause();
                    media.currentTime = (outPoint! - offset) / 1e6;
                    lastPlayhead.current = outPoint!;
                    setTime(outPoint!);
                    return;
                  }
                  setTime(next);
                }}
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
                onError={() =>
                  setError(
                    "瀏覽器無法播放此格式。請取得相容 MP4 預覽素材，或先使用音訊。",
                  )
                }
              />
            ) : (
              <div className="preview-empty">
                <Film size={42} strokeWidth={1} />
                <h2>讓片段說重點</h2>
                <p>在最上方貼上連結按「加入下載」，或按「匯入本機檔案」；只抓一段就先在「① 下載與剪輯 → 下載設定」填下載範圍。兩頁共用這個預覽與時間軸。</p>
              </div>
            )}
            {/* 目前字幕：就是匯出 SRT 這個時間點的那一則 */}
            {currentSubtitle ? (
              <div className="subtitle-now" aria-label="目前字幕">
                {currentSubtitle.text}
              </div>
            ) : null}
          </section>
        </div>
        <Timeline
          duration={asset ? offset + asset.duration_us : sourceDuration}
          startUs={offset}
          time={time}
          items={editor.items}
          active={active}
          assetId={asset?.asset_id}
          seek={seek}
          select={setActive}
          boundary={(id, k, t) =>
            change(
              editor.items.map((s) =>
                s.id === id ? updateBoundary(s, k, t, sourceDuration) : s,
              ),
            )
          }
          inUs={asset && ioValid ? inPoint : null}
          outUs={asset && ioValid ? outPoint : null}
          move={(id, a, b) => change(editor.items.map((s) => (s.id === id ? { ...s, start_us: a, end_us: b } : s)))}
          onRange={(a, b) => {
            setInPoint(a);
            setOutPoint(b);
          }}
          onClearRange={clearInOut}
          remove={(id) => change(editor.items.filter((s) => s.id !== id))}
        />
        </section>
        {/* 字幕頁中排右側：逐字稿（左側是共用的預覽與時間軸） */}
        <div className="page page-transcript" data-page="subtitles" hidden={page !== "subtitles"}>
        <div className="subtitle-workspace">
          <TranscriptPanel
            classifications={classifications}
            overrideClassification={overrideClassification}
            transcript={transcript}
            annotations={annotations}
            seek={seek}
            edit={editCue}
            timeBase={offset}
            currentUs={asset ? time : null}
            tabRequest={tabRequest}
            subtitlePreview={subtitlePreview}
            subtitlePreviewError={subtitlePreviewError}
            corrections={correctedCues}
            audit={auditPoint}
          />
        </div>
        </div>
        <div className="proposals-column" data-page="subtitles" hidden={page !== "subtitles"}>
        <ProposalPanel
          jobs={jobs}
          seek={seek}
          run={(fn) => run(fn)}
          timeBase={offset}
          applied={appliedView}
          revertCorrection={revertCorrection}
          acceptCorrection={async (p, replacement) => {
            if (
              !transcript ||
              (p.base_revision && p.base_revision !== transcript.revision)
            )
              throw Error("校字提案已過期，請重新產生");
            const cue = transcript.cues.find((c) => c.cue_id === p.cue_id);
            if (!cue || cue.text !== p.original_text)
              throw Error("原文已變更，請重新產生校字提案");
            const t = await api.edits(project, transcript.revision, [
              { cue_id: p.cue_id, text: (replacement ?? p.replacement_text).trim() || p.replacement_text },
            ]);
            setTranscript(t);
            loadedRevision.current = t.revision;
          }}
          acceptProposal={async (p) => {
            if (dirty) throw Error("請先儲存目前剪輯，再重新產生提案");
            if (
              p.base_transcript_revision !== transcript?.revision ||
              p.base_sequence_revision !== sequence.revision
            )
              throw Error("剪輯或文字已更新，請重新產生提案");
            const items = p.items.flatMap((item) =>
              item.source_spans.map((span) =>
                createSegment({
                  ...span,
                  name: item.name,
                  tags: { cue_ids: item.cue_ids.join(",") },
                }),
              ),
            );
            const result = await request<Sequence>(
              `/projects/${project}/sequence`,
              {
                method: "PUT",
                body: JSON.stringify({
                  base_revision: sequence.revision,
                  source_id: sourceId,
                  items,
                  proposal_id: p.proposal_id,
                  base_transcript_revision: p.base_transcript_revision,
                  map_revision: p.map_revision,
                }),
              },
            );
            setSequence(result);
            editor.set(items);
            setDirty(false);
          }}
        />
        </div>
        <div className="jobs-area">
        <JobList
          jobs={jobs}
          run={(fn) =>
            run(async () => {
              await fn();
              await refresh();
            })
          }
        />
        </div>
      </main>
      <footer>
        冬比字幕工作室 <span>空白鍵 播放／暫停 · I/O 入出點 · Ctrl+Z 復原</span>
        <span>{busy ? "處理請求中…" : "原始素材保持完整"}</span>
      </footer>
    </>
  );
}
