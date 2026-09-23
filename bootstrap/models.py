"""模型清單比對（純標準函式庫版）：只看檔案是否存在與非空，不連網；下載在 M0-P3 補上。"""
import json
from pathlib import Path


def repo_folder(repo):
    return "models--" + repo.replace("/", "--")


def missing_models(manifest, models_dir, include_optional=False):
    """回傳缺少的模型 ID 清單（hf:repo、torch:file、gguf:dir）。"""
    models_dir = Path(models_dir)
    missing = []
    for entry in manifest.get("hf", []):
        if not entry.get("required", True) and not include_optional:
            continue
        snapshots = models_dir / "hf" / repo_folder(entry["repo"]) / "snapshots"
        if not snapshots.is_dir() or not any(child.is_dir() and any(child.iterdir()) for child in snapshots.iterdir()):
            missing.append("hf:" + entry["repo"])
    for entry in manifest.get("torch", []):
        if not entry.get("required", True) and not include_optional:
            continue
        target = models_dir / "torch" / entry["file"]
        if not target.is_file() or target.stat().st_size == 0:
            missing.append("torch:" + entry["file"])
    for entry in manifest.get("gguf", []):
        if not entry.get("required", False) and not include_optional:
            continue
        folder = models_dir / "gguf" / entry["dir"]
        if not folder.is_dir() or not any(folder.rglob("*.gguf")):
            missing.append("gguf:" + entry["dir"])
    return missing


def load_manifest(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
