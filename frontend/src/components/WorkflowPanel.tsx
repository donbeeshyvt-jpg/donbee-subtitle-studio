import { useEffect, useState } from "react";
import { request } from "../api/client";
import { formatTimecode, parseRanges } from "../domain/time";
import AnalysisOptions, {
  defaultAnalysisPolicies,
  type AnalysisPolicies,
} from "./AnalysisOptions";
type Settings = AnalysisPolicies & {
  analysis: "none" | "draft" | "balanced" | "quality";
  engine: "whisperx" | "vibevoice";
  video: boolean;
  audio: boolean;
  summary: boolean;
  subtitles: boolean;
  subtitlePolicy: "none" | "generate" | "source" | "compare";
  sourceLanguage: string;
  sourceKind: "prefer_manual" | "manual" | "automatic";
  alignment: "allow_segment" | "require_word";
  outputRootId: string;
  sentences: 1 | 2;
  punctuation: boolean;
  grouping: "separate" | "merge";
  accurate: boolean;
};
interface Plan {
  plan_id: string;
  dependency_graph: string[];
  ranges?: { start_us: number; end_us: number }[];
  deliverables?: { formats: string[] };
}
export default function WorkflowPanel({
  project,
  sourceId,
  ranges,
  provider,
  sourceSubtitlesEnabled,
  outputRoots,
  registerSource,
  run,
}: {
  project: string;
  sourceId: string;
  ranges: string;
  provider: string;
  sourceSubtitlesEnabled: boolean;
  outputRoots: { id: string; name: string }[];
  // 尚無素材但已貼連結：由 App 提供「只登記來源、不下載」的方法，讓快照可直接建立
  registerSource?: (() => Promise<string>) | null;
  run: (fn: () => Promise<unknown>) => void;
}) {
  const [settings, setSettings] = useState<Settings>({
    ...defaultAnalysisPolicies,
    analysis: "draft",
    engine: "whisperx",
    video: true,
    audio: false,
    summary: false,
    subtitles: true,
    subtitlePolicy: "generate",
    sourceLanguage: "auto",
    sourceKind: "prefer_manual",
    alignment: "allow_segment",
    outputRootId: "",
    sentences: 1,
    punctuation: false,
    grouping: "merge",
    accurate: true,
  });
  const [fullSource, setFullSource] = useState(false);
  useEffect(() => setFullSource(false), [sourceId, project]);
  const [plans, setPlans] = useState<Plan[]>([]),
    [presets, setPresets] = useState<
      { id: string; name: string; settings: Settings }[]
    >([]),
    [name, setName] = useState("我的流程");
  useEffect(() => {
    if (!project) return;
    request<{ items: Plan[] }>(`/projects/${project}/plans`)
      .then((r) => setPlans(r.items))
      .catch(() => {});
    request<{ items: typeof presets }>("/presets")
      .then((r) => setPresets(r.items))
      .catch(() => {});
  }, [project]);
  const change = <K extends keyof Settings>(key: K, value: Settings[K]) =>
    setSettings((s) => ({ ...s, [key]: value }));
  return (
    <details className="workflow-panel">
      <summary>
        整合工作流程與預設組{" "}
        <span className="muted">音訊先行、字幕、摘要與交付檔案</span>
      </summary>
      <p>流程範圍：{fullSource ? "完整來源" : ranges.trim() || "尚未填寫，請在上方設定下載範圍"}</p>
      <label className="check">
        <input type="checkbox" checked={fullSource} onChange={(e) => setFullSource(e.target.checked)} />
        使用完整來源
      </label>
      <div className="download-options">
        <AnalysisOptions
          prefix="流程"
          value={settings}
          change={(policies) => setSettings((s) => ({ ...s, ...policies }))}
        />
        <label>
          分析
          <select
            value={settings.analysis}
            onChange={(e) =>
              change("analysis", e.target.value as Settings["analysis"])
            }
          >
            <option value="none">不分析</option>
            <option value="draft">快速草稿</option>
            <option value="balanced">平衡：草稿＋選段精修</option>
            <option value="quality">精修：草稿＋全段精修</option>
          </select>
        </label>
        <label>
          精修引擎
          <select
            value={settings.engine}
            onChange={(e) =>
              change("engine", e.target.value as Settings["engine"])
            }
          >
            <option value="whisperx">WhisperX large-v3（預設，選段精修）</option>
            <option value="vibevoice">VibeVoice（可選，約 11.5 GB VRAM，較慢，失敗退回 WhisperX）</option>
          </select>
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={settings.video}
            onChange={(e) => change("video", e.target.checked)}
          />
          取得選段影片
        </label>
        <label className="check">
          <input type="checkbox" checked={settings.audio} onChange={(e) => change("audio", e.target.checked)} />
          輸出音檔
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={settings.summary}
            onChange={(e) => change("summary", e.target.checked)}
          />
          產生摘要
        </label>
        <label>
          字幕方式
          <select
            value={settings.subtitlePolicy}
            onChange={(e) =>
              setSettings((s) => ({
                ...s,
                subtitlePolicy: e.target.value as Settings["subtitlePolicy"],
                alignment:
                  e.target.value === "source" ? "allow_segment" : s.alignment,
              }))
            }
          >
            <option value="none">不輸出字幕</option>
            <option value="generate">辨識生成字幕</option>
            <option value="source" disabled={!sourceSubtitlesEnabled}>
              使用來源字幕
            </option>
            <option value="compare" disabled={!sourceSubtitlesEnabled}>
              比對來源與辨識字幕
            </option>
          </select>
        </label>
        {["source", "compare"].includes(settings.subtitlePolicy) && (
          <>
            <label>
              來源字幕語言
              <select
                value={settings.sourceLanguage}
                onChange={(e) => change("sourceLanguage", e.target.value)}
              >
                <option value="auto">自動選擇可用語言</option>
                <option value="zh-Hant">繁體中文</option>
                <option value="zh">中文</option>
                <option value="ja">日文</option>
                <option value="en">英文</option>
              </select>
            </label>
            <label>
              來源字幕種類
              <select
                value={settings.sourceKind}
                onChange={(e) =>
                  change("sourceKind", e.target.value as Settings["sourceKind"])
                }
              >
                <option value="prefer_manual">優先人工字幕</option>
                <option value="manual">只用人工字幕</option>
                <option value="automatic">只用自動字幕</option>
              </select>
            </label>
          </>
        )}
        <label>
          字幕對齊
          <select
            value={settings.alignment}
            disabled={settings.subtitlePolicy === "none"}
            onChange={(e) =>
              change("alignment", e.target.value as Settings["alignment"])
            }
          >
            <option value="allow_segment">允許分段時間</option>
            <option
              value="require_word"
              disabled={settings.subtitlePolicy === "source"}
            >
              要求逐詞對齊
            </option>
          </select>
        </label>
        <label>
          流程輸出位置
          <select
            value={settings.outputRootId}
            onChange={(e) => change("outputRootId", e.target.value)}
          >
            <option value="">專案資料目錄</option>
            {outputRoots.map((root) => (
              <option key={root.id} value={root.id}>
                {root.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          字幕句數
          <select
            value={settings.sentences}
            onChange={(e) =>
              change("sentences", Number(e.target.value) as 1 | 2)
            }
          >
            <option value="1">1 句</option>
            <option value="2">2 句</option>
          </select>
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={settings.punctuation}
            onChange={(e) => change("punctuation", e.target.checked)}
          />
          保留標點
        </label>
        <label>
          交付方式
          <select
            value={settings.grouping}
            onChange={(e) =>
              change("grouping", e.target.value as Settings["grouping"])
            }
          >
            <option value="separate">分段輸出</option>
            <option value="merge">合併輸出</option>
          </select>
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={settings.accurate}
            onChange={(e) => change("accurate", e.target.checked)}
          />
          精準重編碼
        </label>
      </div>
      {!sourceSubtitlesEnabled && (
        <p className="muted">服務尚未啟用來源字幕，來源與比對模式暫不可用。</p>
      )}
      {settings.subtitlePolicy === "source" && (
        <p className="muted">來源字幕只有分段時間，無法要求逐詞對齊。</p>
      )}
      {settings.subtitlePolicy === "compare" && (
        <p className="muted">
          比對會以辨識字幕進行對齊，來源字幕另行保留，不自動覆寫原稿。
        </p>
      )}
      {!sourceId && !registerSource && (
        <p className="muted">請先貼上來源連結或選擇素材，才能建立流程快照。</p>
      )}
      <div className="workflow-actions">
        <button
          disabled={!project || (!sourceId && !registerSource) || (!fullSource && !ranges.trim())}
          onClick={() =>
            run(async () => {
              const resolvedSource = sourceId || (registerSource ? await registerSource() : "");
              if (!resolvedSource) throw Error("請先貼上來源連結或選擇素材");
              if (
                ["source", "compare"].includes(settings.subtitlePolicy) &&
                !sourceSubtitlesEnabled
              )
                throw Error("服務尚未啟用來源字幕");
              if (
                settings.outputRootId &&
                !outputRoots.some((root) => root.id === settings.outputRootId)
              )
                throw Error("此預設的輸出位置目前不在允許清單，請重新選擇");
              if (
                settings.analysis === "none" &&
                (settings.summary ||
                  ["generate", "compare"].includes(settings.subtitlePolicy))
              )
                throw Error("產生摘要、辨識字幕或比對字幕需要啟用分析");
              const body = {
                source_id: resolvedSource,
                ranges: fullSource ? [] : parseRanges(ranges),
                acquisition: {
                  ...(settings.outputRootId
                    ? { output_root_id: settings.outputRootId }
                    : {}),
                  audio_first: true,
                  video: settings.video ? "selected" : "none",
                  quality: "source",
                  // 與「加入下載」相同的邊界策略（精準起點），流程執行時才能重用已下載的素材
                  boundary_policy: "accurate",
                },
                analysis: {
                  language_policy: settings.language_policy,
                  music_policy: settings.music_policy,
                  mode: settings.analysis,
                  engine: settings.engine,
                  fallback_engine: settings.engine === "vibevoice" ? "whisperx" : null,
                  summary: settings.summary,
                  provider_id: provider || null,
                },
                subtitles: {
                  policy: settings.subtitlePolicy,
                  source_language: settings.sourceLanguage,
                  source_kind: settings.sourceKind,
                  alignment:
                    settings.subtitlePolicy === "source"
                      ? "allow_segment"
                      : settings.alignment,
                  sentences_per_cue: settings.sentences,
                  keep_punctuation: settings.punctuation,
                },
                deliverables: {
                  ...(settings.outputRootId
                    ? { output_root_id: settings.outputRootId }
                    : {}),
                  grouping: settings.grouping,
                  formats: [
                    ...(settings.video ? ["mp4"] : []),
                    ...(settings.audio ? ["audio"] : []),
                    ...(settings.subtitlePolicy !== "none" ? ["srt"] : []),
                    ...(settings.summary ? ["summary_md"] : []),
                  ],
                  cut_mode: settings.accurate ? "accurate" : "copy",
                },
              };
              const p = await request<Plan>(`/projects/${project}/plans`, {
                method: "POST",
                body: JSON.stringify(body),
              });
              setPlans((v) => [{ ...p, ranges: body.ranges, deliverables: body.deliverables }, ...v]);
            })
          }
        >
          建立流程快照
        </button>
        <input
          aria-label="預設組名稱"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button
          onClick={() =>
            run(async () => {
              const p = await request<(typeof presets)[number]>("/presets", {
                method: "POST",
                body: JSON.stringify({ name, settings }),
              });
              setPresets((v) => [...v, p]);
            })
          }
        >
          儲存預設組
        </button>
        <select
          aria-label="套用預設組"
          value=""
          onChange={(e) => {
            const p = presets.find((p) => p.id === e.target.value);
            if (p)
              setSettings({
                ...defaultAnalysisPolicies,
                ...p.settings,
                audio: p.settings.audio ?? false,
                subtitlePolicy:
                  p.settings.subtitlePolicy ??
                  (p.settings.subtitles ? "generate" : "none"),
                sourceLanguage: p.settings.sourceLanguage ?? "auto",
                sourceKind: p.settings.sourceKind ?? "prefer_manual",
                alignment:
                  p.settings.subtitlePolicy === "source"
                    ? "allow_segment"
                    : (p.settings.alignment ?? "allow_segment"),
                outputRootId: p.settings.outputRootId ?? "",
              });
          }}
        >
          <option value="">套用預設組</option>
          {presets.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      {!sourceId && (
        <p className="muted">
          請先加入來源，再使用上方已填寫的下載範圍建立流程。
        </p>
      )}
      {plans.map((p, i) => (
        <div key={p.plan_id} className="workflow-plan">
          <span>流程 {plans.length - i} · 設定快照已儲存</span>
          <span>
            範圍：{p.ranges ? (p.ranges.length ? p.ranges.map((r) => `${formatTimecode(r.start_us)}–${formatTimecode(r.end_us)}`).join("；") : "完整來源") : "未提供"}
            {" · "}輸出：{p.deliverables?.formats.join("、") || "未指定交付格式"}
          </span>
          <button
            onClick={() =>
              run(() =>
                request(`/plans/${p.plan_id}/run`, {
                  method: "POST",
                  body: "{}",
                }),
              )
            }
          >
            執行此流程
          </button>
        </div>
      ))}
    </details>
  );
}
