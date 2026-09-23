"""模型清單（models.manifest.json）、從本機既有快取匯入專案 models/、GGUF 清單。

原則：只複製、不刪來源；比對大小才視為完成；匯入紀錄寫 models/IMPORTED.json（含 revision）。下載由 M0-P3 另加。
ct2 類（2026-09-19，Breeze-ASR）：從官方 repo 只下載 safetensors 與設定／詞彙檔（allow list，不下載 pickle 類訓練檔），
在本機轉成 CTranslate2 float16 放 models/ct2/<key>/，另存 STUDIO_CT2.json 來源紀錄；轉完刪除下載的原始權重，失敗時保留供重試。
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil

from app import config as app_config

IMPORTED = "IMPORTED.json"
# ct2 轉換只需要這些檔：權重一律 safetensors；不含 training_args.bin／optimizer.bin／*.pkl／*.pt（pickle 可執行程式碼，也用不到）
CT2_SOURCE_PATTERNS = ["config.json", "generation_config.json", "preprocessor_config.json", "tokenizer.json", "tokenizer_config.json",
                       "vocab.json", "merges.txt", "added_tokens.json", "special_tokens_map.json", "normalizer.json",
                       "model.safetensors", "model-*-of-*.safetensors", "model.safetensors.index.json"]


def manifest_path():
    return app_config.PROJECT_ROOT / "models.manifest.json"


def load_manifest(path=None):
    data = json.loads(Path(path or manifest_path()).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("UNSUPPORTED_MODEL_MANIFEST")
    return data


def default_source_hf():
    """本機既有 Hugging Face 快取（匯入來源）；STUDIO_IMPORT_SOURCE_HF 可覆寫。"""
    override = os.environ.get("STUDIO_IMPORT_SOURCE_HF")
    return Path(override) if override else Path.home() / ".cache" / "huggingface" / "hub"


def default_source_torch():
    override = os.environ.get("STUDIO_IMPORT_SOURCE_TORCH")
    return Path(override) if override else Path.home() / ".cache" / "torch"


def repo_folder(repo):
    return "models--" + repo.replace("/", "--")


def tree_size(path):
    total = 0
    for folder, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except OSError:
                pass
    return total


def hf_revision(folder):
    ref = Path(folder) / "refs" / "main"
    return ref.read_text(encoding="utf-8").strip() if ref.is_file() else None


def plan_import(manifest, models_dir, *, source_hf, source_torch, include_optional=False):
    """列出每個模型要做的動作：present／copy／missing_source／skipped_optional；不動任何檔案。"""
    models_dir = Path(models_dir)
    source_hf, source_torch = Path(source_hf), Path(source_torch)
    items, total = [], 0
    for entry in manifest.get("hf", []):
        repo, required = entry["repo"], entry.get("required", True)
        item = dict(id="hf:" + repo, kind="hf", repo=repo, required=required, bytes=0)
        target, source = models_dir / "hf" / repo_folder(repo), source_hf / repo_folder(repo)
        target_size = tree_size(target) if target.is_dir() else 0
        source_size = tree_size(source) if source.is_dir() else 0
        if not required and not include_optional:
            item["action"] = "skipped_optional"
        elif target_size > 0 and target_size >= source_size:
            item.update(action="present", bytes=target_size, target=str(target))
        elif source.is_dir():
            item.update(action="copy", source=str(source), target=str(target), bytes=source_size, revision=hf_revision(source))
            total += source_size
        else:
            item["action"] = "missing_source"
        items.append(item)
    for entry in manifest.get("torch", []):
        rel, required = entry["file"], entry.get("required", True)
        item = dict(id="torch:" + rel, kind="torch", file=rel, required=required, bytes=0)
        target, source = models_dir / "torch" / rel, source_torch / rel
        target_size = target.stat().st_size if target.is_file() else 0
        source_size = source.stat().st_size if source.is_file() else 0
        if not required and not include_optional:
            item["action"] = "skipped_optional"
        elif target_size > 0 and target_size >= source_size:
            item.update(action="present", bytes=target_size, target=str(target))
        elif source.is_file():
            item.update(action="copy", source=str(source), target=str(target), bytes=source_size)
            total += source_size
        else:
            item["action"] = "missing_source"
        items.append(item)
    return dict(models_dir=str(models_dir), items=items, total_bytes=total)


def _load_imported(models_dir):
    path = Path(models_dir) / IMPORTED
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"hf": {}, "torch": {}}


def run_import(plan, progress=None):
    """依計畫複製；每項完成後比對大小，並把 revision 與大小記到 models/IMPORTED.json。"""
    models_dir = Path(plan["models_dir"])
    imported = _load_imported(models_dir)
    results = []
    for item in plan["items"]:
        if item["action"] != "copy":
            results.append(dict(item, status=item["action"]))
            continue
        source, target = Path(item["source"]), Path(item["target"])
        if progress:
            progress(item)
        if item["kind"] == "hf":
            shutil.copytree(source, target, dirs_exist_ok=True, symlinks=False)
            copied = tree_size(target)
            imported.setdefault("hf", {})[item["repo"]] = dict(revision=item.get("revision"), bytes=copied, source=str(source),
                imported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied = target.stat().st_size
            imported.setdefault("torch", {})[item["file"]] = dict(bytes=copied, source=str(source),
                imported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        results.append(dict(item, status="copied" if copied >= item["bytes"] else "size_mismatch", copied_bytes=copied))
    models_dir.mkdir(parents=True, exist_ok=True)
    (models_dir / IMPORTED).write_text(json.dumps(imported, ensure_ascii=False, indent=1), encoding="utf-8")
    return dict(models_dir=str(models_dir), items=results)


TORCH_URLS = {
    "hub/checkpoints/wav2vec2_fairseq_base_ls960_asr_ls960.pth":
        "https://download.pytorch.org/torchaudio/models/wav2vec2_fairseq_base_ls960_asr_ls960.pth",
}


def _hf_present(folder):
    snapshots = Path(folder) / "snapshots"
    return snapshots.is_dir() and any(child.is_dir() and any(child.iterdir()) for child in snapshots.iterdir())


def _hf_complete(folder, entry):
    snapshots = Path(folder) / 'snapshots'
    required = entry.get('required_files', [])
    if not snapshots.is_dir():
        return False
    candidates = [snapshots / entry['revision']] if entry.get('revision') else list(snapshots.iterdir())
    return any(child.is_dir() and any(child.iterdir()) and
               all((child / name).is_file() and (child / name).stat().st_size > 0 for name in required) for child in candidates)


def check_models(manifest, models_dir, include_optional=False):
    """每個模型的狀態：present／missing／size_mismatch／revision_mismatch／skipped_optional；不連網。
    大小與 revision 依 manifest 指定或 IMPORTED.json 的匯入紀錄比對。"""
    models_dir = Path(models_dir)
    imported = _load_imported(models_dir)
    items = []
    for entry in manifest.get("hf", []):
        repo, required = entry["repo"], entry.get("required", True)
        item = dict(id="hf:" + repo, kind="hf", repo=repo, required=required, required_files=entry.get('required_files', []))
        if not required and not include_optional:
            item["status"] = "skipped_optional"
        else:
            folder = models_dir / "hf" / repo_folder(repo)
            if not _hf_present(folder):
                item["status"] = "missing"
            else:
                record = imported.get("hf", {}).get(repo, {})
                actual = hf_revision(folder)
                expected = entry.get("revision") or record.get("revision")
                size = tree_size(folder)
                if entry.get('required_files') and not _hf_complete(folder, entry):
                    item['status'] = 'missing'
                elif record.get("bytes") and size < record["bytes"]:
                    item["status"] = "size_mismatch"
                elif expected and actual and actual != expected:
                    item["status"] = "revision_mismatch"
                else:
                    item["status"] = "present"
                item.update(bytes=size, revision=actual)
        items.append(item)
    for entry in manifest.get("torch", []):
        rel, required = entry["file"], entry.get("required", True)
        item = dict(id="torch:" + rel, kind="torch", file=rel, required=required)
        if not required and not include_optional:
            item["status"] = "skipped_optional"
        else:
            target = models_dir / "torch" / rel
            if not target.is_file() or target.stat().st_size == 0:
                item["status"] = "missing"
            else:
                size = target.stat().st_size
                record = imported.get("torch", {}).get(rel, {})
                expected = entry.get("expected_bytes") or record.get("bytes")
                item["status"] = "size_mismatch" if expected and size < expected else "present"
                item["bytes"] = size
        items.append(item)
    for entry in manifest.get("gguf", []):
        name, required = entry["dir"], entry.get("required", False)
        item = dict(id="gguf:" + name, kind="gguf", dir=name, required=required)
        if not required and not include_optional:
            item["status"] = "skipped_optional"
        else:
            folder = models_dir / "gguf" / name
            item["status"] = "present" if folder.is_dir() and any(folder.rglob("*.gguf")) else "missing"
        items.append(item)
    for entry in manifest.get("ct2", []):
        key, required = entry["key"], entry.get("required", False)
        item = dict(id="ct2:" + key, kind="ct2", key=key, repo=entry["repo"], required=required, label=entry.get("label"),
                    download_bytes=entry.get("download_bytes", 0), license=entry.get("license"))
        if not required and not include_optional:
            item["status"] = "skipped_optional"
        else:
            from .asr_models import REQUIRED_FILES
            folder = models_dir / "ct2" / key
            complete = all((folder / name).is_file() and (folder / name).stat().st_size > 0 for name in REQUIRED_FILES)
            item["status"] = "present" if complete else "missing"
            if complete:
                item["bytes"] = tree_size(folder)
        items.append(item)
    required_missing = [item["id"] for item in items if item["required"] and item["status"] not in ("present", "skipped_optional")]
    return dict(items=items, required_missing=required_missing, offline_ready=not required_missing)


def plan_download(manifest, models_dir, *, source_hf=None, source_torch=None, include_optional=False, only=None):
    """缺少的模型先看本機既有快取可否複製，否則列為下載；`only` 限定模型 ID；不動任何檔案、不連網。"""
    models_dir = Path(models_dir)
    source_hf = Path(source_hf) if source_hf else default_source_hf()
    source_torch = Path(source_torch) if source_torch else default_source_torch()
    lookup = {**{"hf:" + e["repo"]: e for e in manifest.get("hf", [])}, **{"torch:" + e["file"]: e for e in manifest.get("torch", [])},
              **{"gguf:" + e["dir"]: e for e in manifest.get("gguf", [])}, **{"ct2:" + e["key"]: e for e in manifest.get("ct2", [])}}
    items, total = [], 0
    for item in check_models(manifest, models_dir, include_optional)["items"]:
        if only is not None and item["id"] not in only:
            continue
        if item["status"] == "skipped_optional":
            continue
        entry = lookup[item["id"]]
        if item["status"] == "present":
            items.append(dict(item, action="present"))
            continue
        if item["kind"] == "hf":
            source = source_hf / repo_folder(item["repo"])
            target = models_dir / "hf" / repo_folder(item["repo"])
            if source.is_dir() and _hf_complete(source, entry):
                size = tree_size(source)
                items.append(dict(item, action="copy", source=str(source), target=str(target), bytes=size, revision=hf_revision(source)))
                total += size
            else:
                items.append(dict(item, action="download", target=str(models_dir / "hf"), revision=entry.get("revision"), bytes=entry.get("expected_bytes", 0)))
        elif item["kind"] == "torch":
            source = source_torch / item["file"]
            target = models_dir / "torch" / item["file"]
            if source.is_file():
                items.append(dict(item, action="copy", source=str(source), target=str(target), bytes=source.stat().st_size))
                total += source.stat().st_size
            else:
                url = entry.get("url") or TORCH_URLS.get(item["file"])
                items.append(dict(item, action="download" if url else "manual", target=str(target), url=url, bytes=entry.get("expected_bytes", 0)))
        elif item["kind"] == "ct2":
            items.append(dict(item, action="convert", revision=entry.get("revision"), bytes=entry.get("download_bytes", 0),
                              target=str(models_dir / "ct2" / item["key"])))
        else:
            items.append(dict(item, action="manual", target=str(models_dir / "gguf" / item["dir"])))
    return dict(models_dir=str(models_dir), items=items, total_bytes=total)


def _download_progress(progress):
    """HF snapshot 的 tqdm 計數是檔案，不冒充位元組；保留 SDK 自己的下載輸出。"""
    from tqdm.auto import tqdm
    class ModelProgress(tqdm):
        def display(self, *args, **kwargs):
            result = super().display(*args, **kwargs)
            if progress:
                progress(dict(files_done=self.n, files_total=self.total))
            return result
    return ModelProgress


def _default_hf_download(repo, cache_dir, revision=None, progress=None):
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=repo, cache_dir=str(cache_dir), revision=revision, tqdm_class=_download_progress(progress))


def _default_ct2_download(repo, local_dir, revision=None, allow_patterns=None, progress=None):
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=repo, revision=revision, local_dir=str(local_dir), allow_patterns=allow_patterns, tqdm_class=_download_progress(progress))


def _default_ct2_convert(source, target, quantization):
    # CTranslate2 官方轉換器：transformers 讀 safetensors（不執行 repo 內程式碼），輸出 model.bin＋config.json（含 alignment_heads）
    from ctranslate2.converters import TransformersConverter
    TransformersConverter(str(source), load_as_float16=True).convert(str(target), quantization=quantization)


def _default_ct2_tokenizer(source, target_file):
    # faster-whisper 需要 tokenizer.json；repo 沒附就用 vocab.json＋merges.txt＋added_tokens.json 在本機產生（標準 Whisper 詞彙 51865）
    source, target_file = Path(source), Path(target_file)
    if (source / "tokenizer.json").is_file():
        shutil.copy2(source / "tokenizer.json", target_file)
        return
    from transformers import WhisperTokenizerFast
    WhisperTokenizerFast.from_pretrained(str(source)).backend_tokenizer.save(str(target_file))


def _convert_ct2(item, models_dir, download, convert, tokenizer, progress=None):
    """下載官方權重→轉換到 <key>.staging→補 tokenizer／前處理設定／來源紀錄→驗證→改名成正式資料夾→刪原始權重。"""
    from .asr_models import PROVENANCE, REQUIRED_FILES
    from .fsutil import replace_with_retry
    base = models_dir / "ct2"
    source = base / "_source" / item["key"]
    staging = base / (item["key"] + ".staging")
    final = base / item["key"]
    if staging.exists():
        shutil.rmtree(staging)  # 上次失敗留下的半成品（本程式自己建立的暫存）
    if progress:
        progress(dict(id=item["id"], stage="download", bytes_total=item.get("bytes", 0)))
    kwargs = dict(revision=item.get('revision'), allow_patterns=list(CT2_SOURCE_PATTERNS))
    if download is _default_ct2_download:
        kwargs['progress'] = (lambda event: progress(dict(event, id=item['id'], stage='download'))) if progress else None
    download(item["repo"], source, **kwargs)
    if progress:
        progress(dict(id=item["id"], stage="convert"))
    try:
        convert(source, staging, "float16")
        tokenizer(source, staging / "tokenizer.json")
        if (source / "preprocessor_config.json").is_file() and not (staging / "preprocessor_config.json").is_file():
            shutil.copy2(source / "preprocessor_config.json", staging / "preprocessor_config.json")
        import importlib.metadata
        try:
            ct2_version = importlib.metadata.version("ctranslate2")
        except importlib.metadata.PackageNotFoundError:
            ct2_version = None
        (staging / PROVENANCE).write_text(json.dumps(dict(repo=item["repo"], revision=item.get("revision"), quantization="float16",
            license=item.get("license"), ctranslate2=ct2_version, converted_at=datetime.now(timezone.utc).isoformat(timespec="seconds")),
            ensure_ascii=False, indent=1), encoding="utf-8")
        missing = [name for name in REQUIRED_FILES if not (staging / name).is_file() or (staging / name).stat().st_size == 0]
        if missing:
            raise ValueError("CT2_CONVERSION_INCOMPLETE: " + ",".join(missing))
        if final.exists():
            shutil.rmtree(final)  # 舊的轉換結果（同一個 key、本程式建立）
        replace_with_retry(staging, final)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(source, ignore_errors=True)
    if source.parent.is_dir() and not any(source.parent.iterdir()):
        source.parent.rmdir()
    return tree_size(final)


def _default_file_download(url, target, progress=None):
    import urllib.request
    if not str(url).startswith("https://"):
        raise ValueError("INSECURE_DOWNLOAD_URL")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name + ".part")
    with urllib.request.urlopen(url, timeout=60) as response, staging.open("wb") as stream:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            stream.write(chunk)
            done += len(chunk)
            if progress:
                progress(dict(bytes_done=done, bytes_total=total))
        if total and done != total:
            raise ValueError('DOWNLOAD_SIZE_MISMATCH')
    from .fsutil import replace_with_retry
    replace_with_retry(staging, target)  # 幾 GB 的下載不能敗在最後一步改名被防毒占用


def run_download(plan, hf_download=None, file_download=None, progress=None, ct2_download=None, ct2_convert=None, ct2_tokenizer=None):
    """依計畫複製或下載；每項前後回報進度；完成後記 IMPORTED.json（source=download 或來源路徑）。"""
    models_dir = Path(plan["models_dir"])
    imported = _load_imported(models_dir)
    hf_download = hf_download or _default_hf_download
    file_download = file_download or _default_file_download
    stamp = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    results = []
    for item in plan["items"]:
        if item["action"] == "present":
            results.append(dict(item, status="present"))
            continue
        if item["action"] == "manual":
            results.append(dict(item, status="manual_required"))
            continue
        if progress:
            progress(dict(id=item["id"], stage="start", action=item["action"]))
        status = "failed"
        if item["action"] == "convert":
            try:
                size = _convert_ct2(item, models_dir, ct2_download or _default_ct2_download, ct2_convert or _default_ct2_convert,
                                    ct2_tokenizer or _default_ct2_tokenizer, progress)
                imported.setdefault("ct2", {})[item["key"]] = dict(repo=item["repo"], revision=item.get("revision"), bytes=size,
                                                                  source="download+convert", imported_at=stamp())
                status = "converted"
            except Exception as error:
                # 失敗不讓其他項目跟著停；只記錯誤型別（不外洩回應全文）
                if progress:
                    progress(dict(id=item["id"], stage="done", status="failed", error=type(error).__name__))
                results.append(dict(item, status="failed", error=type(error).__name__))
                continue
        elif item["action"] == "copy":
            source, target = Path(item["source"]), Path(item["target"])
            if item["kind"] == "hf":
                shutil.copytree(source, target, dirs_exist_ok=True, symlinks=False)
                copied = tree_size(target)
                imported.setdefault("hf", {})[item["repo"]] = dict(revision=hf_revision(target), bytes=copied, source=str(source), imported_at=stamp())
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied = target.stat().st_size
                imported.setdefault("torch", {})[item["file"]] = dict(bytes=copied, source=str(source), imported_at=stamp())
            status = "copied" if copied >= item.get("bytes", 0) else "size_mismatch"
        elif item["kind"] == "hf":
            kwargs = dict(revision=item.get('revision'))
            if hf_download is _default_hf_download:
                kwargs['progress'] = (lambda event: progress(dict(event, id=item['id'], stage='download'))) if progress else None
            hf_download(item["repo"], models_dir / "hf", **kwargs)
            folder = models_dir / "hf" / repo_folder(item["repo"])
            if _hf_complete(folder, item):
                imported.setdefault("hf", {})[item["repo"]] = dict(revision=hf_revision(folder), bytes=tree_size(folder), source="download", imported_at=stamp())
                status = "downloaded"
        elif item["kind"] == "torch":
            target = Path(item["target"])
            file_download(item["url"], target, progress=(lambda event: progress(dict(event, id=item["id"], stage="progress"))) if progress else None)
            if target.is_file() and target.stat().st_size > 0:
                imported.setdefault("torch", {})[item["file"]] = dict(bytes=target.stat().st_size, source="download", imported_at=stamp())
                status = "downloaded"
        if item['kind'] == 'hf' and status == 'copied' and not _hf_complete(Path(item['target']), item):
            status = 'failed'
        if progress:
            progress(dict(id=item["id"], stage="done", status=status))
        results.append(dict(item, status=status))
    models_dir.mkdir(parents=True, exist_ok=True)
    (models_dir / IMPORTED).write_text(json.dumps(imported, ensure_ascii=False, indent=1), encoding="utf-8")
    return dict(models_dir=str(models_dir), items=results)


def list_gguf(models_dir):
    """列出 models/gguf 下的 GGUF 檔（相對 gguf 目錄的 POSIX 路徑），供 llama.cpp 指定與介面顯示。"""
    base = Path(models_dir) / "gguf"
    if not base.is_dir():
        return []
    return sorted(path.relative_to(base).as_posix() for path in base.rglob("*.gguf") if path.is_file())
