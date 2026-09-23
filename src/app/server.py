"""FastAPI 後端：服務編輯器 UI、媒體串流(Range)、開檔、下載(含進度)、轉錄管線。"""
import os
import uuid
import shutil
import threading
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import cli, regions, download

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")

app = FastAPI(title="字幕工作室 Subtitle Studio")
_media = {}    # id -> 絕對路徑（避免前端直接傳任意路徑給檔案 API）
_jobs = {}     # job_id -> 下載進度/結果

app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/media/{mid}")
def media(mid: str):
    """串流媒體（FileResponse 自動支援 Range，供 <video> 拖曳定位）。"""
    path = _media.get(mid)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "media not found")
    return FileResponse(path)


def _safe_dur(path):
    try:
        return regions.ffprobe_duration(path)
    except Exception:
        return None


class OpenReq(BaseModel):
    path: str


@app.post("/open")
def open_media(req: OpenReq):
    """註冊本機媒體檔，回傳 id 與 metadata。"""
    if not os.path.isfile(req.path):
        raise HTTPException(404, f"file not found: {req.path}")
    mid = uuid.uuid4().hex[:12]
    _media[mid] = os.path.abspath(req.path)
    return {"id": mid, "name": os.path.basename(req.path), "duration": _safe_dur(req.path)}


@app.post("/upload")
def upload_media(file: UploadFile = File(...)):
    """使用者選擇的本機影片/音檔上傳，存到 uploads/ 後註冊（sync→threadpool，不卡事件迴圈）。"""
    up_dir = os.path.join(os.getcwd(), "uploads")
    os.makedirs(up_dir, exist_ok=True)
    name = os.path.basename(file.filename or "media")
    dst = os.path.join(up_dir, name)
    with open(dst, "wb") as f:
        shutil.copyfileobj(file.file, f)
    mid = uuid.uuid4().hex[:12]
    _media[mid] = os.path.abspath(dst)
    return {"id": mid, "name": name, "duration": _safe_dur(dst)}


# ----------------------------- 下載（背景 + 進度輪詢） ----------------------------- #
class DownloadReq(BaseModel):
    url: str
    download_only: bool = False     # 純下載：下載完不自動進編輯


def _run_download(job_id, url):
    out_dir = os.path.join(os.getcwd(), "downloads")

    def hook(d):
        st = d.get("status")
        if st == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            pct = (done / total * 100.0) if total else 0.0
            _jobs[job_id].update(status="downloading", percent=round(pct, 1),
                                 speed=d.get("speed"), eta=d.get("eta"))
        elif st == "finished":
            _jobs[job_id].update(status="processing", percent=99.0)   # 合併/faststart 中

    try:
        info = download.fetch(url, out_dir, progress_hook=hook)
        path = info.get("path")
        if not path or not os.path.isfile(path):
            _jobs[job_id].update(status="error", error="下載未產生檔案")
            return
        mid = uuid.uuid4().hex[:12]
        _media[mid] = os.path.abspath(path)
        _jobs[job_id].update(status="done", percent=100.0, id=mid,
                             name=os.path.basename(path),
                             duration=info.get("duration") or _safe_dur(path))
    except Exception as e:   # noqa: BLE001
        _jobs[job_id].update(status="error", error=str(e))


@app.post("/download")
def start_download(req: DownloadReq):
    """啟動背景下載，立即回傳 job_id；前端輪詢 /download_status。"""
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {"status": "starting", "percent": 0.0, "download_only": req.download_only}
    threading.Thread(target=_run_download, args=(job_id, req.url), daemon=True).start()
    return {"job": job_id}


@app.get("/download_status/{job_id}")
def download_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


# ----------------------------- 轉錄 ----------------------------- #
class TranscribeReq(BaseModel):
    id: str
    regions: list = []          # [[start,end], ...]；空＝整檔
    show_lang: bool = True
    engine: str = "whisperx"    # whisperx 或 hybrid(VibeVoice 最佳文字)
    model: str = "large-v3"     # 僅 whisperx 引擎用：large-v3 / large-v3-turbo
    srt_mode: str = "natural"   # natural(自然分句) 或 oneline(一句一行去標點)


@app.post("/transcribe")
def transcribe(req: TranscribeReq):
    path = _media.get(req.id)
    if not path:
        raise HTTPException(404, "media id not registered")
    region_list = [(float(r[0]), float(r[1])) for r in req.regions]
    work = os.path.splitext(path)[0] + "_work"
    srt, seg = cli.transcribe_media(path, region_list, work, show_lang=req.show_lang,
                                    engine=req.engine, model=req.model, srt_mode=req.srt_mode)
    return {"srt": srt, "segments": seg}
