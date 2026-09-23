"""受 coordinator 管理的工作入口，模型只存在於可終止的工作程序。"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import time

from .domain import new_id, TimeRange, merge_ranges
from .store import Store, StudioError, canonical


# P-05：供應者傳輸層失敗的中文說明；暫時性失敗已依 max_retries 重試過，原稿一律不變
TRANSPORT_FAILURES = {
    "PROVIDER_RATE_LIMITED": "供應者限流（已依設定重試），原稿已保留；請稍後再試或改用其他供應者",
    "PROVIDER_SERVER_ERROR": "供應者伺服器錯誤（已依設定重試），原稿已保留；請稍後再試",
    "PROVIDER_TIMEOUT": "文字模型回應逾時（已依設定重試），原稿已保留；可提高 timeout_sec、縮小 max_context_chars，或改用較快的模型",
    "PROVIDER_CONNECTION_FAILED": "無法連線到文字模型服務，原稿已保留；請確認 LM Studio／llama-server 已啟動，或網路與 base_url 正確",
    "PROVIDER_AUTH_FAILED": "供應者認證失敗，原稿已保留；請在設定重新填入金鑰並測試連線",
    "PROVIDER_SECRET_MISSING": "此供應者尚未設定金鑰，原稿已保留；請在設定填入金鑰後再試",
    "PROVIDER_CREDITS_EXHAUSTED": "遠端帳戶餘額不足（OpenRouter 回 402），原稿已保留；請到 OpenRouter 儲值後再試，或改用本機模型",
    "PROVIDER_ATTESTATION_REQUIRED": "這個遠端模型要求帳戶先完成確認（OpenRouter：18 歲以上確認，https://openrouter.ai/settings/preferences），原稿已保留；完成後再試，或改用其他模型",
    "PROVIDER_PERMISSION_MISSING": "金鑰缺少需要的權限，原稿已保留；請到服務後台編輯這把金鑰的權限後再試（不必重填金鑰）",
}
# 供應者權限名稱 → 後台上的名稱（ElevenLabs API Keys 設定頁）
PERMISSION_NAMES = {"speech_to_text": "Speech to Text", "text_to_speech": "Text to Speech", "models_read": "Models（讀取）",
                    "user_read": "User（讀取）"}


def failure_reason(cause):
    """精修／轉錄失敗原因 → 中文說明；缺權限時說出是哪個權限、去哪裡開（2026-09-21 真跑：以前叫人重填金鑰，重填也沒用）。"""
    code = (cause or {}).get("code")
    if code == "PROVIDER_PERMISSION_MISSING":
        permission = cause.get("permission") or ""
        name = PERMISSION_NAMES.get(permission, permission or "需要的")
        return (f"金鑰沒有開「{name}」權限，原稿已保留；請到 ElevenLabs 後台 API Keys 編輯這把金鑰，勾選「{name}」後再試"
                "（不必重填金鑰；存好後可在「模型設定」按測試連線確認）")
    return TRANSPORT_FAILURES.get(code) or (cause or {}).get("message") or ""


def acquisition_signature(kind, settings):
    """取得設定的比對簽章：去掉未設定（None）的欄位、預設值視為相同、音訊不看畫質／幀率；只有會改變媒體內容的差異才算不同素材。"""
    settings = settings or {}
    policy = {k: v for k, v in (settings.get("format_policy") or {}).items() if v is not None}
    if policy.get("container") == "source":
        policy.pop("container")
    if policy.get("allow_transcode") is False:
        policy.pop("allow_transcode")
    if kind == "audio":
        policy.pop("max_height", None)
        policy.pop("max_fps", None)
    return {"format_policy": policy, "boundary_policy": settings.get("boundary_policy") or "source_seek", "quality": settings.get("quality") or "source"}


def model_output_failure(error, provider):
    """供應者錯誤 → 工作錯誤：模型輸出不符格式時給中文說明並附輸出節錄（不含金鑰）；其他錯誤碼原樣保留。"""
    code = str(error)
    if code == "MODEL_REASONING_EXHAUSTED":
        return StudioError("MODEL_REASONING_EXHAUSTED", "文字模型把輸出額度用在思考而沒有回答，原稿已保留；請在供應者設定把推理模式設為 none，或提高 max_output_tokens", 422,
                           {"provider_id": (provider or {}).get("id"), "model": (provider or {}).get("model"), "provider_error": code})
    if code == "PROVIDER_HTTP_400":
        mode = (provider or {}).get("response_format_mode", "text")
        return StudioError("PROVIDER_REJECTED_REQUEST", f"供應者拒絕此請求（HTTP 400），原稿已保留；多半是不支援目前的回覆格式模式「{mode}」（例如 LM Studio 只接受 json_schema 或 text），請在供應者設定改用其他模式並重新測試連線", 422,
                           {"provider_id": (provider or {}).get("id"), "model": (provider or {}).get("model"), "provider_error": code, "response_format_mode": mode})
    if code in TRANSPORT_FAILURES:
        return StudioError(code, TRANSPORT_FAILURES[code], 422, {"provider_id": (provider or {}).get("id"), "model": (provider or {}).get("model"), "provider_error": code})
    if code != "INVALID_MODEL_OUTPUT":
        # 其他供應者錯誤碼（如 PROVIDER_HTTP_404、CUE_EXCEEDS_CONTEXT_BUDGET）：保留原碼，加中文說明；原稿一律不變
        return StudioError(code, f"文字模型供應者回報錯誤（{code}），原稿已保留；請檢查供應者設定或測試連線", 422,
                           {"provider_id": (provider or {}).get("id"), "model": (provider or {}).get("model"), "provider_error": code})
    details = {"provider_id": (provider or {}).get("id"), "model": (provider or {}).get("model"), "provider_error": code,
               "raw_excerpt": getattr(error, "raw_excerpt", None), "chunk_index": getattr(error, "chunk_index", None)}
    return StudioError("MODEL_OUTPUT_INVALID", "文字模型輸出不符指定格式（已重試一次），原稿已保留；可改用較大的模型、縮小 max_context_chars，或改用 json_schema 模式", 422, details)


MEDIA_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts", ".mts", ".mpg", ".mpeg", ".wmv", ".flv"}
# 輸出檔名的種類標籤：<素材名>.<標籤>.<副檔名>；沒有標籤的（srt、mp4、m4a…）直接用 <素材名>.<副檔名>
DELIVERY_LABELS = {"transcript_record": "transcript", "transcript_history": "history", "export_manifest": "export_manifest",
    "transcript": "transcript_full", "project": "project", "summary_md": "summary", "chapters_txt": "chapters"}


def title_stem(title):
    """輸出檔名用的素材名：本機檔去掉影音副檔名；YouTube 標題不是檔名，不能把最後一個點之後切掉。長度上限 80，留空間給標籤與副檔名。"""
    from .destinations import safe_filename
    text = str(title or "").strip()
    if Path(text).suffix.lower() in MEDIA_SUFFIXES:
        text = text[: -len(Path(text).suffix)]
    return safe_filename(text)[:80].rstrip(" .") or "冬比輸出"


def range_label(span):
    """下載副本檔名用的時間範圍：01h50m00s-02h00m00s（Windows 檔名不能有冒號）。"""
    if not span:
        return ""
    def clock(us):
        seconds = int(us) // 1000000
        return f"{seconds // 3600:02}h{seconds // 60 % 60:02}m{seconds % 60:02}s"
    return f"{clock(span['start_us'])}-{clock(span['end_us'])}"


def delivery_names(stem, artifacts, extensions, variant="", media_job=False):
    """同一次輸出的檔名：同種類多個時加 _01、_02（同片段的影片與字幕編號一致）；影音輸出的清單另名，不蓋掉字幕頁的清單。"""
    names = []
    for artifact, extension in zip(artifacts, extensions):
        label = "media_manifest" if media_job and artifact["kind"] == "export_manifest" else DELIVERY_LABELS.get(artifact["kind"])
        same = [a["id"] for a in artifacts if a["kind"] == artifact["kind"]]
        number = f"_{same.index(artifact['id']) + 1:02}" if len(same) > 1 else ""
        names.append(f"{stem}{variant}{number}.{label}{extension}" if label else f"{stem}{variant}{number}{extension}")
    return names


def follow_up_from_analysis(analysis):
    """流程快照的分析設定 → 取得工作的 follow_up；精修引擎選擇（M0-2）一路帶到 analyze／refine。"""
    return {"kind": "analyze", "profile": analysis["mode"], "engine": analysis.get("engine", "whisperx"),
            "fallback_engine": analysis.get("fallback_engine")}


def analysis_request(follow, body, source_id, asset):
    """由取得工作的 follow_up 組 analyze 子工作；engine／fallback_engine 以 follow_up 優先，其次取得工作本身。"""
    return {"kind": "analyze", "source_id": source_id, "asset_ids": [asset["id"]],
            "profile": follow.get("profile", "draft"), "model": body.get("model", "turbo"),
            "engine": follow.get("engine", body.get("engine", "whisperx")),
            "fallback_engine": follow.get("fallback_engine", body.get("fallback_engine")),
            "device": body.get("device", "auto"), "audio_track_id": asset["audio_track_id"],
            "language_policy": body.get("language_policy", "auto_ja_zh_en"),
            "music_policy": body.get("music_policy", "conservative"),
            # 網頁選的精修模型／轉錄術語提示／遠端同意一起帶過去（沒選的不放，由 acquire 補上實際預設）
            **{key: follow[key] for key in ("asr_model", "asr_hints", "remote_consent") if follow.get(key)}}


def alignment_jobs(store, project_id, revision):
    """這一版逐字稿的整份逐詞對齊工作（新到舊）；匯出等對齊與網頁字幕預覽共用。"""
    return [j for j in store.jobs(project_id, 200)["items"]
            if j["kind"] == "align" and (j.get("body") or {}).get("transcript_revision") == revision and not (j.get("body") or {}).get("cue_ids")]


def alignment_state(store, project_id, transcript):
    """預覽用（不等待、不排工作）：('word', 對齊版本) 已對齊；('pending', None) 對齊中或尚未排；('failed', None) 最近一次失敗；
    ('not_applicable', None) 來源字幕或沒有綁定音訊。"""
    if transcript.get("audio_track_id") == "source_subtitles" or not transcript.get("asset_ids"):
        return "not_applicable", None
    jobs = alignment_jobs(store, project_id, transcript["id"])
    done = next((j for j in jobs if j["status"] == "succeeded" and (j.get("result") or {}).get("alignment_revision")), None)
    if done:
        return "word", done["result"]["alignment_revision"]
    if jobs and not any(j["status"] in {"queued", "running", "waiting"} for j in jobs):
        return "failed", None
    return "pending", None


class Worker:
    def __init__(self, store, job):
        self.store, self.job, self.body = store, job, job["body"]
        self.project_id, self.job_id = job["project_id"], job["id"]
        self.work = store.root / "jobs" / self.job_id
        self.work.mkdir(exist_ok=True)

    def stage(self, name, **payload):
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET stage=?,updated_at=? WHERE id=?", (name, time.time(), self.job_id))
        self.store.event(self.job_id, "stage.progress", {"stage": name, **payload})

    def queue_subtitle_export(self, alignment_revision):
        """對齊完成後自動輸出：整段素材的 SRT＋逐字稿 JSON＋修改紀錄（與字幕頁「匯出 SRT」相同的工作）。"""
        settings = self.body["then_export"]
        asset = self.store.get(settings["asset_id"], "asset")
        mapping = asset["source_map"][0]
        body = {"kind": "export", "source_id": asset["source_id"],
                "ranges": [{"start_us": mapping["source_start_us"], "end_us": mapping["source_start_us"] + asset["duration_us"]}],
                "transcript_revision": self.body["transcript_revision"], "formats": ["srt"], "grouping": "merge",
                "subtitle_timebase": "sequence", "alignment_policy": "allow_segment", "include_records": True,
                "sentences_per_cue": settings.get("sentences_per_cue", 1), "keep_punctuation": bool(settings.get("keep_punctuation", False))}
        if alignment_revision:
            body["alignment_revision"] = alignment_revision
        if settings.get("output_root_id"):
            body["output_root_id"] = settings["output_root_id"]
        job = self.store.submit(self.project_id, body, "cpu", parent_id=self.job_id)
        self.store.event(self.job_id, "export.queued", {"job_id": job["id"]})
        return job["id"]

    def data_artifact(self, kind, data):
        return self.store.publish(self.project_id, self.job_id, kind, canonical(data).encode(), "json")

    def source_stem(self, source_id):
        """輸出檔名用的素材名：本機檔＝檔名去副檔名；YouTube＝探測到的影片標題（來源 title 存的是網址，不能拿來當檔名）；都沒有就用專案名。"""
        try:
            fallback = self.store.get(self.project_id, "project").get("name") or ""
        except StudioError:
            fallback = ""
        try:
            source = self.store.get(source_id, "source")
        except StudioError:
            return title_stem(fallback)
        title = source.get("title") or ""
        if source.get("kind") == "youtube" or not title or title == source.get("url") or "://" in title:
            revision = self.store.head(f"source:{source_id}", None)
            try:
                title = (self.store.get(revision, "source_metadata").get("title") if revision else "") or ""
            except StudioError:
                title = ""
            if "://" in title:
                title = ""
        return title_stem(title or fallback)

    def remote_asr_settings(self):
        """精修模型是遠端轉錄（OpenRouter、ElevenLabs）時，把供應者設定、要用的轉錄模型與（後端解析的）金鑰交給 refine；其他模型回 None。"""
        from . import asr_models
        key = self.body.get("model")
        if not asr_models.is_remote(key):
            return None
        path = self.store.root / "config.json"
        config = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        return asr_models.remote_settings(config, self.store.root, key)

    def default_asr_model(self):
        """沒指定精修模型時（例如下載完成後自動轉錄的子工作）：與 API 同一規則，已安裝 Breeze-ASR-26 就用它，否則 large-v3。"""
        from . import asr_models
        path = self.store.root / "config.json"
        try:
            config = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except ValueError:
            config = {}
        return asr_models.default_model(config)

    def deliver(self, path, filename, root_id=None, overwrite=False):
        from .destinations import deliver_file
        config = json.loads((self.store.root / "config.json").read_text(encoding="utf-8"))
        return deliver_file(path, config, root_id or self.body["output_root_id"], self.project_id, self.job_id, filename, overwrite=overwrite)

    def register_asset(self, source, record, kind):
        from .media import waveform_peaks
        path = Path(record["path"])
        mapping = record.get("source_map") or {"source_start_us": 0, "source_end_us": record["duration_us"],
            "asset_start_us": 0, "asset_end_us": record["duration_us"], "status": "verified"}
        mapping = {**mapping, "id": new_id("map")}
        asset_id = new_id("asset")
        peaks = None
        if any(s.get("codec_type") == "audio" for s in record.get("probe", {}).get("streams", [])):
            peaks = waveform_peaks(path)
            peaks["source_start_us"] = mapping["source_start_us"]
            peaks["source_end_us"] = mapping["source_end_us"]
        data = {"asset_id": asset_id, "source_id": source["id"], "kind": kind, "path": str(path),
            "duration_us": record["duration_us"], "source_map": [mapping], "map_revision": mapping["id"],
            "audio_track_id": self.body.get("audio_track_id", "default"), "state": "ready", "peaks": peaks,
            "content_hash": record["sha256"], "size_bytes": record.get("size_bytes", path.stat().st_size),
            "requested_range": record.get("requested_range"), "actual_range": record.get("actual_range"),
            "warnings": record.get("warnings", []), "content_url": f"/v1/assets/{asset_id}/content"}
        data["acquisition_settings"] = {"format_policy": self.body.get("format_policy") or {},
            "boundary_policy": self.body.get("boundary_policy", "source_seek"), "quality": self.body.get("quality", "source")}
        result = self.store.create("asset", data, self.project_id, asset_id)
        self.store.event(self.job_id, "asset.ready", {k: v for k, v in result.items() if k != "path"})
        return result

    def probe(self):
        from . import media
        source = self.store.get(self.body["source_id"], "source")
        self.stage("probe")
        assets = []
        if source["kind"] == "youtube":
            metadata = media.probe_youtube(source["url"])
        else:
            record = media._record(source["path"])
            metadata = {"title": source["title"], "duration_us": record["duration_us"],
                        "streams": record["probe"]["streams"]}
            previous = [a for a in self.store.list("asset", self.project_id, 200)["items"] if a["source_id"] == source["id"]]
            if previous:
                assets = [a["id"] for a in previous]
            else:
                kind = "video" if any(s["codec_type"] == "video" for s in metadata["streams"]) else "audio"
                assets = [self.register_asset(source, record, kind)["id"]]
        scope = f"source:{source['id']}"
        self.store.revise(scope, "source_metadata", metadata, self.store.head(scope, None), self.project_id, initial=None)
        return {"source_id": source["id"], "asset_ids": assets, **metadata}

    def acquire(self):
        from . import media
        source = self.store.get(self.body["source_id"], "source")
        metadata_revision = self.store.head(f"source:{source['id']}", None)
        meta = self.store.get(metadata_revision, "source_metadata") if metadata_revision else self.probe()
        requested = [TimeRange(**r) for r in self.body.get("ranges", [])]
        spans = merge_ranges(requested, meta["duration_us"])
        if not spans:
            spans = [TimeRange(start_us=0, end_us=meta["duration_us"])]
        # 範圍有重疊會合併成一段下載（同一段不下載、轉錄兩次）；結果要講清楚（2026-09-21 真瀏覽器：使用者以為少了一段）
        range_info = {"requested_ranges": [r.model_dump() for r in requested], "downloaded_ranges": [r.model_dump() for r in spans]}
        if requested and len(spans) < len(requested):
            clock = lambda us: f"{us // 3_600_000_000:02d}:{us // 60_000_000 % 60:02d}:{us // 1_000_000 % 60:02d}"  # noqa: E731
            merged = "、".join(f"{clock(r.start_us)}–{clock(r.end_us)}" for r in spans)
            range_info["notice"] = (f"你填的 {len(requested)} 段範圍有重疊，已合併成 {len(spans)} 段下載：{merged}。"
                                    "要分開的片段，請在時間軸用入點／出點「加入片段」。")
        assets, children = [], []
        kind = self.body.get("asset_kind", "audio")
        self.stage("acquire", total_ranges=len(spans))
        def enqueue_asset(asset):
            if self.body.get("output_root_id"):
                # 下載副本：<素材名>_<起>-<迄>.<副檔名>，放進輸出位置的 subtitle_studio；同名不覆蓋（自動加 (2)）
                span = asset.get("requested_range")
                if not span and asset.get("source_map"):
                    first = asset["source_map"][0]
                    span = {"start_us": first["source_start_us"], "end_us": first["source_end_us"]}
                label = range_label(span)
                name = f"{self.source_stem(source['id'])}{'_' + label if label else ''}{Path(asset['path']).suffix}"
                delivery = self.deliver(asset["path"], name)
                deliveries.append({"asset_id": asset["id"], **delivery})
            assets.append(asset["id"])
            if self.body.get("follow_up"):
                follow = self.body["follow_up"]
                request = analysis_request(follow, self.body, source["id"], asset)
                if not request.get("asr_model") and request["profile"] != "draft":
                    request["asr_model"] = self.default_asr_model()  # 沒選：寫進實際會用的預設，之後看得出用了哪個模型
                analysis_key = hashlib.sha256(canonical(request).encode()).hexdigest()
                child = self.store.submit(self.project_id, request, "gpu", key=f"analyze:{analysis_key}", parent_id=self.job_id)
                if child["status"] in {"failed", "cancelled", "interrupted"}:
                    child = self.store.submit(self.project_id, request, "gpu", key=f"analyze:{analysis_key}:retry:{self.job_id}",
                        parent_id=self.job_id, attempt=child["attempt"] + 1)
                children.append(child["id"])
                self.store.event(self.job_id, "analysis.queued", {"asset_id": asset["id"], "job_id": child["id"]})
        deliveries = []
        def ready(record):
            enqueue_asset(self.register_asset(source, record, kind))
        settings = {"format_policy": self.body.get("format_policy") or {}, "boundary_policy": self.body.get("boundary_policy", "source_seek"),
                    "quality": self.body.get("quality", "source")}
        pending = []
        existing = self.store.list("asset", self.project_id, 200)["items"]
        for span in spans:
            cached = next((a for a in existing if a["source_id"] == source["id"] and a["kind"] == kind
                and a.get("requested_range") == span.model_dump()
                and acquisition_signature(kind, a.get("acquisition_settings")) == acquisition_signature(kind, settings)), None)
            if cached and Path(cached["path"]).is_file():
                with Path(cached["path"]).open("rb") as stream:
                    verified = hashlib.file_digest(stream, "sha256").hexdigest() == cached["content_hash"]
                if verified:
                    self.store.event(self.job_id, "cache.hit", {"kind": "asset", "asset_id": cached["id"]})
                    enqueue_asset(cached)
                    continue
            pending.append(span)
        spans = pending
        if not spans:
            return {"asset_ids": assets, "child_job_ids": children, "saved_files": deliveries, "cache_hit": True, **range_info}
        if source["kind"] == "youtube":
            media.download_youtube(source["url"], spans, kind, self.work / "media", self.body.get("format_policy"),
                                   on_asset=ready, boundary_policy=self.body.get("boundary_policy", "source_seek"))
        else:
            for index, span in enumerate(spans):
                extension = "m4a" if kind == "audio" else "mp4"
                ready(media.cut_media(source["path"], self.work / f"clip-{index}.{extension}",
                    span.start_us, span.end_us, self.body.get("boundary_policy", "source_seek").replace("source_seek", "copy"), kind))
        return {"asset_ids": assets, "child_job_ids": children, "saved_files": deliveries, **range_info}

    def analyze(self):
        from . import media
        from .asr import draft
        source_id = self.body["source_id"]
        ids = self.body.get("asset_ids") or [a["id"] for a in self.store.list("asset", self.project_id, 200)["items"] if a["source_id"] == source_id]
        if not ids:
            raise StudioError("SOURCE_RANGE_UNAVAILABLE", "請先取得要分析的音訊", 409)
        revisions = []
        classification_jobs = []
        for asset_id in ids:
            asset = self.store.get(asset_id, "asset")
            if asset["source_id"] != source_id:
                raise StudioError("SOURCE_MISMATCH", "音訊不屬於來源", 422)
            track = asset.get("audio_track_id", "default")
            cache_settings = {"content_hash": asset["content_hash"], "map_revision": asset["map_revision"],
                "model": self.body.get("model", "turbo"), "device": self.body.get("device", "auto"),
                "engine_version": importlib.metadata.version("faster-whisper"), "pipeline_version": 4,
                "music_policy": self.body.get("music_policy", "conservative"),
                "source_id": source_id, "audio_track_id": track, "language_policy": self.body.get("language_policy", "auto_ja_zh_en")}
            if self.body.get("asr_hints"):
                cache_settings["asr_hints"] = self.body["asr_hints"]  # 術語提示會改變草稿結果：換提示不能用舊快取
            cache_key = hashlib.sha256(canonical(cache_settings).encode()).hexdigest()
            cache_head = self.store.head(f"analysis-cache:{cache_key}", None)
            current_head = self.store.head(f"transcript:{source_id}:{track}", None)
            reused = None
            if cache_head:
                cached = self.store.get(cache_head, "analysis_cache")
                with Path(asset["path"]).open("rb") as stream:
                    valid = hashlib.file_digest(stream, "sha256").hexdigest() == asset["content_hash"]
                if cached["transcript_revision"] == current_head and valid:
                    revisions.append(current_head)
                    self.store.event(self.job_id, "cache.hit", {"kind": "transcript", "transcript_revision": current_head})
                    continue
                # 目前版本已被精修／人工修改換掉：草稿只看音訊與草稿設定、與精修模型無關 → 沿用同一份草稿，精修照跑
                # （2026-09-21 真跑：換精修模型重新轉錄 10 分鐘音訊，每次都重跑草稿 46–66 秒）
                if valid and cached.get("shard_id"):
                    try:
                        reused = self.store.get(cached["shard_id"], "transcript_shard")
                    except StudioError:
                        reused = None
            pcm = self.work / f"{asset_id}.wav"
            offset = asset["source_map"][0]["source_start_us"]
            coverage = {"start_us": offset, "end_us": offset + asset["duration_us"]}
            if reused:
                self.store.event(self.job_id, "cache.hit", {"kind": "draft", "shard_id": reused["id"]})
                cues = [dict(deepcopy(cue), id=new_id("cue")) for cue in reused["cues"]]  # 新版本用新的句子識別碼
                result = {"settings": reused["settings"]}
                shard = reused
            else:
                self.stage("decode", asset_id=asset_id)
                media._run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-i", asset["path"], "-vn", "-ac", "1", "-ar", "16000", str(pcm)], 300)
                self.stage("asr", asset_id=asset_id)
                previous_transcript = self.store.get(current_head, "transcript") if current_head else None
                languages = [c.get("lang") for c in previous_transcript["cues"] if c.get("lang") in {"zh", "ja", "en"}] if previous_transcript else []
                hint = max(set(languages), key=languages.count) if languages else None
                result = draft(pcm, model=self.body.get("model", "turbo"), device=self.body.get("device", "auto"),
                               language_policy=self.body.get("language_policy", "auto_ja_zh_en"), language_hint=hint,
                               progress=lambda **p: self.stage("asr", **p),
                               **({"hints": self.body["asr_hints"]} if self.body.get("asr_hints") else {}))
                cues = []
                for item in result["cues"]:
                    cue = deepcopy(item)
                    cue["source_id"] = source_id
                    cue["asset_id"] = asset_id  # 精修／對齊要用產生這句的素材重聽，不能只靠時間涵蓋
                    cue["start_us"] += offset
                    cue["end_us"] += offset
                    for word in cue["words"]:
                        for key in ("start_us", "end_us"):
                            if word.get(key) is not None:
                                word[key] += offset
                    cues.append(cue)
                shard = self.store.create("transcript_shard", {"source_id": source_id, "asset_id": asset_id,
                    "audio_track_id": track, "coverage": coverage, "cues": cues, "settings": result["settings"]}, self.project_id)
            scope = f"transcript:{source_id}:{track}"
            # 合併以最新 revision 為基底，人工接受文字不被稍晚完成的 shard 覆蓋。
            for attempt in range(5):
                base = self.store.head(scope, None)
                previous = self.store.get(base, "transcript") if base else {"cues": [], "coverage": [], "asset_ids": []}
                kept = [c for c in previous["cues"] if c.get("edited") or c["end_us"] <= coverage["start_us"] or c["start_us"] >= coverage["end_us"]]
                additions = [c for c in cues if not any(k.get("edited") and k["start_us"] < c["end_us"] and k["end_us"] > c["start_us"] for k in kept)]
                content = {"source_id": source_id, "audio_track_id": track,
                    "cues": sorted(kept + additions, key=lambda c: c["start_us"]),
                    "coverage": [r.model_dump() for r in merge_ranges([TimeRange(**r) for r in previous["coverage"] + [coverage]])],
                    "asset_ids": list(dict.fromkeys(previous["asset_ids"] + [asset_id])), "settings": result["settings"],
                    "shard_id": shard["id"], "alignment_status": "draft", "warnings": asset.get("warnings", [])}
                try:
                    revision = self.store.revise(scope, "transcript", content, base, self.project_id, initial=None)
                    break
                except StudioError as error:
                    if error.code != "REVISION_CONFLICT" or attempt == 4:
                        raise
            revisions.append(revision["id"])
            self.store.event(self.job_id, "transcript.partial", {"transcript_revision": revision["id"], "coverage": revision["coverage"]})
            self.data_artifact("transcript", revision)
            self.store.revise(f"analysis-cache:{cache_key}", "analysis_cache", {"transcript_revision": revision["id"],
                "settings": cache_settings, "shard_id": shard["id"]}, cache_head, self.project_id, initial=None)
            if self.body.get("music_policy", "conservative") != "off":
                # 分類子工作以素材為單位重用（冪等鍵 classify:<asset>:ast-v1）：音訊放在與本次工作目錄無關的固定位置，
                # 同一素材再次分析（例如換精修引擎重跑）時內容相同 → 回傳既有工作，不會 IDEMPOTENCY_CONFLICT。
                classify_pcm = self.store.root / "cache" / "classify" / f"{asset_id}.wav"
                classify_pcm.parent.mkdir(parents=True, exist_ok=True)
                if not classify_pcm.is_file():
                    if not pcm.is_file():  # 沿用草稿時沒有解碼過：分類要的音訊現在才轉
                        media._run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-i", asset["path"], "-vn", "-ac", "1", "-ar", "16000", str(pcm)], 300)
                    shutil.copyfile(pcm, classify_pcm)
                classifier = self.store.submit(self.project_id, {"kind": "classify", "source_id": source_id,
                    "asset_id": asset_id, "pcm_path": str(classify_pcm), "timeout_sec": 600}, "cpu",
                    key=f"classify:{asset_id}:ast-v2", parent_id=self.job_id)  # v2：pcm 改放固定快取位置，與修正前的 v1 紀錄分開
                classification_jobs.append(classifier["id"])
        final_revision = revisions[-1]
        profile = self.body.get("profile", "draft")
        refine_usage = None
        if profile in {"balanced", "quality"}:
            previous_body = self.body
            transcript = self.store.get(final_revision, "transcript")
            self.body = {**previous_body, "kind": "refine", "transcript_revision": final_revision,
                "model": previous_body.get("asr_model") or self.default_asr_model(), "engine": previous_body.get("engine", "whisperx"),
                "max_refine_audio_ratio": 1.0 if profile == "quality" else previous_body.get("max_refine_audio_ratio", .15),
                "cue_ids": [c["id"] for c in transcript["cues"]] if profile == "quality" else []}
            try:
                refined = self.refine_align()
                final_revision = refined["transcript_revision"]
                revisions.append(final_revision)
                refine_usage = refined.get("usage")
            finally:
                self.body = previous_body
        outcome = {"transcript_revision": final_revision, "transcript_revisions": revisions, "profile": profile,
                   "classification_job_ids": classification_jobs}
        if refine_usage:
            outcome["refine_usage"] = refine_usage  # 遠端轉錄實際花費（OpenRouter 每句回的 usage 加總）
        if self.body.get("auto_align", True):
            # 逐詞對齊是輸出的一部分：轉錄完成即排對齊子工作；同一逐字稿版本以冪等鍵只排一次
            align_body = {"kind": "align", "transcript_revision": final_revision, "source_id": source_id}
            if self.body.get("auto_export"):
                # 自動輸出要用逐詞對齊後的時間，所以掛在對齊工作後面
                align_body["then_export"] = {**self.body["auto_export"], "asset_id": ids[-1]}
            align = self.store.submit(self.project_id, align_body, "gpu", key=f"align:{final_revision}", parent_id=self.job_id)
            outcome["align_job_id"] = align["id"]
            self.store.event(self.job_id, "alignment.queued", {"job_id": align["id"], "transcript_revision": final_revision})
        return outcome

    def configured_provider(self, provider_id):
        if not provider_id:
            return None
        config = json.loads((self.store.root / "config.json").read_text(encoding="utf-8"))
        provider = next((p for p in config.get("providers", []) if p["id"] == provider_id), None)
        if provider is None:
            raise StudioError("PROVIDER_NOT_FOUND", "選定的文字模型已不存在，請重新選擇", 422)
        return provider

    def provider_work(self):
        from . import providers, provider_secrets
        revision = self.store.get(self.body["transcript_revision"], "transcript")
        provider = self.configured_provider(self.body.get("provider_id"))
        # 金鑰與「測試連線」同一套解析：環境變數優先，其次介面存的 data/secrets.json（2026-09-21 真呼叫發現背景工作沒讀到）
        secret = provider_secrets.resolve_secret(provider, self.store.root) if provider else None
        cues = revision["cues"]
        if self.body.get("cue_ids"):
            ids = set(self.body["cue_ids"])
            if ids - {c["id"] for c in cues}:
                raise StudioError("INVALID_REFERENCE", "找不到引用的句子", 422)
            cues = [c for c in cues if c["id"] in ids]
        self.stage(self.body["kind"])
        if self.body["kind"] == "correct":
            if provider is None:
                raise StudioError("PROVIDER_REQUIRED", "校字需要選擇文字模型", 422)
            try:
                title = ""
                try:
                    title = (self.store.get(revision["source_id"], "source") or {}).get("title") or ""
                except Exception:
                    title = ""  # 找不到來源資訊不影響校字，只是少了主題提示
                result = providers.correct(cues, provider, self.body.get("glossary") or None, base_revision=revision["id"], title=title,
                                           mode=self.body.get("correction_mode", "conservative"), secret=secret,
                                           keep_punctuation=bool(self.body.get("keep_punctuation", False)),  # 字幕格式交給提示詞
                                           **({"reference": self.body["reference_text"]} if self.body.get("reference_text") else {}))
            except providers.ProviderError as error:
                raise model_output_failure(error, provider) from error
        elif self.body["kind"] == "summarize":
            try:
                result = providers.summarize(cues, provider, self.body.get("outputs", ["summary", "highlights"]), transcript_revision=revision["id"],
                                             secret=secret)
            except providers.ProviderError as error:
                raise model_output_failure(error, provider) from error
            for annotation in result["annotations"]:
                self.store.create("annotation", {**annotation, "transcript_revision": revision["id"], "method": result["method"]}, self.project_id)
        else:
            try:
                result = providers.plan_edits(cues, self.body.get("intent", ""), provider,
                    self.body.get("target_duration_us", 180000000), transcript_revision=revision["id"],
                    base_sequence_revision=self.body.get("base_sequence_revision"), map_revision=self.body.get("map_revision"),
                    secret=secret)
            except providers.ProviderError as error:
                raise model_output_failure(error, provider) from error
            proposal = self.store.create("edit_proposal", result, self.project_id)
            result["proposal_id"] = proposal["id"]
        result["artifacts"] = [self.data_artifact(self.body["kind"], result)]
        return result

    def acquire_subtitles(self):
        from .source_subtitles import acquire_captions, CaptionError
        source = self.store.get(self.body["source_id"], "source")
        if source["kind"] != "youtube":
            raise StudioError("SOURCE_CAPTIONS_UNAVAILABLE", "此來源沒有可取得的網路字幕", 422)
        self.stage("source_subtitles.acquire")
        try:
            result = acquire_captions(source["url"], self.work, source_id=source["id"],
                language=None if self.body.get("source_language", "auto") == "auto" else self.body["source_language"],
                kind=self.body.get("source_kind", "prefer_manual"), ranges=self.body.get("ranges", []))
        except CaptionError as error:
            code = str(error)
            raise StudioError(code, "來源字幕無法取得，請調整字幕語言或改用生成字幕", 409) from error
        raw = self.store.publish_file(self.project_id, self.job_id, "source_subtitle_original", result["raw_path"])
        cues = result["cues"]
        coverage = self.body.get("ranges") or [r.model_dump() for r in merge_ranges([
            TimeRange(start_us=c["start_us"], end_us=c["end_us"]) for c in cues])]
        transcript = self.store.create("transcript", {"source_id": source["id"], "audio_track_id": "source_subtitles",
            "cues": cues, "coverage": coverage, "asset_ids": [], "alignment_status": "segment",
            "settings": {"engine": "source_subtitles", "track": result["track"]},
            "warnings": result.get("warnings", []) + ["source_timing_unverified"]}, self.project_id)
        caption = self.store.create("source_subtitle", {"source_id": source["id"], "transcript_revision": transcript["id"],
            "language": result["track"]["language"], "origin": "automatic" if result["track"]["automatic"] else "manual",
            "track": result["track"], "coverage": coverage, "status": "ready" if cues else "empty_selection",
            "original_artifact_id": raw["id"], "warnings": transcript["warnings"]}, self.project_id)
        self.store.event(self.job_id, "source_subtitles.ready", {"source_subtitle_revision": caption["id"], "transcript_revision": transcript["id"]})
        return {"source_subtitle_revision": caption["id"], "transcript_revision": transcript["id"],
                "artifacts": [raw, self.data_artifact("source_subtitle", caption)]}

    def export(self):
        from . import media, subtitles, interchange
        if self.body.get("sequence_revision"):
            sequence = self.store.get(self.body["sequence_revision"], "sequence")
        else:
            # 直接指定範圍（字幕頁整段 SRT）：不動專案的剪輯清單，臨時組一份序列
            sequence = {"id": None, "source_id": self.body["source_id"],
                        "items": [{"id": new_id("seg"), "kind": "clip", "start_us": span["start_us"], "end_us": span["end_us"],
                                   "name": f"範圍 {index + 1}", "selected": True, "tags": {}} for index, span in enumerate(self.body.get("ranges", []))]}
        items = sequence["items"]
        selected = [i for i in items if i["kind"] == "clip" and i.get("selected", True)]
        if not selected:
            raise StudioError("EMPTY_SEQUENCE", "請先選擇要匯出的片段", 422)
        formats = self.body.get("formats", ["srt"])
        source_id = sequence["source_id"]
        assets = [a for a in self.store.list("asset", self.project_id, 200)["items"] if a["source_id"] == source_id]
        grouping = self.body.get("grouping", "merge")
        cut_mode = self.body.get("cut_mode", "accurate")
        mappings, outputs, media_records = [], [], []
        subtitle_quality = []
        effective = deepcopy(selected)
        cursor = 0
        self.stage("export")
        media_format = "mp4" if "mp4" in formats else "m4a" if "audio" in formats else None
        if media_format:
            for index, item in enumerate(selected):
                options = [a for a in assets if (media_format != "mp4" or a["kind"] == "video") and any(
                    m["source_start_us"] <= item["start_us"] and m["source_end_us"] >= item["end_us"] for m in a["source_map"])]
                if not options:
                    raise StudioError("SOURCE_RANGE_UNAVAILABLE", "選區尚未取得可匯出素材", 409, {"item_id": item["id"]})
                asset = options[0]
                mapping = next(m for m in asset["source_map"] if m["source_start_us"] <= item["start_us"] and m["source_end_us"] >= item["end_us"])
                offset = mapping["source_start_us"] - mapping["asset_start_us"]
                record = media.cut_media(asset["path"], self.work / f"export-{index}.{media_format}",
                    item["start_us"] - offset, item["end_us"] - offset, cut_mode, "video" if media_format == "mp4" else "audio")
                actual = record.get("actual_range") or record["requested_range"]
                effective[index]["start_us"] = actual["start_us"] + offset
                effective[index]["end_us"] = effective[index]["start_us"] + record["duration_us"]
                media_records.append(record)
                mappings.append({"item_id": item["id"], "requested_range": {"start_us": item["start_us"], "end_us": item["end_us"]},
                    "actual_range": {"start_us": effective[index]["start_us"], "end_us": effective[index]["end_us"]},
                    "output_start_us": cursor if grouping == "merge" else 0,
                    "output_end_us": cursor + record["duration_us"] if grouping == "merge" else record["duration_us"],
                    "map_revision": mapping["id"], "map_status": mapping["status"], "warnings": record["warnings"]})
                cursor += record["duration_us"]
            if grouping == "merge":
                merged = media.merge_media([r["path"] for r in media_records], self.work / f"merged.{media_format}", cut_mode)
                outputs.append(self.store.publish_file(self.project_id, self.job_id, media_format, merged["path"], {"mappings": mappings}))
            else:
                for item, record in zip(effective, media_records):
                    outputs.append(self.store.publish_file(self.project_id, self.job_id, media_format, record["path"], {"item_id": item["id"]}))
        if "mp4" in formats and "audio" in formats:
            # 直接由完成的影片抽取音軌，避免重跑剪輯與再次編碼造成不同邊界。
            for index, video in enumerate(list(outputs)):
                video_path = self.store.artifact_path(video["id"])
                audio_streams = [s for s in media.ffprobe(video_path)["streams"] if s.get("codec_type") == "audio"]
                if not audio_streams:
                    raise StudioError("AUDIO_STREAM_UNAVAILABLE", "輸出影片沒有音軌可匯出", 422)
                codec = audio_streams[0].get("codec_name")
                audio_codec = "copy" if codec in {"aac", "alac"} else "aac"
                if audio_codec != "copy" and cut_mode == "copy":
                    raise StudioError("INCOMPATIBLE_AUDIO_CONTAINER", "音軌無法直接存成 M4A，請選精準輸出允許轉碼", 422)
                audio_path = self.work / f"video-audio-{index}.m4a"
                media._run(["ffmpeg", "-v", "error", "-nostdin", "-n", "-i", str(video_path),
                            "-map", "0:a:0", "-vn", "-c:a", audio_codec, str(audio_path)], 600)
                provenance = {**video.get("provenance", {}), "derived_from_artifact_id": video["id"],
                    "audio_conversion": audio_codec, "mappings": video.get("provenance", {}).get("mappings") or
                        [m for m in mappings if m["item_id"] == video.get("provenance", {}).get("item_id")]}
                outputs.append(self.store.publish_file(self.project_id, self.job_id, "m4a", audio_path, provenance))
        if not media_format:
            for item in effective:
                duration = item["end_us"] - item["start_us"]
                start = cursor if grouping == "merge" else 0
                mappings.append({"item_id": item["id"],
                    "requested_range": {"start_us": item["start_us"], "end_us": item["end_us"]},
                    "actual_range": None, "output_start_us": start, "output_end_us": start + duration,
                    "map_revision": None, "map_status": "sequence_defined", "warnings": []})
                cursor += duration
        subtitle_timebase = self.body.get("subtitle_timebase", "sequence")
        subtitle_mappings = deepcopy(mappings)
        if subtitle_timebase == "source":
            for mapping, item in zip(subtitle_mappings, effective):
                mapping["output_start_us"] = item["start_us"]
                mapping["output_end_us"] = item["end_us"]
        for mapping in subtitle_mappings:
            mapping["timebase"] = subtitle_timebase
        if not media_format:
            mappings = deepcopy(subtitle_mappings)
        transcript = self.store.get(self.body["transcript_revision"], "transcript") if self.body.get("transcript_revision") else None
        if any(f in formats for f in ("srt", "vtt", "transcript_json")) and transcript is None:
            raise StudioError("TRANSCRIPT_REQUIRED", "字幕匯出需要逐字稿版本", 409)
        if transcript and transcript["source_id"] != source_id:
            raise StudioError("SOURCE_MISMATCH", "逐字稿與剪輯來源不一致", 422)
        require_word = self.body.get("alignment_policy") == "require_word"
        alignment_warnings = []
        if (self.body.get("await_alignment") and transcript and not self.body.get("alignment_revision")
                and any(f in formats for f in ("srt", "vtt"))):
            awaited = self.await_alignment(transcript)
            if awaited:
                self.body["alignment_revision"] = awaited
            elif awaited is None:
                alignment_warnings.append("alignment_unavailable_segment_timing")
        if require_word and not self.body.get("alignment_revision"):
            raise StudioError("ALIGNMENT_REQUIRED", "請先完成文字版本對齊", 409)
        if self.body.get("alignment_revision"):
            # 有提供對齊版本就用逐詞時間；allow_segment 只代表沒對齊到的句子退回句時間，require_word 則由 map_cues 嚴格要求逐詞。
            alignment = self.store.get(self.body["alignment_revision"], "alignment")
            from .alignment_binding import validate_binding
            if transcript is None:
                raise StudioError("TRANSCRIPT_REQUIRED", "逐詞對齊匯出需要逐字稿版本", 409)
            validate_binding(transcript, alignment,
                [self.store.get(a, "asset") for a in transcript.get("asset_ids", [])])
            transcript = {**transcript, "cues": alignment["cues"]}
        for format_name in formats:
            if format_name in {"srt", "vtt"}:
                # 與網頁預覽（POST /subtitles/preview）同一段計算：預覽看到的就是檔案內容
                groups = subtitles.build(transcript["cues"], effective, subtitle_mappings, timebase=subtitle_timebase, grouping=grouping,
                    require_word=require_word, sentences_per_cue=self.body.get("sentences_per_cue", 1),
                    keep_punctuation=self.body.get("keep_punctuation", False), show_language=bool(self.body.get("show_language")))
                for group_index, laid in enumerate(groups):
                    text = subtitles.to_srt(laid) if format_name == "srt" else subtitles.to_vtt(laid)
                    # 字幕品質檢查（M4-C2）：不改字幕，寫進成果紀錄與匯出清單，介面據此提示
                    quality = subtitles.quality_report(laid, transcript["cues"])
                    if format_name == "srt":
                        subtitle_quality.append(quality)
                    outputs.append(self.store.publish(self.project_id, self.job_id, format_name, text.encode(), format_name,
                        {"sequence_revision": sequence["id"], "transcript_revision": transcript["id"],
                         "item_id": effective[group_index]["id"] if grouping == "separate" else None,
                         "timebase": subtitle_timebase,
                         "mappings": [subtitle_mappings[group_index]] if grouping == "separate" else subtitle_mappings,
                         "quality": quality,
                         "warnings": sorted({w for c in laid for w in c.get("warnings", [])})}))
            elif format_name == "transcript_json":
                outputs.append(self.data_artifact("transcript", transcript))
            elif format_name in {"llc", "csv"}:
                if not assets:
                    raise StudioError("SOURCE_RANGE_UNAVAILABLE", "請先取得交換格式參照的素材", 409)
                asset = next((a for a in assets if all(a["source_map"][0]["source_start_us"] <= i["start_us"] and
                    a["source_map"][0]["source_end_us"] >= (i.get("end_us") or i["start_us"]) for i in items)), None)
                if asset is None:
                    raise StudioError("SOURCE_RANGE_UNAVAILABLE", "交換格式需要涵蓋全部片段的單一素材", 409)
                offset = asset["source_map"][0]["source_start_us"]
                text = interchange.to_llc(items, Path(asset["path"]).name, offset) if format_name == "llc" else interchange.to_csv(items, offset)
                outputs.append(self.store.publish(self.project_id, self.job_id, format_name, text.encode(), format_name))
            elif format_name == "studio_json":
                outputs.append(self.data_artifact("project", {"schema_version": 2, "sequence": sequence, "transcript": transcript}))
            elif format_name in {"summary_md", "chapters_txt"}:
                if transcript is None:
                    raise StudioError("TRANSCRIPT_REQUIRED", "摘要匯出需要指定文字版本", 409)
                annotations = [a for a in self.store.list("annotation", self.project_id, 200)["items"]
                    if a.get("transcript_revision") == transcript["id"]]
                if format_name == "chapters_txt":
                    annotations = [a for a in annotations if a.get("kind") in {"chapter", "chapters"}]
                if not annotations:
                    raise StudioError("SUMMARY_REQUIRED", "請先為此文字版本產生摘要或章節", 409)
                lines = []
                for annotation in annotations:
                    spans = annotation.get("source_spans", [])
                    start = spans[0]["start_us"] if spans else 0
                    seconds = start // 1000000
                    timecode = f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"
                    title = annotation.get("title", "重點")
                    lines.append(f"{timecode} {title}" if format_name == "chapters_txt" else
                        f"## {title}\n\n{annotation.get('body', '')}\n\n來源：{timecode}；引用：{', '.join(annotation.get('cue_ids', []))}\n")
                extension = "md" if format_name == "summary_md" else "txt"
                outputs.append(self.store.publish(self.project_id, self.job_id, format_name,
                    "\n".join(lines).encode(), extension, {"transcript_revision": transcript["id"]}))
            elif format_name not in {"mp4", "audio"}:
                raise StudioError("UNSUPPORTED_FORMAT", "此匯出格式尚未完成", 422)
        manifest = {"sequence_revision": sequence["id"], "transcript_revision": transcript["id"] if transcript else None,
            "alignment_revision": self.body.get("alignment_revision"), "grouping": grouping, "cut_mode": cut_mode,
            "mappings": mappings, "subtitle_mappings": subtitle_mappings, "subtitle_timebase": subtitle_timebase,
            "subtitle_quality": subtitle_quality,
            "outputs": outputs, "warnings": sorted({w for m in mappings for w in m.get("warnings", [])} | set(alignment_warnings))}
        manifest_artifact = self.data_artifact("export_manifest", manifest)
        records = []
        if self.body.get("include_records") and transcript:
            # 逐字稿 JSON 與修改紀錄 JSON：跟 SRT 放一起，之後任何工具都能讀
            from .history import build_history, transcript_record
            records = [self.data_artifact("transcript_record", transcript_record(transcript)),
                       self.data_artifact("transcript_history", build_history(self.store, transcript, self.project_id))]
        saved_files = []
        # 從網頁下載（專案資料目錄）時的檔名：與輸出到資料夾同一個規則，不用內部的 artifact_xxx（2026-09-21 真瀏覽器）
        stem = self.source_stem(source_id)
        track = (transcript or {}).get("audio_track_id") or "default"
        if track != "default":
            stem += "_來源字幕" if track == "source_subtitles" else f"_{title_stem(track)}"
        delivered = outputs + records + [manifest_artifact]
        paths = [self.store.artifact_path(a["id"]) for a in delivered]
        extensions = [path.suffix for path in paths]
        media_job = bool(media_format)
        download_names = {a["id"]: name for a, name in zip(delivered, delivery_names(stem, delivered, extensions, "", media_job))}
        if self.body.get("output_root_id"):
            # 輸出檔名跟著素材檔名、一律放進 subtitle_studio：<素材名>.srt／.transcript.json／.history.json／.export_manifest.json；
            # 字幕與紀錄覆寫成最新一份；影音不覆蓋舊檔，整批（影片＋附帶字幕＋清單）共用同一個不重複尾碼。
            # 同一個素材可能有兩種文字來源（WhisperX 生成／YouTube 來源字幕，比對流程會各輸出一份）：來源字幕另名，不互相覆蓋（stem 已處理）
            from .destinations import delivery_folder, free_variant
            config = json.loads((self.store.root / "config.json").read_text(encoding="utf-8"))
            variant = free_variant(delivery_folder(config, self.body["output_root_id"]),
                lambda v: delivery_names(stem, delivered, extensions, v, True)) if media_job else ""
            for artifact, original, name in zip(delivered, paths, delivery_names(stem, delivered, extensions, variant, media_job)):
                saved_files.append({"artifact_id": artifact["id"], **self.deliver(original, name, overwrite=not media_job)})
        return {"artifacts": outputs + records, "manifest": manifest, "manifest_artifact": manifest_artifact, "saved_files": saved_files,
                "download_names": download_names}

    def run(self):
        kind = self.body["kind"]
        if kind in {"probe", "acquire", "acquire_subtitles", "analyze", "export"}:
            return getattr(self, kind)()
        if kind in {"correct", "summarize", "plan_edits"}:
            return self.provider_work()
        if kind == "workflow":
            return self.workflow()
        if kind == "import":
            return self.import_sequence()
        if kind in {"align", "refine"}:
            result = self.refine_align()
            if kind == "align" and self.body.get("then_export"):
                result = {**result, "export_job_id": self.queue_subtitle_export(result.get("alignment_revision"))}
            return result
        if kind == "classify":
            from .classification import classify_audio
            import torch
            torch.set_num_threads(max(1, min(8, int(os.environ.get("STUDIO_CPU_THREADS", "2")))))
            asset = self.store.get(self.body["asset_id"], "asset")
            self.stage("classify")
            result = classify_audio(self.body["pcm_path"], device="cpu")
            offset = asset["source_map"][0]["source_start_us"]
            for collection in ("raw_spans", "spans"):
                for span in result[collection]:
                    span["start_us"] += offset
                    span["end_us"] += offset
            result.update(source_id=asset["source_id"], asset_id=asset["id"], map_revision=asset["map_revision"])
            result["settings"]["timebase"] = "source"
            classification = self.store.create("classification", result, self.project_id)
            return {"classification_id": classification["id"], "artifacts": [self.data_artifact("classification", classification)]}
        raise StudioError("UNSUPPORTED_OPERATION", "此工作階段尚未完成", 422)

    ALIGNMENT_WAIT_SEC = 900

    def await_alignment(self, transcript):
        """匯出前等這一版逐字稿的逐詞對齊。回傳 alignment_revision；沒得對齊回 False（不算警告）；失敗／逾時回 None（退回句子時間並警告）。

        使用者編輯或校字後馬上按匯出時，網頁排的對齊工作多半還在跑；CLI／API 改完字則可能沒人排過——這裡用同一個冪等鍵補排。"""
        revision = transcript["id"]
        if transcript.get("audio_track_id") == "source_subtitles" or not transcript.get("asset_ids"):
            return False  # 來源字幕／沒有綁定音訊的逐字稿：沒有逐詞對齊可言
        jobs = alignment_jobs(self.store, self.project_id, revision)
        done = next((j for j in jobs if j["status"] == "succeeded" and (j.get("result") or {}).get("alignment_revision")), None)
        if done:
            return done["result"]["alignment_revision"]
        active = next((j for j in jobs if j["status"] in {"queued", "running", "waiting"}), None)
        if active is None:
            active = self.store.submit(self.project_id, {"kind": "align", "transcript_revision": revision, "source_id": transcript["source_id"]},
                "gpu", key=f"align:{revision}")
            if active["status"] in {"failed", "cancelled", "interrupted"}:  # 同鍵的舊工作失敗過：換鍵重排一次
                active = self.store.submit(self.project_id, {"kind": "align", "transcript_revision": revision, "source_id": transcript["source_id"]},
                    "gpu", key=f"align:{revision}:retry:{self.job_id}")
        self.stage("export.await_alignment", align_job_id=active["id"])
        deadline = time.monotonic() + self.ALIGNMENT_WAIT_SEC
        while time.monotonic() < deadline:
            job = self.store.job(active["id"])
            if job["status"] == "succeeded":
                return (job.get("result") or {}).get("alignment_revision") or None
            if job["status"] in {"failed", "cancelled", "interrupted"}:
                self.store.event(self.job_id, "export.alignment_unavailable", {"align_job_id": job["id"], "status": job["status"]})
                return None
            time.sleep(.2)
        self.store.event(self.job_id, "export.alignment_unavailable", {"align_job_id": active["id"], "status": "timeout"})
        return None

    def wait_children(self, ids):
        results = []
        for job_id in ids:
            while True:
                job = self.store.job(job_id)
                if job["status"] == "succeeded":
                    results.append(job["result"])
                    break
                if job["status"] in {"failed", "cancelled", "interrupted"}:
                    raise StudioError("DEPENDENCY_FAILED", "前置工作未完成", 409, {"job_id": job_id, "error": job.get("error")})
                time.sleep(.2)
        return results

    def child(self, body, resource):
        if body["kind"] in {"summarize", "correct", "plan_edits"}:
            provider = self.configured_provider(body.get("provider_id"))
            resource = "gpu" if provider and provider.get("gpu_ownership", "external") != "cpu" else "provider"
        digest = hashlib.sha256(canonical(body).encode()).hexdigest()
        return self.store.submit(self.project_id, body, resource, key=f"child:{self.job_id}:{digest}", parent_id=self.job_id)["id"]

    def workflow(self):
        plan = self.store.get(self.body["plan_id"], "plan")
        source_id = plan["source_id"]
        analysis = plan["analysis"]
        acquisition = plan["acquisition"]
        subtitles = plan["subtitles"]
        deliverables = plan["deliverables"]
        probe_id = self.child({"kind": "probe", "source_id": source_id}, "download")
        metadata = self.wait_children([probe_id])[0]
        spans = plan["ranges"] or [{"start_us": 0, "end_us": metadata["duration_us"]}]
        if plan.get("sequence_revision"):
            sequence = self.store.get(plan["sequence_revision"], "sequence")
        else:
            items = [{"id": new_id("seg"), "kind": "clip", **span, "name": f"片段 {i + 1}", "selected": True, "tags": {}}
                     for i, span in enumerate(spans)]
            scope = f"sequence:{self.project_id}"
            sequence = self.store.revise(scope, "sequence", {"source_id": source_id, "items": items}, self.store.head(scope), self.project_id)
        base = {"kind": "acquire", "source_id": source_id, "ranges": spans,
                "output_root_id": acquisition.get("output_root_id"),
                "boundary_policy": acquisition.get("boundary_policy", "accurate"),
                # 取得設定一路帶到子工作：下載才會照快照設定，快取簽章也才會與面板「加入下載」一致
                "quality": acquisition.get("quality", "source"), "format_policy": acquisition.get("format_policy") or {}}
        audio_request = {**base, "asset_kind": "audio", "model": analysis.get("model", "turbo"),
                         "device": analysis.get("device", "auto"),
                         "language_policy": analysis.get("language_policy", "auto_ja_zh_en"),
                         "music_policy": analysis.get("music_policy", "conservative")}
        if analysis["mode"] != "none":
            audio_request["follow_up"] = follow_up_from_analysis(analysis)
        source_caption_job = None
        if subtitles["policy"] in {"source", "compare"}:
            source_caption_job = self.child({"kind": "acquire_subtitles", "source_id": source_id, "ranges": spans,
                "source_language": subtitles.get("source_language", "auto"), "source_kind": subtitles.get("source_kind", "prefer_manual")}, "download")
        needs_audio = analysis["mode"] != "none" or "audio" in deliverables["formats"] or (
            acquisition["video"] == "none" and subtitles["policy"] not in {"source", "compare"})
        audio_job = self.child(audio_request, "download") if needs_audio else None
        video_job = None
        if acquisition["video"] != "none":
            video_job = self.child({**base, "asset_kind": "video", "ranges": [] if acquisition["video"] == "full" else spans,
                "quality": acquisition.get("quality", "source"), "format_policy": acquisition.get("format_policy", {})}, "download")
        self.stage("waiting_audio")
        audio = self.wait_children([audio_job])[0] if audio_job else {"asset_ids": [], "child_job_ids": []}
        transcript = None
        alignment = None
        source_caption = None
        comparison = None
        analysis_artifacts = []
        video = {"asset_ids": [], "saved_files": []}
        if analysis["mode"] != "none":
            self.stage("waiting_analysis")
            analysis_results = self.wait_children(audio.get("child_job_ids", []))
            analysis_artifacts.extend(a for r in analysis_results for a in r.get("artifacts", []))
            classifier_jobs = [job for result in analysis_results for job in result.get("classification_job_ids", [])]
            self.wait_children(classifier_jobs)
            transcript = self.store.head(f"transcript:{source_id}:default", None)
            if subtitles["alignment"] == "require_word" and subtitles["policy"] != "none":
                align_job = self.child({"kind": "align", "transcript_revision": transcript, "source_id": source_id,
                                       "device": analysis.get("device", "auto")}, "gpu")
                aligned = self.wait_children([align_job])[0]
                alignment = aligned["alignment_revision"]
                analysis_artifacts.extend(aligned.get("artifacts", []))
            if analysis.get("summary") and subtitles["policy"] != "source":
                summary_job = self.child({"kind": "summarize", "transcript_revision": transcript,
                    "provider_id": analysis.get("provider_id"), "outputs": ["summary", "highlights", "chapters"]},
                    "gpu" if analysis.get("provider_id") else "provider")
                analysis_artifacts.extend(self.wait_children([summary_job])[0].get("artifacts", []))
        if video_job:
            self.stage("waiting_video")
            video = self.wait_children([video_job])[0]
        if source_caption_job:
            source_caption = self.wait_children([source_caption_job])[0]
            if subtitles["policy"] == "source":
                transcript = source_caption["transcript_revision"]
                alignment = None
            else:
                from .source_subtitles import compare_captions
                generated = self.store.get(transcript, "transcript")
                source_transcript = self.store.get(source_caption["transcript_revision"], "transcript")
                comparison = self.store.create("subtitle_comparison", {"source_id": source_id,
                    **compare_captions(generated["cues"], source_transcript["cues"], generated_revision=transcript,
                                       source_revision=source_transcript["id"])}, self.project_id)
        if analysis.get("summary") and subtitles["policy"] == "source":
            # 來源字幕成為最終文字版本後，才建立同版本的摘要引用。
            summary_job = self.child({"kind": "summarize", "transcript_revision": transcript,
                "provider_id": analysis.get("provider_id"), "outputs": ["summary", "highlights", "chapters"]},
                "gpu" if analysis.get("provider_id") else "provider")
            analysis_artifacts.extend(self.wait_children([summary_job])[0].get("artifacts", []))
        if not deliverables["formats"]:
            artifacts = analysis_artifacts + (source_caption.get("artifacts", []) if source_caption else [])
            if comparison:
                artifacts.append(self.data_artifact("subtitle_comparison", comparison))
            return {"asset_ids": list(dict.fromkeys(audio["asset_ids"] + video["asset_ids"])),
                "saved_files": audio.get("saved_files", []) + video.get("saved_files", []), "artifacts": artifacts,
                "alignment_revision": alignment, "transcript_revision": transcript, "sequence_revision": sequence["id"],
                "source_subtitle_revision": source_caption["source_subtitle_revision"] if source_caption else None,
                "comparison_revision": comparison["id"] if comparison else None}
        export_job = self.child({"kind": "export", "sequence_revision": sequence["id"], "transcript_revision": transcript,
            "output_root_id": deliverables.get("output_root_id"),
            "alignment_revision": alignment, "formats": deliverables["formats"], "grouping": deliverables["grouping"],
            "cut_mode": deliverables["cut_mode"], "subtitle_timebase": "clip" if deliverables["grouping"] == "separate" else "sequence",
            "sentences_per_cue": subtitles["sentences_per_cue"], "keep_punctuation": subtitles["keep_punctuation"],
            "alignment_policy": subtitles["alignment"] if subtitles["policy"] != "none" else "allow_segment"}, "cpu")
        result = self.wait_children([export_job])[0]
        export_manifests = [result["manifest_artifact"]]
        extra_artifacts = []
        if comparison:
            source_export = self.child({"kind": "export", "sequence_revision": sequence["id"],
                "transcript_revision": source_caption["transcript_revision"], "formats": ["srt"], "grouping": deliverables["grouping"],
                "subtitle_timebase": "clip" if deliverables["grouping"] == "separate" else "sequence",
                "keep_punctuation": subtitles["keep_punctuation"], "sentences_per_cue": subtitles["sentences_per_cue"],
                "alignment_policy": "allow_segment", "output_root_id": deliverables.get("output_root_id")}, "cpu")
            source_output = self.wait_children([source_export])[0]
            export_manifests.append(source_output["manifest_artifact"])
            source_artifacts = [{**a, "subtitle_origin": "source"} for a in source_output["artifacts"]]
            extra_artifacts.append(self.data_artifact("subtitle_comparison", comparison))
            result["artifacts"] = result["artifacts"] + source_artifacts + extra_artifacts
            result["saved_files"] = result.get("saved_files", []) + source_output.get("saved_files", [])
        manifest = {**result["manifest"], "kind": "workflow", "plan_revision": plan["id"],
            "source_subtitle_revision": source_caption["source_subtitle_revision"] if source_caption else None,
            "comparison_revision": comparison["id"] if comparison else None,
            "outputs": result["artifacts"], "export_manifests": export_manifests}
        result["manifest"] = manifest
        result["manifest_artifact"] = self.data_artifact("workflow_manifest", manifest)
        root_id = deliverables.get("output_root_id")
        if root_id:
            # 流程層的清單與比對結果：同樣跟著素材檔名、覆寫成最新一份
            stem = self.source_stem(sequence["source_id"])
            for artifact in extra_artifacts + [result["manifest_artifact"]]:
                saved = self.deliver(self.store.artifact_path(artifact["id"]), f"{stem}.{artifact['kind']}.json", root_id, overwrite=True)
                result.setdefault("saved_files", []).append({"artifact_id": artifact["id"], **saved})
        return {**result, "sequence_revision": sequence["id"], "transcript_revision": transcript, "export_job_id": export_job,
            "source_subtitle_revision": source_caption["source_subtitle_revision"] if source_caption else None,
            "comparison_revision": comparison["id"] if comparison else None}

    def import_sequence(self):
        from . import interchange
        from .domain import SequenceItem
        asset = self.store.get(self.body["asset_id"], "asset")
        offset = asset["source_map"][0]["source_start_us"]
        format_name = self.body["format"]
        if format_name == "llc":
            items = interchange.from_llc(self.body["content"], Path(asset["path"]).name, offset)
        elif format_name == "csv":
            items = interchange.from_csv(self.body["content"], offset)
        else:
            data = json.loads(self.body["content"])
            if data.get("schema_version") != 2:
                raise StudioError("UNSUPPORTED_VERSION", "專案版本不受支援", 422)
            items = data["sequence"]["items"]
        items = [SequenceItem.model_validate(i).model_dump() for i in items]
        if any(i["start_us"] < offset or (i.get("end_us") or i["start_us"]) > offset + asset["duration_us"] for i in items):
            raise StudioError("SOURCE_RANGE_UNAVAILABLE", "匯入片段超出參照素材", 422)
        result = self.store.revise(f"sequence:{self.project_id}", "sequence", {"source_id": asset["source_id"], "items": items},
            self.body["base_sequence_revision"], self.project_id)
        return {"sequence_revision": result["id"], "sequence": result}

    def refine_align(self):
        from . import asr
        original = self.store.get(self.body["transcript_revision"], "transcript")
        if self.body["kind"] != "align":
            # 先確認要精修的是最新版本，再開始跑模型（2026-09-21 真跑：同一份舊草稿第二次精修會整段轉完——遠端模型就是花了錢——
            # 才在存檔時撞 REVISION_CONFLICT）；對齊不改逐字稿，不需要這個檢查
            current = self.store.head(f"transcript:{original['source_id']}:{original['audio_track_id']}", None)
            if current is not None and current != original["id"]:
                raise StudioError("REVISION_CONFLICT", "這份逐字稿已經有更新的版本（例如另一次精修先完成了），沒有開始精修、原稿已保留；請用最新版本重新精修",
                                  409, {"current_revision": current})
        cues = deepcopy(original["cues"])
        assets = [self.store.get(a, "asset") for a in original["asset_ids"]]
        known_assets = {a["id"] for a in assets}
        from .alignment_binding import validate_audio
        validate_audio(assets)
        covered = set()
        warnings = []
        routing = []
        usage = {}
        for asset in assets:
            start = asset["source_map"][0]["source_start_us"]
            end = asset["source_map"][0]["source_end_us"]
            # 有 asset_id 的句子只歸產生它的素材；沒有的（舊資料）才用時間涵蓋判斷
            selected = [c for c in cues if c["id"] not in covered and (c.get("asset_id") == asset["id"] if c.get("asset_id") and c.get("asset_id") in known_assets
                        else start <= c["start_us"] and c["end_us"] <= end)]
            if self.body["kind"] == "align" and self.body.get("cue_ids"):
                selected = [c for c in selected if c["id"] in self.body["cue_ids"]]
            # 精修：指定的 cue_ids 只保留本素材涵蓋的句子（逐字稿可能掛多個素材；不在本素材的句子交給後面的素材）
            asset_cue_ids = None
            if self.body["kind"] != "align" and self.body.get("cue_ids"):
                present = {c["id"] for c in selected}
                asset_cue_ids = [i for i in self.body["cue_ids"] if i in present]
                if not asset_cue_ids:
                    continue
            if not selected:
                continue
            self.stage(self.body["kind"], asset_id=asset["id"])
            if self.body["kind"] == "align":
                result = asr.align(selected, asset["path"], start, device=self.body.get("device", "auto"))
            else:
                result = asr.refine(selected, asset["path"], self.body.get("engine", "whisperx"), source_offset_us=start,
                    cue_ids=asset_cue_ids, ranges=self.body.get("ranges") or None,
                    max_refine_audio_ratio=self.body.get("max_refine_audio_ratio", .15), model=self.body.get("model", "large-v3"),
                    device=self.body.get("device", "auto"), context_us=self.body.get("context_us", 300000),
                    timeout_sec=self.body.get("timeout_sec", 1800), fallback_engine=self.body.get("fallback_engine"),
                    language_policy=self.body.get("language_policy", "auto_ja_zh_en"), remote=self.remote_asr_settings(),
                    hints=self.body.get("asr_hints"))
                routing.append(result.get("routing"))
                for key, value in (result.get("usage") or {}).items():  # 遠端轉錄的秒數／費用，多個素材加總
                    usage[key] = round(usage.get(key, 0) + value, 10)
                choices = result.get("routing", {}).get("selected", [])
                if choices and not any(choice.get("status") in {"completed", "proposed"} for choice in choices):
                    self.data_artifact("refinement_diagnostics", result.get("routing", {}))
                    cause = next((choice.get("error") for choice in choices if choice.get("error")), None)
                    reason = failure_reason(cause) if cause else None  # 遠端錯誤碼 → 中文說明（缺權限會說是哪個）
                    raise StudioError("REFINEMENT_FAILED", "選段精修未成功，原稿已保留" +
                        ("：" + reason if reason else ""), 409, details={"error": cause})
                if not choices and (self.body.get("cue_ids") or self.body.get("ranges")):
                    raise StudioError("REFINEMENT_BUDGET_EXHAUSTED", "目前預算不足以處理選定完整句與補邊，請增加精修預算", 422)
            selected_ids = {c["id"] for c in selected}
            if self.body["kind"] != "align":
                # 精修產生的新句子記下產生它的素材（2026-09-21 真跑：307 句只有 11 句有），之後再精修／對齊才不會靠時間猜錯檔
                for cue in result["cues"]:
                    cue.setdefault("asset_id", asset["id"])
            cues = [c for c in cues if c["id"] not in selected_ids] + result["cues"]
            covered.update(selected_ids)
            warnings.extend(result.get("warnings", []))
        cues.sort(key=lambda c: c["start_us"])
        if self.body["kind"] == "align":
            alignment = self.store.create("alignment", {"transcript_revision": original["id"], "source_id": original["source_id"],
                "audio_track_id": original["audio_track_id"], "cues": cues, "asset_ids": original["asset_ids"],
                "map_revisions": [a["map_revision"] for a in assets],
                "content_hashes": [a["content_hash"] for a in assets], "warnings": warnings}, self.project_id)
            return {"alignment_revision": alignment["id"], "artifacts": [self.data_artifact("alignment", alignment)], "warnings": warnings}
        revision = self.store.revise(f"transcript:{original['source_id']}:{original['audio_track_id']}", "transcript",
            {**original, "cues": cues, "routing": routing}, original["id"], self.project_id, initial=None)
        return {"transcript_revision": revision["id"], "routing": routing, "warnings": warnings,
                "artifacts": [self.data_artifact("transcript", revision)], **({"usage": usage} if usage else {})}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    store = Store(args.root)
    work = store.root / "jobs" / args.job
    gate = work / "start.ready"
    deadline = time.monotonic() + 10
    while not gate.exists():
        if time.monotonic() > deadline:
            raise RuntimeError("工作啟動閘門逾時")
        time.sleep(.02)
    try:
        result = Worker(store, store.job(args.job)).run()
        outcome = {"ok": True, "result": result}
    except Exception as error:
        outcome = {"ok": False, "error": {"code": getattr(error, "code", "WORKER_FAILED"), "message": str(error),
            "details": getattr(error, "details", None)}}
    temporary = work / "result.staging"
    temporary.write_text(canonical(outcome), encoding="utf-8")
    from .fsutil import replace_with_retry
    replace_with_retry(temporary, work / "result.json")
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
