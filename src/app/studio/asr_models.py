"""精修可選的辨識模型（Whisper 家族）：內建 large-v3，加上模型清單 `ct2` 類（聯發創新基地 Breeze-ASR-25；26 於 2026-09-20 依使用者要求移除），以及 hf 類的 Qwen3-ASR（子程序執行）。

ct2 類模型由 model_store 從官方 Hugging Face repo 下載 safetensors 後在本機轉成 CTranslate2，放在 models/ct2/<key>/；
faster-whisper 直接以資料夾路徑載入（不連網）。清單（models.manifest.json）是唯一來源：標籤、語言、授權、下載量都從這裡讀。"""
from __future__ import annotations

from pathlib import Path

from .store import StudioError

DEFAULT = "large-v3"  # 內建模型：沒指定語言的段落、Breeze 不處理的日／英段落、預設模型沒安裝時都用它
# 精修預設：2026-09-19 改 Breeze-ASR-26；2026-09-20 使用者「26 可以移除 用 25 預設」→ Breeze-ASR-25；
# 同日使用者「我要看 CP 值高的當預設」→ 同一份草稿四模型比對（TEST_REPORT 第十四輪）：Qwen3-ASR-0.6B 精修 10.9 秒、+2.6 GB，
# large-v3 26.4 秒、Breeze-ASR-25 31.8 秒、Qwen3-ASR-1.7B 12.9 秒（+4.8 GB）→ Qwen3-ASR-0.6B。
# 已安裝才用，否則退回 DEFAULT；config.json 的 default_asr_model 可改
# 2026-09-20 使用者「這個模型可以拿掉跟刪除了，只留四種」→ 移除 Qwen3-ASR-0.6B（原預設）。
# 2026-09-20 使用者「預設 asr25 為預設第一個，再來次要是 v3」→ 預設與清單第一位都是 Breeze-ASR-25，large-v3 排第二並當退路。
REFINE_DEFAULT = "breeze-asr-25"
# 遠端轉錄：只換文字、需要金鑰與使用者同意、永遠不當預設。
# 來源（清單鍵的前半）→ 供應者：OpenRouter（2026-09-20）、ElevenLabs（2026-09-21 使用者新增）。
# 清單鍵：「openrouter」＝該來源的主要轉錄模型（transcription_model）；其他登記的模型（transcription_models）是「openrouter:模型名稱」，
# 例如 openrouter:microsoft/mai-transcribe-2（2026-09-21 使用者：OpenRouter 再新增轉錄可以用的 microsoft/mai-transcribe-2）。
REMOTE = "openrouter"
REMOTE_PROVIDER_ID = "api-openrouter"
REMOTE_SOURCES = {"openrouter": ("api-openrouter", "OpenRouter"), "elevenlabs": ("api-elevenlabs", "ElevenLabs")}
REMOTE_KEY_PATTERN = r"(openrouter|elevenlabs)(:[A-Za-z0-9][A-Za-z0-9._/:-]{0,119})?"


def parse_remote(key):
    """清單鍵 → (來源, 指定的模型名稱或 None)；不是遠端鍵回 (None, None)。"""
    if not isinstance(key, str):
        return None, None
    source, _, model = key.partition(":")
    if source not in REMOTE_SOURCES:
        return None, None
    return source, (model or None)


def is_remote(key):
    return parse_remote(key)[0] is not None


def source_label(key):
    """介面與錯誤訊息用的服務名稱（OpenRouter／ElevenLabs）。"""
    source = parse_remote(key)[0]
    return REMOTE_SOURCES[source][1] if source else "遠端服務"


def remote_provider(config, key=REMOTE):
    source = parse_remote(key)[0]
    if source is None:
        return None
    return next((p for p in (config or {}).get("providers", []) if p.get("id") == REMOTE_SOURCES[source][0]), None)


def remote_settings(config, root, key=REMOTE):
    """這個遠端轉錄鍵可用時回 {provider, secret, model}；沒設定供應者、沒開遠端、沒有金鑰，或模型名稱沒登記在供應者設定裡都回 None。
    金鑰只在後端解析。"""
    from .remote_asr import transcription_models
    provider = remote_provider(config, key)
    if provider is None or provider.get("allow_remote") is not True:
        return None
    registered = transcription_models(provider)
    model = parse_remote(key)[1] or registered[0]
    if model not in registered:
        return None
    from .provider_secrets import resolve_secret
    secret = resolve_secret(provider, root) if root is not None else None
    return dict(provider=provider, secret=secret, model=model) if secret else None
# 轉換後資料夾必須有的檔案；少了 tokenizer.json 時 faster-whisper 會改去網路抓標準詞彙，離線就壞，所以不算裝好
REQUIRED_FILES = ("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json")
PROVENANCE = "STUDIO_CT2.json"


def _manifest():
    from .model_store import load_manifest
    return load_manifest()


def _models_dir(models_dir=None):
    if models_dir is not None:
        return Path(models_dir)
    from app import config as app_config
    return Path(app_config.MODELS_DIR)  # 呼叫時才讀：測試與 STUDIO_MODELS_DIR 可以換位置


def entries(manifest=None):
    """可選的精修模型：ct2 類（本機轉換的 Whisper 家族）＋ hf 類裡帶 asr 設定的（例如 Qwen3-ASR，子程序執行）。"""
    manifest = manifest or _manifest()
    items = [dict(e, runner="ct2", download_id="ct2:" + e["key"]) for e in manifest.get("ct2", [])]
    for e in manifest.get("hf", []):
        if isinstance(e.get("asr"), dict):
            items.append(dict(e["asr"], repo=e["repo"], revision=e.get("revision"), download_id="hf:" + e["repo"]))
    return items


def qwen_overlay():
    """qwen-asr 套件層（--no-deps 裝在專案內，借用主環境 torch／transformers）；STUDIO_QWEN_ASR_PATH 可覆寫。"""
    import os
    override = os.environ.get("STUDIO_QWEN_ASR_PATH")
    if override:
        return Path(override)
    from app import config as app_config
    return Path(app_config.PROJECT_ROOT) / ".venv-qwen-asr"


def snapshot_dir(e, models_dir=None):
    """hf 類模型在 models/hf 的快照資料夾（依固定 revision；沒有就找任一個有權重的快照）；沒有回 None。"""
    base = _models_dir(models_dir) / "hf" / ("models--" + e["repo"].replace("/", "--")) / "snapshots"
    pinned = base / str(e.get("revision"))
    candidates = [pinned] + (sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else [])
    return next((p for p in candidates if p.is_dir() and any(p.glob("*.safetensors"))), None)


def runner(key):
    """builtin（faster-whisper 內建名稱）／ct2（本機轉換）／qwen（Qwen3-ASR 子程序）／remote（OpenRouter、ElevenLabs）。"""
    if is_remote(key):
        return "remote"
    e = entry(key)
    return e.get("runner", "ct2") if e else "builtin"


def entry(key, manifest=None):
    return next((e for e in entries(manifest) if e["key"] == key), None)


def folder(key, models_dir=None):
    return _models_dir(models_dir) / "ct2" / key


def installed(key, models_dir=None):
    e = entry(key)
    if e is None:
        return key == DEFAULT
    if e.get("runner") == "qwen":
        return snapshot_dir(e, models_dir) is not None and (qwen_overlay() / "qwen_asr" / "__init__.py").is_file()
    target = folder(key, models_dir)
    return all((target / name).is_file() and (target / name).stat().st_size > 0 for name in REQUIRED_FILES)


def catalog(models_dir=None, config=None, root=None):
    """介面／API 用的清單：第一個是預設模型（Breeze-ASR-25），接著 large-v3，其餘照清單；有設定 OpenRouter 供應者時最後加遠端轉錄。"""
    builtin = dict(key=DEFAULT, label="Whisper large-v3", languages=None, installed=True, download_bytes=0, license="MIT")
    items = []
    for e in entries():
        items.append(dict(key=e["key"], label=e["label"], languages=e.get("languages"), installed=installed(e["key"], models_dir),
                          download_bytes=e.get("download_bytes", 0), license=e.get("license"), repo=e["repo"],
                          runner=e.get("runner"), download_id=e.get("download_id"), timestamps=e.get("timestamps", True) is not False))
    # 使用者指定的順序：預設模型第一、large-v3 第二（2026-09-20）
    items.sort(key=lambda item: 0 if item["key"] == REFINE_DEFAULT else 1)
    items.insert(1 if items else 0, builtin)
    from .remote_asr import transcription_models
    for source, (_, name) in REMOTE_SOURCES.items():
        provider = remote_provider(config, source)
        if provider is None:
            continue
        # 每個登記的轉錄模型各一個選項：主要的用來源名當鍵（舊鍵不變），其他用「來源:模型名稱」
        for index, model in enumerate(transcription_models(provider)):
            key = source if index == 0 else f"{source}:{model}"
            ready = remote_settings(config, root, key) is not None
            items.append(dict(key=key, label=f"{name} 遠端轉錄（{model}）", languages=None, installed=ready, download_bytes=0, license=None,
                              remote=True, timestamps=False, provider_label=name, remote_model=model,
                              missing=None if ready else ("remote_disabled" if provider.get("allow_remote") is not True else "secret")))
    return items


def default_model(config=None, models_dir=None):
    """analyze 沒指定精修模型時用哪一個：config.json 的 default_asr_model（認得的值才採用），否則 REFINE_DEFAULT；
    選到的 ct2 模型沒安裝就退回 large-v3，轉錄不會因為預設而失敗。"""
    wanted = (config or {}).get("default_asr_model")
    if wanted != DEFAULT and entry(wanted) is None:
        wanted = REFINE_DEFAULT
    if wanted != DEFAULT and not installed(wanted, models_dir):
        return DEFAULT
    return wanted


def languages(key):
    """這個模型只該處理哪些語言；None＝不限（large-v3 等內建模型）。"""
    e = entry(key)
    return list(e["languages"]) if e and e.get("languages") else None


def timestamps(key):
    """模型會不會預測句子時間戳；False（例如 Breeze-ASR-26）時精修只換文字、時間沿用草稿句界。內建模型一律 True。"""
    if is_remote(key):
        return False  # 遠端轉錄只取文字（各供應者時間戳支援不一），時間沿用草稿句界
    e = entry(key)
    return True if e is None else e.get("timestamps", True) is not False


def resolve(key, models_dir=None):
    """給 faster-whisper 的模型參數：內建名稱原樣傳回；ct2 類傳回本機資料夾，沒裝好就回清楚的錯誤。"""
    e = entry(key)
    if e is None:
        return key
    if e.get("runner") == "qwen":
        snapshot = snapshot_dir(e, models_dir)
        if snapshot is None or not installed(key, models_dir):
            missing = "權重" if snapshot is None else "qwen-asr 執行套件（.venv-qwen-asr）"
            raise StudioError("MODEL_NOT_INSTALLED", f"尚未安裝 {e['label']} 的{missing}：權重到「模型設定 → 環境與模型」下載；"
                              "執行套件用 python -m pip install --no-deps --target .venv-qwen-asr qwen-asr==0.0.6 qwen-omni-utils==0.0.9", 409,
                              {"model": key, "id": e.get("download_id")})
        return str(snapshot)
    if not installed(key, models_dir):
        raise StudioError("MODEL_NOT_INSTALLED",
            f"尚未安裝 {e['label']}：請到右上角齒輪「模型設定」→「環境與模型」的模型清單按「下載並轉換」（下載約 {e.get('download_bytes', 0) / 1e9:.1f} GB，會在本機轉成可用格式）",
            409, {"model": key, "id": "ct2:" + key})
    return str(folder(key, models_dir))
