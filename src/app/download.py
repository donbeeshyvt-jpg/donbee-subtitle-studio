"""用 yt_dlp 下載 YouTube 影片為 H.264/AAC MP4（可被 WebView2/播放器直接播）。"""
import os
from app.config import ASR_SAMPLE_RATE  # noqa: F401 — 觸發 config


def probe(url):
    """只探測 metadata（不下載），供 UI 先渲染。"""
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                           "nocheckcertificate": True, "noplaylist": True}) as ydl:
        info = ydl.sanitize_info(ydl.extract_info(url, download=False))
    return {"title": info.get("title"), "duration": info.get("duration"),
            "fps": info.get("fps"), "width": info.get("width"),
            "height": info.get("height"), "thumbnail": info.get("thumbnail")}


def fetch(url, out_dir, progress_hook=None):
    """下載並回傳 {path, title, duration, fps, width, height}。path 取可靠最終路徑。"""
    import yt_dlp
    os.makedirs(out_dir, exist_ok=True)
    opts = {
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        # 先挑最高解析度/fps/碼率；H.264/AAC 只當同級時的優先（不讓編碼鎖住畫質，
        # 有 4K 就抓 4K；WebView2/Chromium 可播 VP9/AV1）
        "format_sort": ["res", "fps", "vcodec:h264", "acodec:aac", "tbr"],
        "concurrent_fragment_downloads": 4,   # 多分段平行下載，加速
        "postprocessor_args": {"merger": ["-movflags", "+faststart"]},
        "outtmpl": {"default": os.path.join(out_dir, "%(title).80B [%(id)s].%(ext)s")},
        "nocheckcertificate": True, "noplaylist": True, "continuedl": True,
        "quiet": True, "no_warnings": True,
    }
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.sanitize_info(ydl.extract_info(url, download=True))
    reqs = info.get("requested_downloads") or []
    path = reqs[0]["filepath"] if reqs else None
    return {"path": path, "title": info.get("title"), "duration": info.get("duration"),
            "fps": info.get("fps"), "width": info.get("width"), "height": info.get("height")}
