"""遠端語音轉錄端口：OpenRouter（OpenAI 相容）`POST {base}/audio/transcriptions`（2026-09-20 使用者要求加入）。

依 OpenRouter 官網 STT 文件：JSON 本文 {model, input_audio: {data: 純 base64（不加 data: 前綴）, format}, language}，回 {text, usage}；
單次約 60 秒處理上限、verbose_json 只有部分供應者支援 → 本端口只取文字：精修逐句送（每句自己的音訊），
時間沿用草稿句界、逐詞時間交給本機 wav2vec2 對齊（與 Breeze-ASR-26 同一條「只換文字」路）。
供應者設定、金鑰、遠端開關、錯誤碼與暫時性重試都沿用語言模型端口（providers._settings／_request）。

2026-09-21 使用者新增 ElevenLabs（Speech to Text，官網 api-reference/speech-to-text/convert）：
`POST {base}/speech-to-text`，multipart/form-data，標頭 xi-api-key；欄位 model_id、file、language_code（ISO-639-3）、
tag_audio_events=false（字幕不要「(笑聲)」這類標記）；回 {text, language_code, audio_duration_secs, words}。
跟 OpenRouter 同一條路：逐句送、只取文字（words 的時間不用，逐詞時間仍交給本機對齊）。
同一個來源可以登記多個轉錄模型（transcription_models），精修時用哪一個由清單鍵決定（asr_models.remote_settings）。"""
from __future__ import annotations

import base64
import io
import time
import wave

import numpy as np

from . import providers
from .providers import ProviderError

DEFAULT_TRANSCRIPTION_MODEL = "openai/whisper-1"
DEFAULT_MODELS = {"elevenlabs": "scribe_v2"}  # 依 adapter 的預設轉錄模型
SAMPLE_RATE = 16000
# ElevenLabs 的 language_code 用 ISO-639-3（官網支援語言表：中文 zho、日文 jpn、英文 eng）
ELEVENLABS_LANGUAGES = {"zh": "zho", "ja": "jpn", "en": "eng"}


def transcription_model(provider):
    """供應者設定裡的轉錄模型（可替換，與語言模型分開）；沒設定用預設。"""
    value = provider.get("transcription_model") if isinstance(provider, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    adapter = provider.get("adapter") if isinstance(provider, dict) else None
    return DEFAULT_MODELS.get(adapter, DEFAULT_TRANSCRIPTION_MODEL)


def transcription_models(provider):
    """這個來源登記的全部轉錄模型（主要的排第一、不重複）。"""
    names = [transcription_model(provider)]
    extra = provider.get("transcription_models") if isinstance(provider, dict) else None
    for name in extra if isinstance(extra, list) else []:
        if isinstance(name, str) and name.strip() and name.strip() not in names:
            names.append(name.strip())
    return names


def wav_bytes(audio):
    """16 kHz 單聲道 float → PCM16 WAV 位元組。"""
    samples = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes((samples * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


def wav_base64(audio):
    """16 kHz 單聲道 float → PCM16 WAV → 純 base64 字串。"""
    return base64.b64encode(wav_bytes(audio)).decode("ascii")


def _send(settings, provider, audio, language):
    """依供應者送出一段音訊，回供應者的原始 JSON 與整理過的 usage。"""
    if settings.get("adapter") == "elevenlabs":
        data = {"model_id": transcription_model(provider), "tag_audio_events": "false"}
        if language in ELEVENLABS_LANGUAGES:
            data["language_code"] = ELEVENLABS_LANGUAGES[language]
        response = providers._request_form(settings, "/speech-to-text", data, {"file": ("audio.wav", wav_bytes(audio), "audio/wav")})
        seconds = response.get("audio_duration_secs") if isinstance(response, dict) else None
        # ElevenLabs 回應沒有費用欄位：記下計費秒數（官網依音訊長度計費），費用由報表依牌價換算
        usage = {"seconds": seconds} if type(seconds) in (int, float) and seconds >= 0 else {}
        return response, usage
    body = {"model": transcription_model(provider), "input_audio": {"data": wav_base64(audio), "format": "wav"}}
    if language in ("zh", "ja", "en"):
        body["language"] = language
    response = providers._request(settings, "POST", "/audio/transcriptions", body)
    usage = response.get("usage") if isinstance(response, dict) and isinstance(response.get("usage"), dict) else {}
    return response, usage


def transcribe_text(audio, *, provider, secret, language=None):
    """送一段音訊，回 {text, avg_logprob, compression_ratio, usage}。錯誤一律是 ProviderError 穩定碼；限流／逾時／5xx 重試一次。"""
    settings = providers._settings(provider, secret)
    for attempt in range(settings["retries"] + 1):
        try:
            response, usage = _send(settings, provider, audio, language)
            break
        except ProviderError as error:
            if str(error) in providers.TRANSIENT_ERRORS and attempt < settings["retries"]:
                time.sleep(min(getattr(error, "retry_after", None) or providers.TRANSIENT_RETRY_DELAY_SEC, 5))
                continue
            raise
    text = response.get("text") if isinstance(response, dict) else None
    if not isinstance(text, str):
        raise ProviderError("INVALID_MODEL_OUTPUT")
    text = text.strip()
    from .asr import check_text_output
    check_text_output(text, len(audio) / SAMPLE_RATE)
    return dict(text=text, avg_logprob=None, compression_ratio=None, usage=usage)
