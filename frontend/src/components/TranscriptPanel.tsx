import { memo, useEffect, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import type {
  Transcript,
  Cue,
  Annotation,
  Classification,
  ClassificationSpan,
  AudioLabel,
} from "../api/types";
import ClassificationPanel from "./ClassificationPanel";
import SubtitlePreviewList from "./SubtitlePreviewList";
import type { SubtitlePreview } from "../api/types";
import { formatTimecode } from "../domain/time";
// 辨識端標出的旗標：是提示不是待辦（使用者 2026-09-20：「那些又是做甚麼用的，根本沒地方套用」）。
// 純資訊的（VAD 保留不確定音訊，幾乎每句都有）不顯示。
const FLAG_TEXT: Record<string, string> = {
  possible_hallucination: "可能是辨識幻聽，請對照音訊",
  low_log_probability: "辨識信心較低",
  repetition: "重複字較多",
  mixed_language: "中外文混雜",
  draft_gap_filled: "草稿漏掉後補轉的句子",
  unknown_word_time: "部分字沒有時間",
};
const flagHints = (flags?: string[]) => (flags || []).map((f) => FLAG_TEXT[f]).filter(Boolean);
function TranscriptPanel({
  transcript,
  annotations,
  seek,
  edit,
  classifications,
  overrideClassification,
  timeBase = 0,
  currentUs = null,
  tabRequest = null,
  subtitlePreview = null,
  subtitlePreviewError = "",
  corrections = {},
  audit,
}: {
  transcript: Transcript | null;
  annotations: Annotation[];
  seek: (n: number) => void;
  edit: (cue: Cue, text: string) => Promise<void>;
  classifications: Classification[];
  overrideClassification: (
    c: Classification,
    span: ClassificationSpan,
    label: AudioLabel,
  ) => Promise<void>;
  // 素材在來源中的起點：卡片顯示「素材相對時間」；目前播放位置（來源時間）用來標示與捲動
  timeBase?: number;
  currentUs?: number | null;
  // 外部要求切分頁（例如摘要完成後切到「摘要與重點」）；nonce 變了才切
  tabRequest?: { tab: string; nonce: number } | null;
  // SRT 字幕預覽（與匯出 SRT 同一段計算）
  subtitlePreview?: SubtitlePreview | null;
  subtitlePreviewError?: string;
  // 被校字改過的句子：cue_id → 原文（顯示在該句下方，看得出改了什麼）
  corrections?: Record<string, string>;
  // 入出點抽查（M4-C5）：只播這個時間點前後各 0.5 秒
  audit?: (us: number) => void;
}) {
  const [query, setQuery] = useState(""),
    [tab, setTab] = useState("transcript"),
    [editing, setEditing] = useState(""),
    [text, setText] = useState(""),
    [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const cues = (transcript?.cues || []).filter((c) => c.text.includes(query));
  const virtual = useVirtualizer({
    count: cues.length,
    getScrollElement: () => ref.current,
    estimateSize: () => 94,
    overscan: 5,
  });
  // 播放中：目前句（含播放位置的句子，否則是播放位置之前最後一句）標示並捲到可視範圍；編輯中不捲動
  const currentIndex =
    currentUs == null
      ? -1
      : cues.findIndex((c) => c.start_us != null && c.end_us != null && c.start_us <= currentUs && currentUs < c.end_us);
  const currentId = currentIndex >= 0 ? cues[currentIndex].cue_id : "";
  useEffect(() => {
    if (tabRequest) setTab(tabRequest.tab);
  }, [tabRequest]);
  useEffect(() => {
    if (currentIndex < 0 || editing || tab !== "transcript") return;
    try {
      virtual.scrollToIndex(currentIndex, { align: "auto" });
    } catch {
      /* 測試環境沒有版面 */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentId]);
  return (
    <aside className="transcript-panel">
      <div className="tabs">
        <button
          className={tab === "transcript" ? "selected" : ""}
          onClick={() => setTab("transcript")}
        >
          逐字稿
        </button>
        <button
          className={tab === "srt" ? "selected" : ""}
          onClick={() => setTab("srt")}
        >
          SRT 字幕預覽
        </button>
        <button
          className={tab === "summary" ? "selected" : ""}
          onClick={() => setTab("summary")}
        >
          摘要與重點
        </button>
        <button
          className={tab === "classification" ? "selected" : ""}
          onClick={() => setTab("classification")}
        >
          音訊區段
        </button>
      </div>
      {tab === "transcript" ? (
        <>
          <input
            aria-label="搜尋逐字稿"
            placeholder="搜尋字句…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <div className="transcript-scroll" ref={ref}>
            {!cues.length ? (
              <div className="empty">
                <h3>{transcript ? "沒有符合的字句" : "從音訊開始理解"}</h3>
                <p>
                  音訊就緒後，執行快速草稿。點時間碼可回播，文字可逐句修正。
                </p>
              </div>
            ) : (
              <div
                style={{ height: virtual.getTotalSize(), position: "relative" }}
              >
                {virtual.getVirtualItems().map((row) => {
                  const cue = cues[row.index];
                  return (
                    <article
                      key={cue.cue_id}
                      ref={virtual.measureElement}
                      data-index={row.index}
                      className={cue.cue_id === currentId ? "cue is-current" : "cue"}
                      onClick={(e) => {
                        // 整張卡片可點：跳到該句（按鈕、文字框各自處理，不重複觸發）
                        if ((e.target as HTMLElement).closest("button, textarea, input")) return;
                        if (cue.start_us != null) seek(cue.start_us);
                      }}
                      style={{
                        position: "absolute",
                        width: "100%",
                        top: 0,
                        transform: `translateY(${row.start}px)`,
                      }}
                    >
                      <div className="cue-heading">
                        <button
                          className="text-button timecode"
                          disabled={cue.start_us == null}
                          onClick={() => seek(cue.start_us!)}
                        >
                          {cue.start_us == null
                            ? "時間待確認"
                            : formatTimecode(cue.start_us, timeBase)}
                        </button>
                        <button
                          className="text-button"
                          onClick={() => {
                            setEditing(cue.cue_id);
                            setText(cue.text);
                          }}
                        >
                          編輯
                        </button>
                      </div>
                      {editing === cue.cue_id ? (
                        <>
                          <textarea
                            aria-label="修改字幕文字"
                            value={text}
                            onChange={(e) => setText(e.target.value)}
                          />
                          <button
                            disabled={busy}
                            onClick={async () => {
                              setBusy(true);
                              try {
                                await edit(cue, text);
                                setEditing("");
                              } catch {
                                // 呼叫端已顯示錯誤，保留編輯中的文字供重試。
                              } finally {
                                setBusy(false);
                              }
                            }}
                          >
                            儲存修改
                          </button>
                          <button onClick={() => setEditing("")}>取消</button>
                        </>
                      ) : (
                        <p>{cue.text}</p>
                      )}
                      {corrections[cue.cue_id] && corrections[cue.cue_id] !== cue.text ? (
                        <small className="muted corrected-note">原：{corrections[cue.cue_id]}</small>
                      ) : null}
                      {flagHints(cue.review_flags).length ? (
                        <small className="muted">提示：{flagHints(cue.review_flags).join("、")}</small>
                      ) : null}
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        </>
      ) : tab === "srt" ? (
        <SubtitlePreviewList preview={subtitlePreview} error={subtitlePreviewError} currentUs={currentUs} seek={seek} audit={audit} />
      ) : tab === "classification" ? (
        <ClassificationPanel
          classifications={classifications}
          seek={seek}
          override={overrideClassification}
          timeBase={timeBase}
        />
      ) : (
        <div className="transcript-scroll">
          {annotations.length ? (
            annotations.map((a) => (
              <article className="cue" key={a.id}>
                <small>
                  {(
                    {
                      summary: "摘要",
                      highlight: "重點",
                      chapter: "章節",
                    } as Record<string, string>
                  )[a.kind || ""] || "引用"}
                  {a.method === "extractive" ? " · 原文摘錄" : ""}
                </small>
                <h3>{a.title}</h3>
                <p>{a.text}</p>
                {a.spans?.map((s, i) => (
                  <button key={i} onClick={() => seek(s.start_us)}>
                    {formatTimecode(s.start_us, timeBase)}
                  </button>
                ))}
              </article>
            ))
          ) : (
            <div className="empty">
              <h3>把長片整理成重點</h3>
              <p>完成逐字稿後產生摘要。每個重點保留原文引用。</p>
            </div>
          )}
        </div>
      )}
    </aside>
  );
}

// 父層（App）有數十個狀態；props 未變時不重新渲染長逐字稿列表。
export default memo(TranscriptPanel);
