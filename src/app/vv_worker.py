"""子行程：用 VibeVoice 4-bit 轉錄出文字段落，寫 JSON 後退出。

設計重點：本檔在獨立子行程執行，跑完即退出 → VibeVoice 的 11.5GB VRAM 由 OS 完整回收，
且父行程可用「超時殺子行程」可靠處理 VibeVoice 的重複迴圈卡死（屬本專案 GR no-infinite-loop）。
用法：python -m app.vv_worker <wav> <out_json> [max_new_tokens]
"""
import os
import sys
import json
import wave

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
# 匯入 app.config 讓 HF／torch 快取綁定到專案 models/；上游腳本已收納於 app.vendor.vibevoice，不再需要參考目錄的 sys.path
import app.config  # noqa: E402,F401


def require_cuda_budget(cuda, required_mib=11500):
    """配置門檻依既有 4-bit 執行觀察，推論尖峰仍由外層錯誤處理保護。"""
    if not cuda.is_available():
        raise RuntimeError('CUDA_UNAVAILABLE')
    try:
        free, total = cuda.mem_get_info()
    except Exception as error:
        raise RuntimeError('GPU_MEMORY_UNAVAILABLE') from error
    if free < required_mib * 1024**2:
        raise RuntimeError(f'GPU_MEMORY_INSUFFICIENT: required={required_mib} MiB, free={free // 1024**2} MiB')
    return {'required_mib': required_mib, 'free_mib': free // 1024**2, 'total_mib': total // 1024**2}


def transcribe_batch(rows, load_model, transcribe):
    """先驗完整批次，再共用一個模型逐段推論，不併加 GPU 音訊張量。"""
    if not isinstance(rows, list) or not 1 <= len(rows) <= 8:
        raise ValueError('INVALID_VIBEVOICE_BATCH')
    duration = 0
    for row in rows:
        if not isinstance(row, dict) or type(row.get('max_new_tokens')) is not int or not 1 <= row['max_new_tokens'] <= 8192:
            raise ValueError('INVALID_VIBEVOICE_BATCH')
        with wave.open(row['wav'], 'rb') as stream:
            if stream.getnchannels() != 1 or stream.getframerate() != 16000 or stream.getsampwidth() != 2:
                raise ValueError('INVALID_VIBEVOICE_AUDIO')
            seconds = stream.getnframes() / stream.getframerate()
            if seconds <= 0:
                raise ValueError('INVALID_VIBEVOICE_AUDIO')
            duration += seconds
    if duration > 180:
        raise ValueError('VIBEVOICE_BATCH_TOO_LONG')
    model, processor = load_model('microsoft/VibeVoice-ASR', 'cuda', 'sdpa', quant='4bit')
    results = []
    for row in rows:
        raw, segments = transcribe(model, processor, row['wav'], 'cuda', max_new_tokens=row['max_new_tokens'])
        results.append({'raw': raw, 'segments': [
            {'start': s.get('start_time'), 'end': s.get('end_time'),
             'speaker': s.get('speaker_id'), 'text': s.get('text', '')} for s in segments]})
    return results


def main():
    wav, out_json = sys.argv[1], sys.argv[2]
    max_new = int(sys.argv[3]) if len(sys.argv) > 3 else 8192
    import torch
    require_cuda_budget(torch.cuda)
    from app.vendor.vibevoice.vibevoice_asr_to_srt import load_model, transcribe
    if wav.endswith('.batch.json'):
        with open(wav, encoding='utf-8') as stream:
            rows = json.load(stream)
        results = transcribe_batch(rows, load_model, transcribe)
        with open(out_json, 'w', encoding='utf-8') as stream:
            json.dump({'results': results}, stream, ensure_ascii=False)
        return
    model, processor = load_model("microsoft/VibeVoice-ASR", "cuda", "sdpa", quant="4bit")
    raw, segs = transcribe(model, processor, wav, "cuda", max_new_tokens=max_new)
    out = [{"start": s.get("start_time"), "end": s.get("end_time"),
            "speaker": s.get("speaker_id"), "text": s.get("text", "")} for s in segs]
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"segments": out, "raw": raw}, f, ensure_ascii=False)


def error_payload(error):
    messages = {
        'CUDA_UNAVAILABLE': 'VibeVoice 需要可用的 CUDA 顯示卡',
        'GPU_MEMORY_UNAVAILABLE': '無法讀取顯示卡記憶體，請檢查驅動與裝置',
        'GPU_MEMORY_INSUFFICIENT': '顯示卡可用記憶體不足，請釋放記憶體後重試',
    }
    code = str(error).split(':', 1)[0]
    return {'code': code if code in messages else 'VIBEVOICE_WORKER_FAILED',
            'message': messages.get(code, 'VibeVoice 執行失敗，原始逐字稿已保留')}


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if len(sys.argv) > 2:
            with open(sys.argv[2], 'w', encoding='utf-8') as stream:
                json.dump({'error': error_payload(error)}, stream, ensure_ascii=False)
        raise SystemExit(1)
