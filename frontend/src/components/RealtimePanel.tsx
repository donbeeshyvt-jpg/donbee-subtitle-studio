// M7 即時字幕與翻譯（2026-09-20 使用者：「M7 也可以納入，然後交叉測試」）：
// 麥克風或播放中的素材 → 每 0.5 秒送一段 16 kHz PCM 到本機服務 → 顯示暫定文字與定稿字幕，譯文跟在每一行下面；停止後可下載 SRT。
// 服務端用 faster-whisper（turbo）滑動視窗＋兩次一致才定稿；翻譯用「AI 分析、摘要與校字」選的文字模型。
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Provider, RealtimeEvent, RealtimeLine } from "../api/types";
import { PcmChunker, downsampleToPcm16 } from "../domain/pcm";

type SourceKind = "mic" | "media";
export type OpenSource = (kind: SourceKind, onChunk: (samples: Int16Array) => void) => Promise<() => void>;

// 瀏覽器音訊來源：麥克風（getUserMedia）或播放中的素材（captureStream，不影響原本的播放聲音）
export function browserSource(media: () => HTMLMediaElement | null): OpenSource {
  return async (kind, onChunk) => {
    const context = new AudioContext();
    let stream: MediaStream;
    let release = () => {};
    if (kind === "mic") {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false } });
      release = () => stream.getTracks().forEach((t) => t.stop());
    } else {
      const element = media() as (HTMLMediaElement & { captureStream?: () => MediaStream; mozCaptureStream?: () => MediaStream }) | null;
      if (!element) throw Error("先在上方載入素材");
      const capture = element.captureStream || element.mozCaptureStream;
      if (!capture) throw Error("這個瀏覽器不支援擷取播放中的聲音，請改用麥克風");
      stream = capture.call(element);
      await element.play();
      release = () => element.pause();
    }
    const input = context.createMediaStreamSource(stream);
    const processor = context.createScriptProcessor(4096, 1, 1);
    processor.onaudioprocess = (e) => onChunk(downsampleToPcm16(e.inputBuffer.getChannelData(0), context.sampleRate));
    input.connect(processor);
    processor.connect(context.destination); // 沒寫輸出（靜音），只是讓處理節點運作
    return () => {
      processor.disconnect();
      input.disconnect();
      release();
      void context.close();
    };
  };
}

const TARGETS: [string, string][] = [
  ["", "不翻譯"],
  ["zh-TW", "繁體中文"],
  ["en", "英文"],
  ["ja", "日文"],
];
const seconds = (value: number) => `${Math.floor(value / 60)}:${(value % 60).toFixed(1).padStart(4, "0")}`;

export default function RealtimePanel({
  providers,
  provider,
  remoteConsent,
  openSource,
}: {
  providers: Provider[];
  provider: string;
  remoteConsent: boolean;
  openSource: OpenSource;
}) {
  const [kind, setKind] = useState<SourceKind>("mic");
  const [language, setLanguage] = useState("zh");
  const [target, setTarget] = useState("");
  const [status, setStatus] = useState<"idle" | "starting" | "live" | "stopping">("idle");
  const [lines, setLines] = useState<RealtimeLine[]>([]);
  const [tentative, setTentative] = useState("");
  const [received, setReceived] = useState(0);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{ srt: string; stats: Record<string, unknown> } | null>(null);
  const session = useRef("");
  const stopSource = useRef<() => void>(() => {});
  const chunker = useRef(new PcmChunker());
  const queue = useRef<Int16Array[]>([]);
  const sending = useRef(false);

  const apply = (events: RealtimeEvent[]) => {
    for (const e of events) {
      if (e.type === "line") {
        setLines((v) => [...v, { id: e.id!, start: e.start!, end: e.end!, text: e.text! }]);
        setTentative("");
      } else if (e.type === "tentative") setTentative(e.text || "");
      else if (e.type === "translation") setLines((v) => v.map((l) => (l.id === e.line_id ? { ...l, translation: e.text } : l)));
      else if (e.type === "translation_error") setError(`翻譯失敗：${e.code || "未知原因"}（字幕照常）`);
    }
  };
  // 一次只送一個請求；送的時候新到的段落排隊，下一次合併送（最多 5 秒）
  const pump = async () => {
    if (sending.current) return;
    sending.current = true;
    try {
      while (queue.current.length && session.current) {
        const parts: Int16Array[] = [];
        let total = 0;
        while (queue.current.length && (!parts.length || total + queue.current[0].length <= 80000)) {
          const part = queue.current.shift()!;
          parts.push(part);
          total += part.length;
        }
        const merged = new Int16Array(total);
        let at = 0;
        for (const part of parts) {
          merged.set(part, at);
          at += part.length;
        }
        const reply = await api.realtimeAudio(session.current, merged.buffer);
        setReceived(reply.received_sec);
        apply(reply.events);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      sending.current = false;
    }
  };
  const onChunk = (samples: Int16Array) => {
    for (const piece of chunker.current.push(samples)) queue.current.push(piece);
    void pump();
  };
  const idle = async () => {
    for (let i = 0; i < 200 && sending.current; i += 1) await new Promise((r) => setTimeout(r, 50));
  };

  const start = async () => {
    setError("");
    setResult(null);
    if (target && !provider) {
      setError("翻譯需要文字模型：先在「AI 分析、摘要與校字」選文字模型");
      return;
    }
    const chosen = providers.find((p) => p.id === provider);
    setStatus("starting");
    setLines([]);
    setTentative("");
    setReceived(0);
    chunker.current = new PcmChunker();
    queue.current = [];
    try {
      const created = await api.realtimeCreate({
        model: "turbo",
        language,
        ...(target ? { translate_to: target, provider_id: provider } : {}),
        remote_consent: !!target && chosen?.local === false && remoteConsent,
      });
      session.current = created.session_id;
      stopSource.current = await openSource(kind, onChunk);
      setStatus("live");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      if (session.current) void api.realtimeClose(session.current).catch(() => undefined);
      session.current = "";
      setStatus("idle");
    }
  };
  const stop = async () => {
    setStatus("stopping");
    stopSource.current();
    stopSource.current = () => {};
    const rest = chunker.current.flush();
    if (rest) queue.current.push(rest);
    await idle();
    await pump();
    const sid = session.current;
    try {
      const final = await api.realtimeFinish(sid);
      setLines(final.lines);
      setTentative("");
      setResult({ srt: final.srt, stats: final.stats });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      session.current = "";
      setStatus("idle");
    }
  };
  useEffect(
    () => () => {
      // 離開頁面：停掉音訊來源並關閉工作階段（釋放顯示卡）
      stopSource.current();
      if (session.current) void api.realtimeClose(session.current).catch(() => undefined);
    },
    [],
  );
  const stats = result?.stats as
    | { real_time_factor?: number; commit_lag_sec?: { median?: number }; translation_sec?: { median?: number | null } }
    | undefined;
  const download = () => {
    const url = URL.createObjectURL(new Blob([result!.srt], { type: "application/x-subrip" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "即時字幕.srt";
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="realtime-panel">
      <div className="realtime-controls">
        <label>
          音訊來源
          <select aria-label="音訊來源" value={kind} disabled={status !== "idle"} onChange={(e) => setKind(e.target.value as SourceKind)}>
            <option value="mic">麥克風</option>
            <option value="media">播放目前素材</option>
          </select>
        </label>
        <label>
          說話語言
          <select aria-label="說話語言" value={language} disabled={status !== "idle"} onChange={(e) => setLanguage(e.target.value)}>
            <option value="zh">中文</option>
            <option value="ja">日文</option>
            <option value="en">英文</option>
            <option value="auto">自動偵測</option>
          </select>
        </label>
        <label>
          翻譯成
          <select aria-label="翻譯成" value={target} disabled={status !== "idle"} onChange={(e) => setTarget(e.target.value)}>
            {TARGETS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {status === "idle" ? (
          <button className="primary" onClick={() => void start()}>
            開始即時字幕
          </button>
        ) : (
          <button disabled={status !== "live"} onClick={() => void stop()}>
            {status === "starting" ? "啟動中…" : status === "stopping" ? "結束中…" : "停止"}
          </button>
        )}
        {result ? <button onClick={download}>下載 SRT</button> : null}
      </div>
      <p className="muted realtime-state" role="status">
        {error
          ? error
          : status === "live"
            ? `即時字幕中：已收 ${received.toFixed(1)} 秒音訊`
            : stats
              ? `處理時間約為音訊長度的 ${Math.round((stats.real_time_factor || 0) * 100)}%、定稿延遲中位 ${stats.commit_lag_sec?.median ?? "—"} 秒${
                  stats.translation_sec?.median != null ? `、翻譯中位 ${stats.translation_sec.median} 秒` : ""
                }`
              : "即時字幕與批次轉錄共用顯示卡：開始前請等轉錄／對齊完成。"}
      </p>
      <ol className="realtime-lines">
        {lines.map((line) => (
          <li key={line.id}>
            <span className="muted realtime-time">{seconds(line.start)}</span>
            <span>{line.text}</span>
            {line.translation ? <span className="realtime-translation">{line.translation}</span> : null}
          </li>
        ))}
      </ol>
      {tentative ? <p className="realtime-tentative">{tentative}</p> : null}
    </div>
  );
}
