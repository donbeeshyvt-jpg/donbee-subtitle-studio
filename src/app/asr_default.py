"""預設 ASR：WhisperX large-v3 轉錄 + wav2vec2 逐語言強制對齊。

回傳含 words 的 segments（本地時間，由 cli 加 offset 映回絕對時間）。
- 逐語言對齊模型以 dict 快取（載一次跑多區段）。
- 對齊前剝除標籤 cue（[Music] 等不可逐字對齊）。
- WhisperX/faster-whisper 為有界前向，無重複迴圈風險（VibeVoice 才有，屬 feature 003）。
"""
from app.config import ASR_SAMPLE_RATE  # noqa: F401 — 觸發 config 設定 HF 環境
from app.reuse import is_tag

_model = None
_model_key = None
_align_cache = {}     # lang -> (align_model, metadata)


def _device():
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def _load_model(name="large-v3", compute_type="float16"):
    global _model, _model_key
    key = (name, _device(), compute_type)
    if _model is None or _model_key != key:
        import whisperx
        _model = None
        _model_key = None
        _model = whisperx.load_model(name, device=key[1], compute_type=compute_type)
        _model_key = key
    return _model


def _get_align(lang, device):
    key = (lang, device)
    if key not in _align_cache:
        import whisperx
        _align_cache[key] = whisperx.load_align_model(language_code=lang, device=device)
    return _align_cache[key]


def transcribe_align(wav_path, model_name="large-v3", do_align=True, batch_size=16):
    """回傳 {'language': code, 'segments': [{start,end,text,words?}]}（本地時間）。"""
    import whisperx
    dev = _device()
    model = _load_model(model_name)
    audio = whisperx.load_audio(wav_path)
    result = model.transcribe(audio, batch_size=batch_size)
    lang = result.get("language")
    segs = [s for s in result.get("segments", []) if not is_tag(s.get("text", ""))]

    if do_align and segs and lang:
        try:
            amodel, meta = _get_align(lang, dev)
            aligned = whisperx.align(segs, amodel, meta, audio, dev,
                                     return_char_alignments=False)
            segs = aligned.get("segments", segs)
        except Exception as e:                  # 語言不支援對齊 → 退回 segment 級時間
            print(f"[asr_default] 對齊跳過（lang={lang}）：{e}")
    return {"language": lang, "segments": segs}
