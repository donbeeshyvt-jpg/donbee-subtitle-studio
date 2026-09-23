"""v2 本機 API；HTTP 層不載入模型，長工作由 coordinator 接手。"""
from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import hashlib
import hmac
import importlib.metadata
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from app import config as app_config

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import copy

from . import model_store, provider_secrets
from .fsutil import replace_with_retry
from .contracts import (ProjectRequest, SourceRequest, SequenceRequest, JobRequest, WorkflowRequest, ModelDownloadRequest,
                        ProviderRequest, SecretRequest, KeywordsRequest, SubtitlePreviewRequest,
                        RealtimeSessionRequest)
from .coordinator import Coordinator
from .domain import new_id
from .environment import environment_report
from .model_store import load_manifest
from .providers import (ADDED_DEFAULT_PROVIDERS, DEFAULT_PROVIDERS, ProviderError, add_new_default_providers, probe_status,
                        transcription_only, upgrade_default_providers, validate_provider_config)
from .store import Store, StudioError, canonical


TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
# 工作內容裡只給 worker 用的內部欄位（資料目錄內的絕對路徑）：API 回應一律拿掉（TEST_PLAN I07）
INTERNAL_JOB_FIELDS = {"pcm_path"}


def job_view(job):
    if not isinstance(job, dict) or not isinstance(job.get("body"), dict):
        return job
    return {**job, "body": {k: v for k, v in job["body"].items() if k not in INTERNAL_JOB_FIELDS}}


def create_app(root=None, start_workers=True):
    if root is None:
        # 首次改用專案內 data/ 時，從舊的 %LOCALAPPDATA% 資料目錄複製遷移（不刪來源）
        from .data_migration import migrate_if_needed
        migrate_if_needed(Path(app_config.resolve_data_dir()), app_config.legacy_data_dir())
    store = Store(root)
    config_path = store.root / "config.json"
    if not config_path.exists():
        config_path.write_text(json.dumps({"token": secrets.token_urlsafe(32), "roots": {}, "providers": []}), encoding="utf-8")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    token = config["token"]

    def save_config():
        # 原子寫回：設定檔只含非敏感欄位，金鑰永遠不進這裡
        staging = config_path.with_name(config_path.name + ".part")
        staging.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
        replace_with_retry(staging, config_path)

    if not config.get("providers"):
        # 首次啟動寫入四個預設供應者（不含金鑰），使用者可在介面修改
        config["providers"] = copy.deepcopy(DEFAULT_PROVIDERS)
        config["provider_defaults_added"] = list(ADDED_DEFAULT_PROVIDERS)
        save_config()
    elif any([upgrade_default_providers(config["providers"]), add_new_default_providers(config)]):
        save_config()
    provider_state = {}
    coordinator = Coordinator(store)

    @asynccontextmanager
    async def lifespan(app):
        if start_workers:
            coordinator.start()
        yield
        if start_workers:
            coordinator.close()

    app = FastAPI(title="冬比字幕工作室", version="2.0.0-dev", lifespan=lifespan)
    app.state.store, app.state.config = store, config

    def error_response(code, message, status=400, details=None):
        return JSONResponse({"error": {"code": code, "message": message, "details": details,
            "retryable": status in {429, 503}, "request_id": new_id("request")}}, status_code=status)

    @app.exception_handler(StudioError)
    async def studio_error(request, error):
        return error_response(error.code, error.message, error.status, error.details)

    @app.exception_handler(json.JSONDecodeError)
    async def invalid_json(request, error):
        # 2026-09-20 M6-2 真跑：Windows 路徑的反斜線沒跳脫時，整個請求變成 500；改成講得清楚的 400
        return error_response("INVALID_JSON", "請求內容不是合法的 JSON（Windows 路徑的反斜線要寫成 \\）", 400,
                              {"position": getattr(error, "pos", None)})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return error_response("INVALID_REQUEST", "請檢查輸入欄位", 422,
            [{"location": list(e["loc"]), "message": e["msg"]} for e in error.errors()])

    def valid_session(value):
        try:
            expiry, signature = value.split(".", 1)
            expected = hmac.new(token.encode(), expiry.encode(), hashlib.sha256).hexdigest()
            return int(expiry) > time.time() and hmac.compare_digest(signature, expected)
        except (AttributeError, ValueError):
            return False

    @app.middleware("http")
    async def local_access(request: Request, call_next):
        hostname = request.url.hostname
        if hostname not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            return error_response("ACCESS_DENIED", "服務只接受本機主機名稱", 403)
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.netloc != request.headers.get("host") or parsed.scheme != request.url.scheme:
                return error_response("ACCESS_DENIED", "請由工作室頁面操作", 403)
        if request.url.path.startswith("/v1") and request.url.path not in {"/v1/session", "/v1/health"}:
            bearer = request.headers.get("authorization", "")
            if not hmac.compare_digest(bearer, f"Bearer {token}") and not valid_session(request.cookies.get("studio_session")):
                # 網頁的連線憑證（cookie，12 小時有效）不存在或已過期：前端會自動重新取得並重送；仍失敗才需要使用者重新整理
                return error_response("ACCESS_DENIED", "與本機服務的連線憑證已過期或不存在，請重新整理頁面（F5）", 401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    def scoped(entity_id, project_id, kind):
        entity = store.get(entity_id, kind)
        with store.connect() as db:
            row = db.execute("SELECT project_id FROM entities WHERE id=?", (entity_id,)).fetchone()
        if row[0] != project_id:
            raise StudioError("NOT_FOUND", "資料不屬於此專案", 404)
        return entity

    def project_view(entity):
        return {**entity, "project_id": entity["id"], "sequence_revision": store.head(f"sequence:{entity['id']}")}

    def source_view(entity):
        result = {k: v for k, v in entity.items() if k not in {"path", "canonical_id"}}
        meta = store.head(f"source:{entity['id']}", None)
        if meta:
            result.update(store.get(meta, "source_metadata"))
        assets = store.list("asset", entity.get("project_id"), 200)["items"]
        result.update(source_id=entity["id"], asset_ids=[a["id"] for a in assets if a["source_id"] == entity["id"]])
        result["id"] = entity["id"]
        result["transcript_revision"] = store.head(f"transcript:{entity['id']}:default", None)
        return result

    def resolve_local(root_id, relative_path):
        roots = config.get("roots", {})
        if root_id not in roots:
            raise StudioError("ACCESS_DENIED", "尚未允許此素材目錄，請在設定檔加入", 403)
        base = Path(roots[root_id]).resolve()
        target = (base / relative_path).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            raise StudioError("ACCESS_DENIED", "檔案不在允許目錄內或不存在", 403)
        return target

    @app.get("/v1/session")
    def session():
        expiry = str(int(time.time()) + 43200)
        signature = hmac.new(token.encode(), expiry.encode(), hashlib.sha256).hexdigest()
        response = JSONResponse({"authenticated": True})
        response.set_cookie("studio_session", f"{expiry}.{signature}", httponly=True, samesite="strict", max_age=43200)
        return response

    @app.get("/v1/health")
    def health():
        return {"status": "ok", "schema_version": 1, "version": "2.0.0-dev", "name": "冬比字幕工作室"}

    @app.get("/v1/environment")
    def environment():
        # 環境檢查結果快取在服務內；重新檢查走 POST /v1/environment/recheck
        if getattr(app.state, "environment", None) is None:
            app.state.environment = environment_report()
        return app.state.environment

    @app.post("/v1/environment/recheck")
    def environment_recheck():
        app.state.environment = environment_report()
        return app.state.environment

    @app.get("/v1/models/status")
    def models_status():
        # 依清單比對專案 models/（含選用），並附上目前的下載進度；不連網
        result = model_store.check_models(load_manifest(), app_config.MODELS_DIR, include_optional=True)
        result["download"] = getattr(app.state, "model_download", None)
        return result

    @app.post("/v1/models/download", status_code=202)
    def models_download(body: ModelDownloadRequest):
        if not body.confirm:
            raise StudioError("CONFIRMATION_REQUIRED", "請先確認所需磁碟空間與下載時間後再開始", 422)
        manifest = load_manifest()
        known = {"hf:" + e["repo"] for e in manifest.get("hf", [])} | {"torch:" + e["file"] for e in manifest.get("torch", [])} | \
                {"gguf:" + e["dir"] for e in manifest.get("gguf", [])} | {"ct2:" + e["key"] for e in manifest.get("ct2", [])}
        unknown = [identity for identity in body.ids if identity not in known]
        if unknown:
            raise StudioError("UNKNOWN_MODEL", "清單中沒有這些模型：" + "、".join(unknown), 422)
        thread = getattr(app.state, "model_download_thread", None)
        if thread is not None and thread.is_alive():
            raise StudioError("DOWNLOAD_IN_PROGRESS", "已有模型複製或下載進行中，請等待完成", 409)
        plan = model_store.plan_download(manifest, app_config.MODELS_DIR, include_optional=True, only=body.ids)
        state = dict(download_id=os.urandom(12).hex(), status="running", ids=list(body.ids), started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     events=[], results=None, error=None, finished_at=None)
        app.state.model_download = state

        def progress(event):
            state['events'].append(event)
            del state['events'][:-100]  # 高頻進度只保留最近 100 筆，避免長下載無界成長

        def worker():
            # 在服務內背景執行；金鑰無關；失敗只記錄型別，不外洩下載回應全文
            try:
                result = model_store.run_download(plan, progress=progress)
                state.update(status="done", results=result["items"], finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            except Exception as error:
                state.update(status="failed", error=type(error).__name__, finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))

        thread = threading.Thread(target=worker, name="model-download", daemon=True)
        app.state.model_download_thread = thread
        thread.start()
        return {"download_id": state['download_id'], "status": state["status"], "ids": state["ids"], "plan": [dict(id=item["id"], action=item["action"], bytes=item.get("bytes", 0)) for item in plan["items"]]}

    @app.get("/v1/capabilities")
    def capabilities():
        from . import asr_models
        packages = {}
        for name in ("yt-dlp", "faster-whisper", "whisperx", "torch", "json5"):
            try:
                packages[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                packages[name] = None
        isolated = Path(__file__).resolve().parents[3] / ".venv-download" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        downloader = os.environ.get("STUDIO_YTDLP_PYTHON", str(isolated) if isolated.exists() else None)
        download_runtime = {"isolated": bool(downloader), "yt_dlp": packages["yt-dlp"], "ejs": None}
        if downloader:
            try:
                probe = subprocess.run([downloader, "-c", "import importlib.metadata as m,json; print(json.dumps({'yt_dlp':m.version('yt-dlp'),'ejs':m.version('yt-dlp-ejs')}))"],
                    capture_output=True, text=True, timeout=5, check=True)
                download_runtime.update(json.loads(probe.stdout))
            except (OSError, subprocess.SubprocessError, ValueError):
                download_runtime["error"] = "DOWNLOAD_RUNTIME_UNAVAILABLE"
        from .model_store import list_gguf
        def in_project(path):
            try:
                return Path(path).resolve().is_relative_to(app_config.PROJECT_ROOT)
            except (OSError, ValueError):
                return False
        # 路徑資訊：一律相對專案根解析，讓介面與 doctor 能顯示資料、模型、環境的實際來源
        paths = {"project_root": str(app_config.PROJECT_ROOT), "data_dir": str(store.root), "models_dir": app_config.MODELS_DIR,
            "hf_cache": app_config.HF_CACHE_DIR, "torch_home": app_config.TORCH_HOME_DIR, "gguf_dir": app_config.GGUF_DIR,
            "gguf_models": list_gguf(app_config.MODELS_DIR), "vibevoice_root": app_config.VIBEVOICE_ROOT, "python": sys.executable,
            "migrated_from": (store.root / "MIGRATED_FROM.json").is_file()}
        paths["in_project"] = {key: in_project(paths[key]) for key in ("data_dir", "models_dir", "hf_cache", "torch_home", "vibevoice_root", "python")}
        return {"tools": {name: bool(shutil.which(name)) for name in ("ffmpeg", "ffprobe", "node")}, "download_runtime": download_runtime,
            "paths": paths,
            "packages": packages, "profiles": ["draft", "balanced", "quality"], "formats": ["srt", "vtt", "transcript_json", "llc", "csv", "mp4", "audio"],
            "subtitle_options": {"sentences_per_cue": [1, 2], "keep_punctuation_default": False},
            "output_roots": root_items(),
            "asr_models": asr_models.catalog(config=config, root=store.root),  # 精修可選的辨識模型與安裝狀態（Breeze-ASR、OpenRouter 遠端轉錄）
            "default_asr_model": asr_models.default_model(config),  # 沒指定時實際會用的精修模型
            "source_subtitles": True, "providers": [{"id": p["id"], "model": p.get("model")} for p in config.get("providers", [])]}

    def root_items():
        # target＝檔案實際會放的資料夾：一律是 subtitle_studio（位置本身就叫 subtitle_studio 時不再多套一層）
        from .destinations import STUDIO_FOLDER
        def target(value):
            return value if Path(value).name.lower() == STUDIO_FOLDER else str(Path(value) / STUDIO_FOLDER)
        return [{"id": key, "name": config.get("root_names", {}).get(key, key), "path": value, "target": target(value),
                 "kind": config.get("root_kinds", {}).get(key, "folder")} for key, value in config.get("roots", {}).items()]

    def register_root(path, name=None, kind="folder"):
        """登記輸出資料夾（同一路徑只登記一次）；sidecar＝匯入檔旁自動建立的 subtitle_studio。
        不論哪一種，檔案都平放在 subtitle_studio 裡、檔名跟著素材檔名（見 destinations.delivery_folder）。"""
        resolved = str(Path(path).resolve())
        roots = config.setdefault("roots", {})
        names = config.setdefault("root_names", {})
        kinds = config.setdefault("root_kinds", {})
        identity = next((key for key, value in roots.items() if value == resolved), None) or "root_" + hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:10]
        roots[identity] = resolved
        if name:
            names[identity] = name[:60]
        elif identity not in names:
            names[identity] = Path(resolved).name or resolved
        if kind == "sidecar" or identity not in kinds:
            kinds[identity] = kind
        save_config()
        return identity

    @app.post("/v1/dialogs/{kind}")
    async def open_native_dialog(kind: str, request: Request):
        # 由後端開原生視窗選資料夾／檔案（瀏覽器拿不到本機絕對路徑）；沒有桌面時回 501，介面退回手動輸入
        from . import dialogs
        if kind not in ("folder", "file"):
            raise StudioError("NOT_FOUND", "沒有這種視窗", 404)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        title = body.get("title") if isinstance(body.get("title"), str) else None
        initial = body.get("initial") if isinstance(body.get("initial"), str) else None
        try:
            chosen = await asyncio.to_thread(dialogs.native_dialog, kind, title, initial)
        except dialogs.DialogUnavailable as error:
            raise StudioError("DIALOG_UNAVAILABLE", "這台電腦無法開啟選擇視窗，請改用手動輸入路徑", 501, {"reason": str(error)[:200]}) from None
        return {"path": chosen, "cancelled": chosen is None}

    @app.post("/v1/projects/{project_id}/sources/local-file", status_code=201)
    async def import_local_file(project_id: str, request: Request):
        # 依路徑匯入本機檔：不複製、不改原檔；輸出預設放在同資料夾的 subtitle_studio
        store.get(project_id, "project")
        body = await request.json()
        raw = body.get("path")
        if not isinstance(raw, str) or not raw.strip():
            raise StudioError("INVALID_REQUEST", "請提供檔案路徑", 422)
        target = Path(raw.strip())
        if not target.is_absolute() or not target.is_file():
            raise StudioError("INVALID_REQUEST", "找不到這個檔案，請重新選擇", 422)
        if target.suffix.lower() not in {".mp4", ".mkv", ".webm", ".mov", ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}:
            raise StudioError("UNSUPPORTED_MEDIA", "請選擇影片或音訊檔", 422)
        target = target.resolve()
        sidecar = target.parent / "subtitle_studio"
        try:
            sidecar.mkdir(exist_ok=True)
            root_id = register_root(sidecar, name=f"{target.parent.name or target.anchor}／subtitle_studio", kind="sidecar")
        except OSError:
            root_id = None  # 唯讀位置：照常匯入，只是沒有預設輸出資料夾
        identity = new_id("local")
        source = store.create("source", {"kind": "local", "path": str(target), "title": target.name, "project_id": project_id,
            "canonical_id": identity, "origin_dir": str(target.parent), "default_output_root_id": root_id}, project_id)
        job = store.submit(project_id, {"kind": "probe", "source_id": source["id"]}, "cpu")
        return {**source_view(source), "job_id": job["id"]}

    @app.post("/v1/output-roots", status_code=201)
    async def add_output_root(request: Request):
        # 使用者指定本地輸出資料夾：只接受絕對路徑；不存在時要明確 create 才建立；同一路徑只登記一次
        body = await request.json()
        raw = body.get("path")
        if not isinstance(raw, str) or not raw.strip():
            raise StudioError("INVALID_REQUEST", "請提供資料夾路徑", 422)
        candidate = Path(raw.strip())
        if not candidate.is_absolute():
            raise StudioError("INVALID_REQUEST", "請提供完整（絕對）路徑，例如 D:\\輸出\\字幕", 422)
        if not candidate.exists():
            if body.get("create") is not True:
                raise StudioError("OUTPUT_ROOT_MISSING", "資料夾不存在；勾選「不存在就建立」或先建立資料夾", 422)
            try:
                candidate.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise StudioError("OUTPUT_ROOT_MISSING", f"無法建立資料夾：{error.strerror or error}", 422) from None
        if not candidate.is_dir():
            raise StudioError("INVALID_REQUEST", "路徑不是資料夾", 422)
        name = body.get("name")
        identity = register_root(candidate, name.strip() if isinstance(name, str) and name.strip() else None)
        return {"items": root_items(), "id": identity}

    @app.delete("/v1/output-roots/{root_id}")
    def remove_output_root(root_id: str):
        if root_id not in config.get("roots", {}):
            raise StudioError("NOT_FOUND", "沒有這個輸出資料夾", 404)
        config["roots"].pop(root_id, None)
        config.get("root_names", {}).pop(root_id, None)
        config.get("root_kinds", {}).pop(root_id, None)
        save_config()
        return {"items": root_items()}

    @app.post("/v1/projects", status_code=201)
    def create_project(body: ProjectRequest):
        return project_view(store.create("project", {"name": body.name, "schema_version": 2}))

    @app.get("/v1/projects")
    def projects(limit: int = 50, cursor: int = 0):
        result = store.list("project", limit=limit, cursor=cursor)
        result["items"] = [project_view(p) for p in result["items"]]
        return result

    @app.get("/v1/projects/{project_id}")
    def project(project_id: str):
        result = project_view(store.get(project_id, "project"))
        result["sources"] = [source_view(s) for s in store.list("source", project_id, 200)["items"]]
        return result

    @app.post("/v1/projects/{project_id}/sources", status_code=201)
    def add_source(project_id: str, body: SourceRequest):
        store.get(project_id, "project")
        if body.kind == "youtube":
            from .media import canonical_youtube_url
            try:
                url = canonical_youtube_url(body.url)
            except ValueError as error:
                raise StudioError("INVALID_REQUEST", str(error), 422)
            identity = url
            data = {"kind": "youtube", "url": url, "title": url, "project_id": project_id}
        else:
            path = resolve_local(body.root_id, body.relative_path)
            identity = str(path)
            data = {"kind": "local", "path": str(path), "title": path.name, "project_id": project_id}
        for source in store.list("source", project_id, 200)["items"]:
            if source.get("canonical_id") == identity:
                return source_view(source)
        source = store.create("source", {**data, "canonical_id": identity}, project_id)
        return source_view(source)

    @app.get("/v1/projects/{project_id}/sources")
    def sources(project_id: str, limit: int = 50, cursor: int = 0):
        store.get(project_id, "project")
        result = store.list("source", project_id, limit, cursor)
        result["items"] = [source_view(s) for s in result["items"]]
        return result

    @app.post("/v1/projects/{project_id}/uploads", status_code=201)
    async def upload(project_id: str, file: UploadFile = File(...)):
        store.get(project_id, "project")
        identity = new_id("upload")
        directory = store.root / "uploads"
        directory.mkdir(exist_ok=True)
        suffix = Path(file.filename or "media").suffix.lower()
        if suffix not in {".mp4", ".mkv", ".webm", ".mov", ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}:
            raise StudioError("UNSUPPORTED_MEDIA", "請選擇影片或音訊檔", 422)
        path = directory / f"{identity}{suffix}"
        staging = path.with_suffix(path.suffix + ".staging")
        size = 0
        try:
            with staging.open("xb") as stream:
                while data := await file.read(1024 * 1024):
                    size += len(data)
                    if size > config.get("max_upload_bytes", 20 * 1024**3):
                        raise StudioError("UPLOAD_TOO_LARGE", "上傳超過允許容量", 413)
                    stream.write(data)
            replace_with_retry(staging, path)
        except BaseException:
            staging.unlink(missing_ok=True)
            raise
        source = store.create("source", {"kind": "local", "path": str(path), "title": file.filename,
            "project_id": project_id, "canonical_id": identity}, project_id)
        job = store.submit(project_id, {"kind": "probe", "source_id": source["id"]}, "cpu")
        return {**source_view(source), "job_id": job["id"]}

    @app.get("/v1/library")
    def library(root_id: str, relative_path: str = ""):
        if root_id not in config.get("roots", {}):
            raise StudioError("ACCESS_DENIED", "未允許此目錄", 403)
        base = Path(config["roots"][root_id]).resolve()
        target = (base / relative_path).resolve()
        if not target.is_relative_to(base) or not target.is_dir():
            raise StudioError("ACCESS_DENIED", "目錄越界或不存在", 403)
        return {"items": [{"name": p.name, "relative_path": str(p.relative_to(base)), "is_directory": p.is_dir()}
                          for p in sorted(target.iterdir())[:200] if p.resolve().is_relative_to(base)]}

    @app.get("/v1/projects/{project_id}/sequence")
    def sequence(project_id: str):
        store.get(project_id, "project")
        revision = store.head(f"sequence:{project_id}")
        return {"revision": "seq_00", "items": [], "source_id": None} if revision == "seq_00" else store.get(revision, "sequence")

    @app.put("/v1/projects/{project_id}/sequence")
    def set_sequence(project_id: str, body: SequenceRequest):
        scoped(body.source_id, project_id, "source")
        if len({i.id for i in body.items}) != len(body.items):
            raise StudioError("INVALID_REQUEST", "每個片段需要獨立識別碼", 422)
        if body.proposal_id:
            proposal = scoped(body.proposal_id, project_id, "edit_proposal")
            source = store.get(body.source_id, "source")
            latest_transcript = store.head(f"transcript:{source['id']}:default", None)
            if (body.base_transcript_revision != latest_transcript or
                body.base_transcript_revision != proposal.get("transcript_revision") or
                body.base_revision != proposal.get("base_sequence_revision") or
                body.map_revision != proposal.get("map_revision")):
                raise StudioError("REVISION_CONFLICT", "剪輯提案依據已變更，請重新產生或合併", 409)
        return store.revise(f"sequence:{project_id}", "sequence", {"source_id": body.source_id,
            "items": [i.model_dump() for i in body.items]}, body.base_revision, project_id)

    @app.post("/v1/projects/{project_id}/jobs", status_code=202)
    def submit_job(project_id: str, body: JobRequest, request: Request):
        if body.kind in {"correct", "summarize", "plan_edits"} and body.provider_id:
            # 只做轉錄的供應者（ElevenLabs）不能拿來做文字工作：先擋，不必等查版本
            reject_transcription_only(next((p for p in config.get("providers", []) if p["id"] == body.provider_id), None))
        if body.output_root_id:
            from .destinations import output_root
            output_root(config, body.output_root_id)
        for identity, kind in ((body.source_id, "source"), (body.sequence_revision, "sequence"),
                               (body.transcript_revision, "transcript"), (body.alignment_revision, "alignment")):
            if identity:
                scoped(identity, project_id, kind)
        if body.kind in {"workflow", "import"}:
            raise StudioError("UNSUPPORTED_OPERATION", "請使用對應工作計畫或匯入入口", 422)
        # 選用的精修模型（Breeze-ASR）：只用在精修那一趟；沒安裝就在排隊前回清楚的錯誤，不讓工作跑到一半才失敗
        from . import asr_models
        if body.kind == "analyze" and body.asr_model is None and body.profile in ("balanced", "quality"):
            # 沒指定精修模型：套服務預設，並寫進工作內容，之後看得出實際用了哪個模型；快速草稿不做精修，不套
            body = body.model_copy(update={"asr_model": asr_models.default_model(config)})
        # 要檢查的精修模型：analyze／refine 本身，或下載完成後自動轉錄（follow_up）要用的
        follow = body.follow_up if body.kind == "acquire" else None
        chosen = (body.asr_model if body.kind == "analyze" else body.model if body.kind == "refine"
                  else follow.asr_model if follow is not None else None)
        profile = follow.profile if follow is not None else body.profile
        consent = follow.remote_consent if follow is not None else body.remote_consent
        if asr_models.is_remote(chosen):
            # 遠端轉錄（OpenRouter、ElevenLabs）：聲音會送出本機 → 明確同意＋金鑰，缺一個就在排隊前擋下（下載前就擋，不白下載）
            name = asr_models.source_label(chosen)
            if body.kind in ("analyze", "acquire") and profile == "draft":
                raise StudioError("ASR_MODEL_NEEDS_REFINE", "遠端轉錄用在精修那一趟：請把辨識品質選「平衡」或「精修」", 422)
            if not consent:
                raise StudioError("REMOTE_CONSENT_REQUIRED", f"要把音訊傳到 {name} 轉錄，需先在「模型設定」同意使用遠端服務", 422)
            if asr_models.remote_settings(config, store.root, chosen) is None:
                from .remote_asr import transcription_models
                provider = asr_models.remote_provider(config, chosen)
                model = asr_models.parse_remote(chosen)[1]
                if provider is not None and model and model not in transcription_models(provider):
                    raise StudioError("MODEL_NOT_INSTALLED", f"{name} 沒有登記轉錄模型 {model}：到「模型設定 → 文字模型供應者」新增後再選", 409, {"model": chosen})
                raise StudioError("MODEL_NOT_INSTALLED", f"尚未設定 {name} 金鑰或未開啟遠端服務：到「模型設定 → 文字模型供應者」設定 {name} 的金鑰", 409, {"model": chosen})
        elif chosen and asr_models.entry(chosen):
            if body.kind in ("analyze", "acquire") and profile == "draft":
                raise StudioError("ASR_MODEL_NEEDS_REFINE", "Breeze 模型用在精修那一趟：請把辨識品質選「平衡」或「精修」", 422)
            asr_models.resolve(chosen)
        resource = "download" if body.kind in {"acquire", "acquire_subtitles"} else "gpu" if body.kind in {"analyze", "align", "refine"} else "cpu"
        if body.kind in {"correct", "summarize", "plan_edits"}:
            provider = next((p for p in config.get("providers", []) if p["id"] == body.provider_id), None)
            if body.provider_id and provider is None:
                raise StudioError("PROVIDER_NOT_FOUND", "尚未配置此文字模型", 422)
            resource = "gpu" if provider and provider.get("gpu_ownership", "external") != "cpu" else "provider"
        job = store.submit(project_id, body.model_dump(mode="json"), resource,
                           request.headers.get("idempotency-key"))
        return {**job_view(job), "links": {"self": f"/v1/jobs/{job['id']}", "events": f"/v1/jobs/{job['id']}/events"}}

    @app.post("/v1/projects/{project_id}/subtitles/preview")
    def subtitle_preview(project_id: str, body: SubtitlePreviewRequest):
        """網頁字幕預覽：與匯出 SRT 同一段計算（subtitles.build），回傳每一則的 SRT 時間與文字，以及整份 SRT 內容。
        對齊版本：有指定就用；沒指定就用這一版逐字稿已完成的逐詞對齊；還沒對齊就用句子時間並標 alignment=pending。"""
        from . import subtitles
        from .alignment_binding import validate_binding
        from .worker import alignment_state
        transcript = scoped(body.transcript_revision, project_id, "transcript")
        if body.sequence_revision:
            sequence = scoped(body.sequence_revision, project_id, "sequence")
            items = [i for i in sequence["items"] if i["kind"] == "clip" and i.get("selected", True)]
            source_id = sequence["source_id"]
        else:
            source_id = body.source_id or transcript["source_id"]
            items = [{"id": f"range_{index + 1}", "kind": "clip", "start_us": span.start_us, "end_us": span.end_us, "selected": True}
                     for index, span in enumerate(body.ranges)]
        if not items:
            raise StudioError("EMPTY_SEQUENCE", "請先選擇要預覽的範圍", 422)
        if transcript["source_id"] != source_id:
            raise StudioError("SOURCE_MISMATCH", "逐字稿與剪輯來源不一致", 422)
        state, revision = ("word", body.alignment_revision) if body.alignment_revision else alignment_state(store, project_id, transcript)
        cues = transcript["cues"]
        if revision:
            alignment = scoped(revision, project_id, "alignment")
            validate_binding(transcript, alignment, [store.get(a, "asset") for a in transcript.get("asset_ids", [])])
            cues = alignment["cues"]
        mappings = subtitles.plain_mappings(items, body.grouping, body.subtitle_timebase)
        try:
            [laid] = subtitles.build(cues, items, mappings, timebase=body.subtitle_timebase, grouping=body.grouping,
                                     sentences_per_cue=body.sentences_per_cue, keep_punctuation=body.keep_punctuation, show_language=body.show_language)
            srt = subtitles.to_srt(laid)
        except ValueError as error:
            raise StudioError("SUBTITLE_LAYOUT_FAILED", f"字幕排版失敗：{error}", 422) from None
        return {"transcript_revision": transcript["id"], "alignment": state, "alignment_revision": revision, "timebase": body.subtitle_timebase,
                "entries": subtitles.entry_view(laid, items, mappings), "srt": srt,
                "quality": subtitles.quality_report(laid, cues),
                "warnings": sorted({w for c in laid for w in c.get("warnings", [])})}

    @app.get("/v1/projects/{project_id}/jobs")
    def jobs(project_id: str, limit: int = 50, cursor: int = 0):
        if not 1 <= limit <= 200 or cursor < 0:
            raise StudioError("INVALID_REQUEST", "分頁引數無效", 422)
        result = store.jobs(project_id, limit, cursor)
        return {**result, "items": [job_view(item) for item in result["items"]]}

    @app.get("/v1/jobs/{job_id}")
    def job(job_id: str):
        return job_view(store.job(job_id))

    @app.post("/v1/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        result = store.cancel(job_id)
        return JSONResponse(job_view(result), status_code=200 if result["status"] in TERMINAL else 202)

    @app.patch("/v1/jobs/{job_id}/control")
    async def control(job_id: str, request: Request):
        body = await request.json()
        if set(body) - {"priority", "dispatch_paused"}:
            raise StudioError("INVALID_REQUEST", "未知派工設定", 422)
        return job_view(store.control(job_id, **body))

    @app.post("/v1/jobs/{job_id}/retry", status_code=202)
    def retry(job_id: str):
        previous = store.job(job_id)
        if previous["status"] not in {"failed", "interrupted", "cancelled"}:
            raise StudioError("JOB_NOT_RETRYABLE", "此工作尚不可重試", 409)
        # 重試有上限（TEST_PLAN F03）：連續失敗到上限就停，請先處理錯誤原因，避免無限重試
        if previous["attempt"] >= Store.MAX_ATTEMPTS:
            raise StudioError("JOB_RETRY_LIMIT", f"此工作已重試 {previous['attempt']} 次（上限 {Store.MAX_ATTEMPTS} 次）。請先看錯誤訊息處理原因，再重新提交一次新的工作。", 409,
                              {"attempt": previous["attempt"], "max_attempts": Store.MAX_ATTEMPTS, "error": previous["error"]})
        return job_view(store.submit(previous["project_id"], previous["body"], previous["resource"], attempt=previous["attempt"] + 1))

    @app.delete("/v1/projects/{project_id}")
    def delete_project(project_id: str):
        """刪掉整個專案（資料列、工作、成果檔）。不可復原；還有工作在跑會回 409。"""
        return store.delete_project(project_id)

    @app.post("/v1/maintenance/cleanup")
    async def cleanup(request: Request):
        """清掉資料目錄裡沒人要的暫存（已結束工作的工作目錄、孤兒成果檔、殘留 .staging）。

        使用中的成果、排隊中／執行中的工作目錄、使用者的原始素材都不動；dry_run 只試算。
        """
        body = await request.json() if (await request.body()) else {}
        if not isinstance(body, dict) or set(body) - {"dry_run"}:
            raise StudioError("INVALID_REQUEST", "只接受 dry_run", 422)
        dry_run = body.get("dry_run", False)
        if not isinstance(dry_run, bool):
            raise StudioError("INVALID_REQUEST", "dry_run 必須為布林值", 422)
        return store.cleanup(dry_run=dry_run)

    @app.get("/v1/jobs/{job_id}/events")
    async def events(job_id: str, request: Request):
        store.job(job_id)
        try:
            after = int(request.headers.get("last-event-id", "0"))
        except ValueError:
            raise StudioError("INVALID_REQUEST", "事件編號無效", 422)
        async def stream():
            cursor = after
            while not await request.is_disconnected():
                rows = store.events(job_id, cursor)
                for event in rows:
                    cursor = event["id"]
                    yield f"id: {cursor}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if store.job(job_id)["status"] in TERMINAL and len(rows) < 200:
                    break
                yield ": heartbeat\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/v1/assets/{asset_id}")
    def asset(asset_id: str):
        return {k: v for k, v in store.get(asset_id, "asset").items() if k != "path"}

    @app.get("/v1/assets/{asset_id}/content")
    def asset_content(asset_id: str):
        data = store.get(asset_id, "asset")
        return FileResponse(data["path"])

    @app.get("/v1/assets/{asset_id}/peaks")
    def peaks(asset_id: str):
        data = store.get(asset_id, "asset")
        if not data.get("peaks"):
            raise StudioError("PEAKS_NOT_READY", "波形尚未產生", 409)
        return data["peaks"]

    @app.get("/v1/assets/{asset_id}/keyframes")
    def keyframes(asset_id: str):
        from .media import keyframes as get_keyframes
        return {"items": get_keyframes(store.get(asset_id, "asset")["path"])}

    @app.get("/v1/artifacts/{artifact_id}")
    def artifact(artifact_id: str):
        return store.get(artifact_id, "artifact")

    @app.get("/v1/artifacts/{artifact_id}/content")
    def artifact_content(artifact_id: str):
        # 下載檔名：匯出時算好的「素材名[_NN].副檔名」（與輸出到資料夾同規則）；舊成果或其他工作沒有就用內部檔名
        artifact = store.get(artifact_id)
        name = artifact["filename"]
        if artifact.get("job_id"):
            try:
                name = ((store.job(artifact["job_id"]).get("result") or {}).get("download_names") or {}).get(artifact_id) or name
            except StudioError:
                pass
        return FileResponse(store.artifact_path(artifact_id), filename=name)

    @app.get("/v1/projects/{project_id}/transcripts/{revision_id}")
    def transcript(project_id: str, revision_id: str, limit: int = 50, cursor: int = 0,
                   start_us: int | None = None, end_us: int | None = None):
        data = scoped(revision_id, project_id, "transcript")
        if not 1 <= limit <= 200 or cursor < 0:
            raise StudioError("INVALID_REQUEST", "分頁引數無效", 422)
        cues = [c for c in data["cues"] if (start_us is None or c["end_us"] > start_us)
                and (end_us is None or c["start_us"] < end_us)]
        return {**data, "cues": cues[cursor:cursor + limit], "total": len(cues),
                "next_cursor": cursor + limit if cursor + limit < len(cues) else None}

    @app.post("/v1/projects/{project_id}/transcripts/{revision_id}/edits", status_code=201)
    async def edit_transcript(project_id: str, revision_id: str, request: Request):
        from copy import deepcopy
        original = scoped(revision_id, project_id, "transcript")
        body = await request.json()
        if body.get("base_revision") != revision_id:
            raise StudioError("REVISION_CONFLICT", "請提供目前文字版本", 409)
        edits = body.get("edits", [])
        origin = body.get("origin", "manual")
        if origin not in ("manual", "llm_correction", "llm_revert", "cli", "import", "other"):
            raise StudioError("INVALID_REQUEST", "origin 只能是 manual／llm_correction／llm_revert／cli／import／other", 422)
        job_id = body.get("job_id")
        note = body.get("note")
        if (job_id is not None and not isinstance(job_id, str)) or (note is not None and not isinstance(note, str)):
            raise StudioError("INVALID_REQUEST", "job_id 與 note 必須是字串", 422)
        cues = deepcopy(original["cues"])
        lookup = {c["id"]: c for c in cues}
        recorded = []
        for edit in edits:
            if set(edit) - {"cue_id", "text", "replacement_text"} or edit.get("cue_id") not in lookup:
                raise StudioError("INVALID_REQUEST", "修正只能指定有效句子與文字", 422)
            text = edit.get("text", edit.get("replacement_text"))
            if not isinstance(text, str) or not text.strip():
                raise StudioError("INVALID_REQUEST", "字幕文字不可為空", 422)
            cue = lookup[edit["cue_id"]]
            before = cue.get("accepted_text") or cue.get("raw_text") or cue.get("text", "")
            recorded.append({"cue_id": edit["cue_id"], "before": before, "after": text})
            cue.update(accepted_text=text, alignment_status="stale", edited=True)
        scope = f"transcript:{original['source_id']}:{original.get('audio_track_id', 'default')}"
        # 每一版都記下這一步改了什麼（來源：逐句編輯／LLM 套用／還原／CLI），history 端點可整段匯出
        change = {"origin": origin, "job_id": job_id, "note": note or "", "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "edits": recorded}
        return store.revise(scope, "transcript", {**original, "cues": cues, "change": change}, revision_id, project_id, initial=None)

    @app.get("/v1/projects/{project_id}/transcripts/{revision_id}/history")
    def transcript_history(project_id: str, revision_id: str):
        """從 WhisperX 輸出的那一版一路到目前版本：每一步的來源、時間、逐句前後文字；可直接存成 JSON。"""
        from .history import build_history
        return build_history(store, scoped(revision_id, project_id, "transcript"), project_id)

    @app.get("/v1/projects/{project_id}/annotations")
    def annotations(project_id: str, limit: int = 50, cursor: int = 0):
        return store.list("annotation", project_id, limit, cursor)

    @app.get("/v1/projects/{project_id}/classifications")
    def classifications(project_id: str, source_id: str | None = None):
        rows = store.list("classification", project_id, 200)["items"]
        if source_id:
            rows = [r for r in rows if r["source_id"] == source_id]
        latest = {}
        for row in rows:
            latest[row["asset_id"]] = row
        return {"items": list(latest.values())}

    @app.post("/v1/projects/{project_id}/classifications/{classification_id}/overrides", status_code=201)
    async def classification_override(project_id: str, classification_id: str, request: Request):
        from .classification import apply_overrides
        original = scoped(classification_id, project_id, "classification")
        body = await request.json()
        if set(body) != {"overrides"} or not isinstance(body["overrides"], list):
            raise StudioError("INVALID_REQUEST", "請提供音訊分類覆寫清單", 422)
        try:
            combined = original.get("overrides", []) + [{**o, "id": o.get("id") or new_id("override")} for o in body["overrides"]]
            spans = apply_overrides(original["raw_spans"], combined)
        except (ValueError, TypeError, AttributeError) as error:
            raise StudioError("INVALID_REQUEST", str(error), 422)
        return store.create("classification", {**original, "parent_id": classification_id, "spans": spans,
            "overrides": combined}, project_id)

    def reject_transcription_only(provider):
        if transcription_only(provider):
            raise StudioError("PROVIDER_TRANSCRIPTION_ONLY", "ElevenLabs 只提供語音轉錄，不能做 AI 分析、校字或拆單詞：請選 LM Studio、llama.cpp 或 OpenRouter", 422)

    def find_provider(provider_id):
        provider = next((p for p in config.get("providers", []) if p["id"] == provider_id), None)
        if provider is None:
            raise StudioError("PROVIDER_NOT_FOUND", "找不到已配置的模型服務", 404)
        return provider

    def provider_view(provider):
        hostname = urlsplit(provider.get("base_url", "")).hostname
        env_name = provider.get("api_key_env")
        return {"id": provider["id"], "adapter": provider.get("adapter", "openai_compatible"), "model": provider.get("model"),
                "transcription_model": provider.get("transcription_model"), "transcription_models": provider.get("transcription_models"),
                "base_url": provider.get("base_url"), "gpu_ownership": provider.get("gpu_ownership", "external"),
                "response_format_mode": provider.get("response_format_mode", "text"), "reasoning_effort": provider.get("reasoning_effort"),
                "timeout_sec": provider.get("timeout_sec", 60),
                "max_retries": provider.get("max_retries", 1), "max_context_chars": provider.get("max_context_chars", 12000),
                "max_output_tokens": provider.get("max_output_tokens", 2048), "allow_remote": provider.get("allow_remote") is True,
                "local": hostname in {"localhost", "127.0.0.1", "::1"}, "api_key_env": env_name,
                "secret_configured": bool(env_name and os.environ.get(env_name)) or provider_secrets.secret_configured(store.root, provider["id"]),
                "last_probe": provider_state.get(provider["id"]) or provider.get("last_probe")}

    def validated_provider(body: ProviderRequest):
        data = body.model_dump(exclude_none=True)
        try:
            validate_provider_config(data)
        except ProviderError as error:
            raise StudioError(error.args[0], "供應者設定無效：" + error.args[0], 422)
        return data

    @app.get("/v1/providers")
    def providers():
        return {"items": [provider_view(p) for p in config.get("providers", [])]}

    @app.post("/v1/providers", status_code=201)
    def create_provider(body: ProviderRequest):
        if any(p["id"] == body.id for p in config.get("providers", [])):
            raise StudioError("PROVIDER_EXISTS", "已有相同 ID 的供應者", 409)
        data = validated_provider(body)
        config.setdefault("providers", []).append(data)
        save_config()
        return provider_view(data)

    @app.put("/v1/providers/{provider_id}")
    def update_provider(provider_id: str, body: ProviderRequest):
        find_provider(provider_id)
        data = validated_provider(body)
        data["id"] = provider_id
        config["providers"] = [data if p["id"] == provider_id else p for p in config["providers"]]
        save_config()
        return provider_view(data)

    @app.delete("/v1/providers/{provider_id}")
    def delete_provider(provider_id: str):
        find_provider(provider_id)
        config["providers"] = [p for p in config["providers"] if p["id"] != provider_id]
        save_config()
        provider_secrets.delete_secret(store.root, provider_id)
        provider_state.pop(provider_id, None)
        return {"deleted": provider_id}

    @app.put("/v1/providers/{provider_id}/secret")
    def set_provider_secret(provider_id: str, body: SecretRequest):
        find_provider(provider_id)
        try:
            provider_secrets.set_secret(store.root, provider_id, body.secret)
        except ProviderError as error:
            raise StudioError(error.args[0], "金鑰格式無效", 422)
        return {"id": provider_id, "secret_configured": True}

    @app.delete("/v1/providers/{provider_id}/secret")
    def delete_provider_secret(provider_id: str):
        find_provider(provider_id)
        provider_secrets.delete_secret(store.root, provider_id)
        return {"id": provider_id, "secret_configured": False}

    from . import realtime
    realtime_sessions = realtime.Sessions()
    app.state.realtime = realtime_sessions
    app.state.coordinator = coordinator
    # 即時字幕進行中：新的轉錄／對齊等 GPU 工作先排隊，結束後照常派出（M7-3）
    coordinator.holds["gpu"] = lambda: bool(realtime_sessions.active())

    @app.post("/v1/realtime/sessions", status_code=201)
    def realtime_create(body: RealtimeSessionRequest):
        """即時字幕與翻譯（M7）：一次只開一個（顯示卡）；批次轉錄／對齊正在跑時不開。"""
        provider, secret = None, None
        if body.translate_to and not body.provider_id:
            raise StudioError("PROVIDER_REQUIRED", "翻譯需要選擇文字模型", 422)
        if body.provider_id:
            provider = find_provider(body.provider_id)
            reject_transcription_only(provider)
            if urlsplit(provider.get("base_url", "")).hostname not in ("localhost", "127.0.0.1", "::1") and not body.remote_consent:
                raise StudioError("REMOTE_CONSENT_REQUIRED", "要把字幕送到遠端文字模型翻譯，需先在「模型設定」同意使用遠端服務", 422)
            secret = provider_secrets.resolve_secret(provider, store.root)
        if realtime_sessions.active():
            raise StudioError("REALTIME_BUSY", "已有一個即時字幕在進行，請先停止", 409)
        with store.connect() as db:
            running = db.execute("SELECT COUNT(*) FROM jobs WHERE resource='gpu' AND status='running'").fetchone()[0]
        if running:
            raise StudioError("GPU_BUSY", "顯示卡正在跑轉錄或對齊工作，完成後再開始即時字幕", 409)
        try:
            session = realtime_sessions.create(model=body.model, device=body.device, language=body.language, hints=body.hints,
                                               translate_to=body.translate_to, provider=provider, secret=secret, step_sec=body.step_sec)
        except StudioError:
            raise
        except Exception as error:
            raise StudioError("REALTIME_MODEL_UNAVAILABLE", f"即時字幕模型載入失敗：{type(error).__name__}", 503) from None
        return {"session_id": session["id"], "model": body.model, "language": body.language, "translate_to": body.translate_to,
                "provider_id": body.provider_id, "sample_rate": realtime.SAMPLE_RATE, "chunk_max_sec": realtime.MAX_CHUNK_SEC, "step_sec": body.step_sec}

    @app.post("/v1/realtime/sessions/{session_id}/audio")
    async def realtime_audio(session_id: str, request: Request):
        """送一段音訊（PCM16LE 單聲道 16 kHz，最多 5 秒）；回傳這段產生的事件（line／tentative／translation）。"""
        from starlette.concurrency import run_in_threadpool
        limit = int(realtime.MAX_CHUNK_SEC * realtime.SAMPLE_RATE * 2)
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            raise StudioError("AUDIO_CHUNK_TOO_LARGE", "一次最多送 5 秒音訊", 413)  # 先看標頭，不讀進過大的內容
        data = await request.body()
        if len(data) % 2:
            raise StudioError("INVALID_AUDIO", "音訊需為 16 位元 PCM（位元組數要是偶數）", 422)
        if len(data) > limit:
            raise StudioError("AUDIO_CHUNK_TOO_LARGE", "一次最多送 5 秒音訊", 413)
        events = await run_in_threadpool(realtime_sessions.feed, session_id, data)
        session = realtime_sessions.get(session_id)
        return {"events": events, "received_sec": round(session["engine"].received, 3)}

    @app.get("/v1/realtime/sessions/{session_id}")
    def realtime_status(session_id: str):
        session = realtime_sessions.get(session_id)
        engine = session["engine"]
        return {"session_id": session_id, "lines": [{**line, **({"translation": session["translations"][line["id"]]} if line["id"] in session["translations"] else {})}
                                                    for line in engine.lines], "stats": engine.stats()}

    @app.post("/v1/realtime/sessions/{session_id}/finish")
    async def realtime_finish(session_id: str):
        """結束：定稿剩下的字、等翻譯（最多 60 秒）、回傳全部字幕與 SRT，然後關閉工作階段。"""
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(realtime_sessions.finish, session_id)

    @app.delete("/v1/realtime/sessions/{session_id}", status_code=204)
    def realtime_close(session_id: str):
        realtime_sessions.close(session_id)

    @app.post("/v1/text/keywords")
    def text_keywords(body: KeywordsRequest):
        """內容拆解單詞：用目前選的文字模型挑專有名詞，模型不可用時退回本機規則（結果的 method 會標明）。"""
        from . import terms
        provider, secret = None, None
        if body.provider_id:
            provider = find_provider(body.provider_id)
            reject_transcription_only(provider)
            if urlsplit(provider.get("base_url", "")).hostname not in ("localhost", "127.0.0.1", "::1") and not body.remote_consent:
                raise StudioError("REMOTE_CONSENT_REQUIRED", "要把內容送到遠端文字模型，需先在「模型設定」同意使用遠端服務", 422)
            secret = provider_secrets.resolve_secret(provider, store.root)
        return terms.extract_keywords(body.text, provider=provider, secret=secret)

    @app.post("/v1/providers/{provider_id}/probe")
    def probe_provider(provider_id: str):
        provider = find_provider(provider_id)
        secret = provider_secrets.resolve_secret(provider, store.root)
        result = probe_status(provider, secret)
        provider_state[provider_id] = {key: result.get(key) for key in ("status", "detail", "checked_at", "elapsed_ms", "model_available", "structured_output")}
        # 探測摘要寫回 config.json（只有狀態碼與耗時，不含金鑰），重啟服務後介面與 doctor 仍看得到上次結果
        provider["last_probe"] = dict(provider_state[provider_id])
        save_config()
        return result

    @app.post("/v1/projects/{project_id}/plans", status_code=201)
    def create_plan(project_id: str, body: WorkflowRequest):
        if body.analysis.provider_id and not any(p["id"] == body.analysis.provider_id for p in config.get("providers", [])):
            raise StudioError("PROVIDER_NOT_FOUND", "尚未配置此文字模型", 422)
        from .destinations import output_root
        for root_id in (body.acquisition.output_root_id, body.deliverables.output_root_id):
            if root_id:
                output_root(config, root_id)
        scoped(body.source_id, project_id, "source")
        if body.sequence_revision:
            scoped(body.sequence_revision, project_id, "sequence")
        source = scoped(body.source_id, project_id, "source")
        if body.subtitles.policy in {"source", "compare"} and source["kind"] != "youtube":
            raise StudioError("SOURCE_CAPTIONS_UNAVAILABLE", "此來源沒有可取得的網路字幕", 422)
        needs_audio = body.analysis.mode != "none" or "audio" in body.deliverables.formats
        stages = ["audio.acquire"] if needs_audio else []
        if body.subtitles.policy in {"source", "compare"}:
            stages.append("source_subtitles.acquire")
        if body.subtitles.policy == "compare":
            stages.append("analyze + source_subtitles -> compare")
        if body.analysis.mode != "none":
            stages.append("audio.ready -> analyze")
        if body.acquisition.video != "none":
            stages.append("video.acquire")
        if body.subtitles.alignment == "require_word" and body.subtitles.policy != "none":
            stages.append("analyze -> align")
        if body.analysis.summary:
            stages.append("analyze -> summarize")
        stages.append("selected.results -> export")
        plan_id = new_id("plan")
        result = store.create("plan", {**body.model_dump(mode="json"), "project_id": project_id,
            "plan_id": plan_id, "revision": plan_id, "dependency_graph": stages}, project_id, plan_id)
        return result

    @app.get("/v1/projects/{project_id}/source-subtitles")
    def source_subtitles(project_id: str, source_id: str | None = None, limit: int = 50, cursor: int = 0):
        store.get(project_id, "project")
        if source_id:
            scoped(source_id, project_id, "source")
        rows = store.list("source_subtitle", project_id, limit, cursor)
        if source_id:
            rows["items"] = [r for r in rows["items"] if r["source_id"] == source_id]
        return rows

    @app.get("/v1/projects/{project_id}/plans")
    def plans(project_id: str, limit: int = 50, cursor: int = 0):
        return store.list("plan", project_id, limit, cursor)

    @app.get("/v1/plans/{plan_id}")
    def plan(plan_id: str):
        return store.get(plan_id, "plan")

    @app.post("/v1/plans/{plan_id}/run", status_code=202)
    def run_plan(plan_id: str, request: Request):
        plan = store.get(plan_id, "plan")
        if plan.get("sequence_revision") and plan["sequence_revision"] != store.head(f"sequence:{plan['project_id']}"):
            raise StudioError("REVISION_CONFLICT", "工作計畫的剪輯版本已變更", 409)
        return store.submit(plan["project_id"], {"kind": "workflow", "plan_id": plan_id, "timeout_sec": 14400},
            "workflow", key=f"plan:{plan_id}:{request.headers.get('idempotency-key', 'default')}")

    @app.get("/v1/presets")
    def presets(limit: int = 50, cursor: int = 0):
        return store.list("preset", limit=limit, cursor=cursor)

    @app.post("/v1/presets", status_code=201)
    async def create_preset(request: Request):
        body = await request.json()
        if not isinstance(body.get("name"), str) or not isinstance(body.get("settings"), dict):
            raise StudioError("INVALID_REQUEST", "預設組需要名稱與設定", 422)
        if any(k in canonical(body).lower() for k in ("api_key", "authorization", "token")):
            raise StudioError("INVALID_REQUEST", "預設組不可包含秘密資訊", 422)
        return store.create("preset", body)

    @app.post("/v1/projects/{project_id}/imports", status_code=202)
    async def import_sequence(project_id: str, request: Request):
        body = await request.json()
        if set(body) - {"asset_id", "format", "content"} or body.get("format") not in {"llc", "csv", "studio_json"}:
            raise StudioError("INVALID_REQUEST", "匯入格式無效", 422)
        scoped(body.get("asset_id"), project_id, "asset")
        if not isinstance(body.get("content"), str) or len(body["content"]) > 10000000:
            raise StudioError("INVALID_REQUEST", "匯入內容無效或過大", 422)
        return store.submit(project_id, {"kind": "import", **body,
            "base_sequence_revision": store.head(f"sequence:{project_id}")}, "cpu", request.headers.get("idempotency-key"))

    frontend = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if frontend.is_dir():
        @app.get("/")
        def home():
            return RedirectResponse("/v2/", headers={"Cache-Control": "no-store"})

        class WorkbenchFiles(StaticFiles):
            """HTML 殼不可快取（重新整理就拿到最新建置）；assets 檔名帶雜湊，可長期快取。"""
            async def get_response(self, path, scope):
                response = await super().get_response(path, scope)
                if response.headers.get("content-type", "").startswith("text/html"):
                    response.headers["Cache-Control"] = "no-store"
                return response
        app.mount("/v2", WorkbenchFiles(directory=frontend, html=True), name="workbench")
    else:
        @app.get("/")
        def pending_frontend():
            return JSONResponse({"name": "冬比字幕工作室", "message": "網頁尚未建置，請在 frontend 執行 npm run build"}, status_code=503)
    return app
