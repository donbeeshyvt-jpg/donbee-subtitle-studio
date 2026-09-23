import { useCallback, useEffect, useState } from "react";
import type { ProbeResult, ProbeStatusCode, ProviderConfig, ProviderInfo } from "../api/types";

// 文字模型供應者（2026-09-21 使用者重新整理）：只留這幾個來源，只做「確認連線」與「填金鑰」。
// - 本機（LM Studio／llama.cpp）：模型跟著服務目前載入的（auto），不顯示設定的模型字串；測試連線後才顯示實際載入的模型。
// - 遠端（OpenRouter、ElevenLabs）：同意遠端 → 存金鑰（只寫入不回顯）→ 測試連線通過後，才能填模型名稱並儲存。
//   OpenRouter 有語言模型與轉錄模型；ElevenLabs 只做轉錄（2026-09-21 使用者新增）。
//   轉錄模型可以登記好幾個（2026-09-21：OpenRouter 再新增 microsoft/mai-transcribe-2），精修模型清單每個各一個選項。
export const probeText: Record<ProbeStatusCode, string> = {
  ready: "已就緒",
  service_unreachable: "服務未啟動或無法連線",
  secret_missing: "尚未填入金鑰",
  auth_failed: "金鑰無效或權限不足",
  model_not_loaded: "服務已開啟但沒有載入對話模型",
  rate_limited: "供應者限流，請稍後重試",
  structured_output_unsupported: "已連線，但不支援目前的回覆格式模式（不會自動換模式）",
  credits_exhausted: "帳戶餘額不足，請到該服務網站儲值後再試",
  invalid_config: "設定無效",
  provider_error: "供應者錯誤",
};

export const probeLabel = (status: ProbeStatusCode | string) => probeText[status as ProbeStatusCode] ?? status;

type Port = {
  id: string;
  label: string;
  local: boolean;
  chat?: boolean; // 有語言模型（AI 分析、校字）
  keyHint?: string;
  chatExample?: string;
  transcriptionExample?: string;
  extraExample?: string; // 「其他轉錄模型」輸入框的範例
};

// 面板只列這幾個（舊設定檔裡的其他項目仍在 config，但不再出現在這裡）
const PORTS: Port[] = [
  { id: "local-lmstudio", label: "LM Studio", local: true },
  { id: "local-llamacpp", label: "llama.cpp", local: true },
  { id: "api-openrouter", label: "OpenRouter", local: false, chat: true, keyHint: "貼上 sk-or-v1- 開頭的金鑰",
    chatExample: "deepseek/deepseek-v4.1-flash", transcriptionExample: "meta/muse-voice-transcribe-1.0",
    extraExample: "microsoft/mai-transcribe-2" },
  { id: "api-elevenlabs", label: "ElevenLabs", local: false, chat: false, keyHint: "貼上 ElevenLabs 的 API 金鑰（sk_ 開頭）",
    transcriptionExample: "scribe_v2", extraExample: "scribe_v1" },
];

// 供應者權限名稱 → 後台上的名稱（與後端 worker.PERMISSION_NAMES 相同）
const PERMISSION_NAMES: Record<string, string> = { speech_to_text: "Speech to Text", text_to_speech: "Text to Speech" };

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

function describe(label: string, local: boolean, result: ProbeResult | undefined) {
  if (!result) return "尚未測試";
  if (result.status === "ready") {
    if (local) return `已連線：${result.model || "（未回報模型）"}${result.auto_model ? "（目前載入）" : ""}`;
    const scoped = result.detail === "KEY_SCOPED" ? "（金鑰有限定權限：請確認有開 Speech to Text）" : "";
    return `已連線：金鑰可用，${result.model || "（未設定模型）"}${scoped}`;
  }
  if (result.status === "service_unreachable") return `沒有連上 ${label}：請確認 ${label} 已啟動並開啟本機伺服器`;
  if (result.status === "model_not_loaded") return `${label} 有回應，但沒有載入任何對話模型：請先在 ${label} 載入模型`;
  // 金鑰有效但缺權限（2026-09-21 真跑：ElevenLabs 金鑰沒開 Speech to Text）：說出缺哪個、去哪裡開
  if (result.detail?.startsWith("PERMISSION_MISSING:")) {
    const permission = result.detail.split(":")[1];
    const name = PERMISSION_NAMES[permission] ?? permission;
    return `金鑰有效，但沒有開「${name}」權限：到 ${label} 後台 API Keys 編輯這把金鑰、勾選「${name}」後再測一次（不必重填金鑰）`;
  }
  return `${probeLabel(result.status)}${result.detail ? `（${result.detail}）` : ""}`;
}

// 這個來源登記的全部轉錄模型（主要的排第一、不重複）
export function transcriptionList(provider: ProviderInfo | undefined, fallback = ""): string[] {
  const names: string[] = [];
  for (const name of [provider?.transcription_model || fallback, ...(provider?.transcription_models ?? [])]) {
    const value = (name ?? "").trim();
    if (value && !names.includes(value)) names.push(value);
  }
  return names;
}

function RemotePort({
  port,
  provider,
  ready,
  busy,
  onSecret,
  onRemoveSecret,
  onSave,
}: {
  port: Port;
  provider: ProviderInfo;
  ready: boolean;
  busy: boolean;
  onSecret: (value: string) => Promise<void>;
  onRemoveSecret: () => Promise<void>;
  onSave: (config: ProviderConfig) => Promise<void>;
}) {
  const registered = transcriptionList(provider, port.transcriptionExample);
  const [secret, setSecretValue] = useState("");
  const [chatModel, setChatModel] = useState(provider.model || "");
  const [primary, setPrimary] = useState(registered[0] ?? "");
  const [extra, setExtra] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const extras = registered.slice(1);
  useEffect(() => {
    // 清單重新讀回（例如存檔後）時，輸入框跟著設定檔
    setChatModel(provider.model || "");
    setPrimary(transcriptionList(provider, port.transcriptionExample)[0] ?? "");
  }, [provider, port.transcriptionExample]);
  const persist = (main: string, others: string[], done: () => void) => {
    const model = port.chat ? chatModel.trim() : main;
    if (!model) {
      setError(`請填語言模型名稱，例如 ${port.chatExample}`);
      return Promise.resolve();
    }
    if (!main) {
      setError(`請填轉錄模型名稱，例如 ${port.transcriptionExample}`);
      return Promise.resolve();
    }
    setError("");
    const { local: _local, secret_configured: _secret, last_probe: _probe, ...config } = provider;
    const names = [main, ...others.filter((name) => name !== main)];
    // 失敗時錯誤已顯示在面板上（onSave 會拋出），這裡只在成功後更新提示
    return onSave({ ...config, model, transcription_model: main, transcription_models: names }).then(done, () => undefined);
  };
  const saveNames = () =>
    persist(primary.trim(), extras, () =>
      setNote(port.chat ? "已儲存。「AI 分析」與「精修模型」的選項會用這些名稱。" : "已儲存。「精修模型」會多出這些選項。"),
    );
  const addModel = () => {
    const name = extra.trim();
    if (!name) return Promise.resolve();
    if (registered.includes(name)) {
      setError(`${name} 已經在清單裡`);
      return Promise.resolve();
    }
    return persist(registered[0] ?? primary.trim(), [...extras, name], () => {
      setExtra("");
      setNote(`已新增 ${name}：到「精修模型」可以選它。`);
    });
  };
  const removeModel = (name: string) =>
    persist(registered[0] ?? primary.trim(), extras.filter((item) => item !== name), () => setNote(`已移除 ${name}。`));
  return (
    <div className="provider-remote">
      {error ? <p className="error-text" role="alert">{error}</p> : null}
      <div className="provider-secret">
        <label>
          <span className="sr-only">{port.label} 金鑰</span>
          <input
            type="password"
            aria-label={`${port.label} 金鑰`}
            autoComplete="off"
            placeholder={provider.secret_configured ? "已存金鑰；要換新的再貼上" : port.keyHint}
            value={secret}
            onChange={(e) => setSecretValue(e.target.value)}
          />
        </label>
        <button
          aria-label={`儲存 ${port.label} 金鑰`}
          disabled={busy || !secret.trim()}
          onClick={() => void onSecret(secret.trim()).then(() => setSecretValue(""), () => undefined)}
        >
          儲存金鑰
        </button>
        {provider.secret_configured ? (
          <>
            <span className="badge ok">已存金鑰</span>
            <button aria-label={`刪除 ${port.label} 金鑰`} disabled={busy} onClick={() => void onRemoveSecret()}>
              刪除金鑰
            </button>
          </>
        ) : (
          <span className="muted">尚未存金鑰</span>
        )}
      </div>
      <div className="provider-models">
        {port.chat ? (
          <label>
            語言模型（AI 分析、校字）
            <input
              aria-label="語言模型（AI 分析、校字）"
              value={chatModel}
              disabled={!ready}
              placeholder={port.chatExample}
              onChange={(e) => setChatModel(e.target.value)}
            />
          </label>
        ) : null}
        <label>
          轉錄模型
          <input
            aria-label={`${port.label} 轉錄模型`}
            value={primary}
            disabled={!ready}
            placeholder={port.transcriptionExample}
            onChange={(e) => setPrimary(e.target.value)}
          />
        </label>
        <button aria-label={`儲存 ${port.label} 模型`} disabled={!ready || busy} onClick={() => void saveNames()}>
          儲存模型
        </button>
      </div>
      <div className="provider-extra-models">
        <span className="muted">其他轉錄模型</span>
        {extras.length ? (
          <ul className="model-chips">
            {extras.map((name) => (
              <li key={name}>
                <code>{name}</code>
                <button aria-label={`移除 ${name}`} disabled={!ready || busy} onClick={() => void removeModel(name)}>
                  移除
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <span className="muted">（沒有）</span>
        )}
        <input
          aria-label={`要新增的 ${port.label} 轉錄模型名稱`}
          value={extra}
          disabled={!ready}
          placeholder={`模型名稱，例如 ${port.extraExample}`}
          onChange={(e) => setExtra(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void addModel();
          }}
        />
        <button aria-label={`新增 ${port.label} 轉錄模型`} disabled={!ready || busy || !extra.trim()} onClick={() => void addModel()}>
          新增
        </button>
      </div>
      <small className="muted">
        {ready ? note || `填 ${port.label} 上的模型名稱後儲存。` : "金鑰測試連線通過後才能設定模型名稱。"}
      </small>
    </div>
  );
}

export default function ProviderPanel({
  list,
  probe,
  setSecret,
  deleteSecret,
  save,
  consent,
  onConsent,
  onChanged,
}: {
  list: () => Promise<{ items: ProviderInfo[] }>;
  probe: (id: string) => Promise<ProbeResult>;
  setSecret: (id: string, secret: string) => Promise<unknown>;
  deleteSecret: (id: string) => Promise<unknown>;
  save: (config: ProviderConfig, isNew: boolean) => Promise<unknown>;
  consent: boolean;
  onConsent: (value: boolean) => void;
  onChanged?: () => void;
}) {
  const [items, setItems] = useState<ProviderInfo[]>([]);
  const [results, setResults] = useState<Record<string, ProbeResult | undefined>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const reload = useCallback(async () => {
    try {
      setItems((await list()).items);
      setError("");
    } catch (e) {
      setError(message(e));
    }
  }, [list]);
  useEffect(() => {
    void reload();
  }, [reload]);
  const withBusy = async (id: string, action: () => Promise<void>) => {
    setBusy(id);
    setError("");
    try {
      await action();
    } catch (e) {
      setError(message(e));
      throw e;
    } finally {
      setBusy("");
    }
  };
  const quiet = (promise: Promise<void>) => promise.catch(() => undefined); // 錯誤已顯示在面板上
  const runProbe = (id: string) =>
    quiet(
      withBusy(id, async () => {
        const result = await probe(id);
        setResults((prev) => ({ ...prev, [id]: result }));
      }),
    );
  const saveSecret = (id: string, value: string) =>
    withBusy(id, async () => {
      await setSecret(id, value);
      setItems((prev) => prev.map((p) => (p.id === id ? { ...p, secret_configured: true } : p)));
      setResults((prev) => ({ ...prev, [id]: undefined })); // 換了金鑰要重新測
      onChanged?.();
    });
  const removeSecret = (id: string) =>
    quiet(
      withBusy(id, async () => {
        await deleteSecret(id);
        setItems((prev) => prev.map((p) => (p.id === id ? { ...p, secret_configured: false } : p)));
        onChanged?.();
      }),
    );
  const saveConfig = (id: string, config: ProviderConfig) =>
    withBusy(id, async () => {
      await save(config, false);
      await reload();
      onChanged?.();
    });
  const hasRemote = PORTS.some((port) => !port.local && items.some((p) => p.id === port.id));
  return (
    <section className="provider-panel" aria-label="文字模型供應者">
      <div className="environment-head">
        <h3>文字模型供應者</h3>
      </div>
      {error ? <p className="error-text" role="alert">{error}</p> : null}
      {hasRemote ? (
        <label className="consent">
          <input type="checkbox" checked={consent} onChange={(e) => onConsent(e.target.checked)} />
          同意把逐字稿／音訊送到 OpenRouter、ElevenLabs 等遠端服務（未勾選時只用本機服務）
        </label>
      ) : null}
      <ul className="provider-list">
        {PORTS.map((port) => ({ port, provider: items.find((p) => p.id === port.id) }))
          .filter((row): row is { port: Port; provider: ProviderInfo } => Boolean(row.provider))
          .map(({ port, provider }) => {
            const result = results[port.id];
            return (
              <li key={port.id} className="provider-row-block">
                <div className="provider-row">
                  <strong>{port.label}</strong>
                  <span className={port.local ? "badge ok" : "badge remote"}>
                    {port.local ? "本機" : port.chat === false ? "遠端 API（只做轉錄）" : "遠端 API"}
                  </span>
                  <button aria-label={`測試 ${port.label} 連線`} disabled={busy === port.id} onClick={() => void runProbe(port.id)}>
                    {busy === port.id ? "測試中…" : "測試連線"}
                  </button>
                  <span className={result?.status === "ready" ? "probe-ok" : "muted"} role="status">
                    {describe(port.label, port.local, result)}
                  </span>
                </div>
                {!port.local ? (
                  <RemotePort
                    port={port}
                    provider={provider}
                    ready={result?.status === "ready"}
                    busy={busy === port.id}
                    onSecret={(value) => saveSecret(port.id, value)}
                    onRemoveSecret={() => removeSecret(port.id)}
                    onSave={(config) => saveConfig(port.id, config)}
                  />
                ) : null}
              </li>
            );
          })}
      </ul>
    </section>
  );
}
