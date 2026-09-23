"""橋接重用上游 VibeVoice 專案的 gen_srt（已收納於 app.vendor.vibevoice，只 import 不改）。"""

try:
    # 上游純 stdlib 腳本，import 安全；不再依賴參考目錄的 sys.path
    from app.vendor.vibevoice.gen_srt import is_tag as _is_tag, fmt as _fmt
except Exception:  # 萬一 vendor 檔案缺失，退回本地實作
    _is_tag = None
    _fmt = None


def is_tag(text):
    """判斷是否為 [Music]/[唱歌] 這類標籤 cue（整段一條、不逐字切）。"""
    if _is_tag is not None:
        return _is_tag(text)
    t = (text or "").strip()
    return t.startswith("[") and t.endswith("]")


def fmt(seconds):
    """秒 → SRT 時間碼 HH:MM:SS,mmm。"""
    if _fmt is not None:
        return _fmt(seconds)
    if seconds is None or seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
