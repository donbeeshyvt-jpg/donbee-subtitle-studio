"""區段處理：正規化/補邊/合併 + ffprobe 時長 + ffmpeg 取樣精確切片 + offset 映射。"""
import os
import subprocess
from app.config import REGION_PAD_SEC, ASR_SAMPLE_RATE


def ffprobe_duration(path):
    """用 ffprobe 取媒體總長（秒）。"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def normalize(regions, duration, pad=REGION_PAD_SEC):
    """
    正規化區段清單：
    - 空清單 → 整檔 [(0, duration)]
    - 先補邊 ±pad（避免切到字）並夾到 [0, duration]，丟掉過短段
    - 排序後合併重疊/相鄰段
    回傳排序、不重疊的 [(start, end)]（浮點秒）。
    """
    duration = float(duration)
    if not regions:
        return [(0.0, duration)]
    spans = []
    for a, b in regions:
        a = max(0.0, float(a) - pad)
        b = min(duration, float(b) + pad)
        if b - a > 0.05:
            spans.append((a, b))
    if not spans:
        return []
    spans.sort()
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:           # 重疊或相鄰 → 合併
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def slice_audio(media_path, spans, work_dir, sr=ASR_SAMPLE_RATE):
    """
    每個區段用 ffmpeg 取樣精確切出 mono wav（-ss 前置 + -t + 重新編碼 pcm_s16le）。
    回傳 [(wav_path, offset_sec)]；offset = 區段起點，用來把本地時間映回絕對時間。
    """
    os.makedirs(work_dir, exist_ok=True)
    out = []
    for i, (a, b) in enumerate(spans):
        wav = os.path.join(work_dir, f"region_{i:03d}.wav")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-ss", f"{a:.3f}", "-i", media_path, "-t", f"{b - a:.3f}",
             "-vn", "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", wav],
            check=True)
        out.append((wav, a))
    return out
