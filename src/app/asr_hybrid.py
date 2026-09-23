"""最佳文字混合引擎：VibeVoice 4-bit 文字（子行程隔離）+ whisperx 詞級對齊。

流程：
1. 子行程跑 VibeVoice（隔離 VRAM）→ 文字段落；帶 max_new_tokens 上限 + 超時殺子行程（防重複迴圈）。
2. 子行程失敗/超時 → 退回 WhisperX（asr_default）。
3. 清理 VibeVoice 輪次時間（去標籤、夾 end>start、排序）。
4. 主行程用 whisperx 對齊 VibeVoice 文字 → 詞級時間。
"""
import os
import sys
import json
import math
import subprocess
from app.config import VV_TOKENS_PER_SEC
from app.reuse import is_tag
from app import asr_default

_SRC_DIR = os.path.dirname(os.path.dirname(__file__))   # .../src


def _vibevoice_text(wav, dur, timeout):
    """子行程跑 VibeVoice，回 text segments；超時/失敗回 None（由呼叫端退回）。"""
    out_json = wav + ".vv.json"
    max_new = max(512, int(math.ceil(dur * VV_TOKENS_PER_SEC * 1.5)))
    env = dict(os.environ, PYTHONPATH=_SRC_DIR, HF_HUB_DISABLE_SYMLINKS="1")
    cmd = [sys.executable, "-m", "app.vv_worker", wav, out_json, str(max_new)]
    try:
        subprocess.run(cmd, timeout=timeout, check=True, env=env, cwd=_SRC_DIR)
    except subprocess.TimeoutExpired:
        print(f"[hybrid] VibeVoice 子行程超時 {timeout}s（疑重複迴圈）→ 殺掉並退回 WhisperX")
        return None
    except subprocess.CalledProcessError as e:
        print(f"[hybrid] VibeVoice 子行程錯誤：{e} → 退回 WhisperX")
        return None
    if not os.path.exists(out_json):
        return None
    try:
        return json.load(open(out_json, encoding="utf-8")).get("segments", [])
    except Exception:
        return None


def _sanitize(segs):
    """清理 VibeVoice 輪次時間：去標籤、夾 end>start、排序（對齊前必要）。"""
    out = []
    for s in segs:
        txt = (s.get("text") or "").strip()
        if not txt or is_tag(txt):
            continue
        a = float(s.get("start") or 0.0)
        b = float(s.get("end") or 0.0)
        if b <= a:
            b = a + 0.5
        out.append({"start": a, "end": b, "text": txt})
    out.sort(key=lambda x: x["start"])
    return out


def transcribe_align(wav_path, dur=None, timeout=None):
    """回傳 {'language','segments'(含 words)}；任何失敗皆退回 WhisperX，保證有輸出。"""
    import whisperx
    from app import regions, lid

    if dur is None:
        dur = regions.ffprobe_duration(wav_path)
    if timeout is None:
        timeout = max(120, int(dur * 4) + 60)        # watchdog 上限

    vv = _vibevoice_text(wav_path, dur, timeout)
    if not vv:
        return asr_default.transcribe_align(wav_path)
    segs = _sanitize(vv)
    if not segs:
        return asr_default.transcribe_align(wav_path)

    lang = lid.label_cue(segs[0]["text"]) or "ja"
    dev = asr_default._device()
    try:
        amodel, meta = asr_default._get_align(lang, dev)
        audio = whisperx.load_audio(wav_path)
        aligned = whisperx.align(segs, amodel, meta, audio, dev, return_char_alignments=False)
        return {"language": lang, "segments": aligned.get("segments", segs)}
    except Exception as e:
        print(f"[hybrid] 對齊失敗（{lang}）：{e} → 用 VibeVoice 原始時間")
        return {"language": lang, "segments": segs}
