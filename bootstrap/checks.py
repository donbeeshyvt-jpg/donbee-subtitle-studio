"""環境類軟體檢查：只偵測版本與可用性並給中文指引，不安裝、不修改系統。

項目：Python、FFmpeg／ffprobe、Node（yt-dlp EJS 用）、NVIDIA GPU、LM Studio 端點、llama-server 執行檔。
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

PYTHON_MIN = (3, 12)
LMSTUDIO_URL = "http://127.0.0.1:1234"
LLAMA_SERVER_URL = "http://127.0.0.1:8080"

GUIDANCE = {
    "python": "需要 Python 3.12：winget install Python.Python.3.12，或到 python.org 下載安裝後重新開啟。",
    "ffmpeg": "缺少 FFmpeg：winget install Gyan.FFmpeg（安裝後重新開啟終端讓 PATH 生效），或到 ffmpeg.org 下載後加入 PATH。",
    "ffprobe": "ffprobe 隨 FFmpeg 安裝：winget install Gyan.FFmpeg。",
    "node": "缺少 Node.js（YouTube 下載的 EJS 需要 JS runtime）：winget install OpenJS.NodeJS.LTS。",
    "gpu": "未偵測到 NVIDIA 顯示卡或驅動（nvidia-smi 不可用）：辨識將改用 CPU（較慢）；安裝或更新 NVIDIA 驅動後重新檢查。",
    "lmstudio": "LM Studio 未啟動或未開啟本機伺服器（預設 127.0.0.1:1234）：請開啟 LM Studio、載入模型並啟用 Local Server 後重新檢查；本程式只對接，不管理其模型。",
    "llama_server": "未找到 llama-server 執行檔：如需 llama.cpp，請安裝 llama.cpp 並將 llama-server 加入 PATH；模型 GGUF 放在專案 models/gguf/。啟動範例：llama-server -m models/gguf/<資料夾>/<檔名>.gguf --alias loaded-gguf -c 8192 -ngl 99 --host 127.0.0.1 --port 8080（--alias 要與供應者設定的模型 ID 一致）。",
}

LABELS = {"python": "Python", "ffmpeg": "FFmpeg", "ffprobe": "ffprobe", "node": "Node.js", "gpu": "NVIDIA 顯示卡",
          "lmstudio": "LM Studio 本機伺服器", "llama_server": "llama.cpp（llama-server）"}


def default_runner(argv, timeout=10):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return (result.stdout or "") + (result.stderr or "")


def default_http_get(url, timeout=2):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                return response.status, json.loads(body)
            except ValueError:
                return response.status, None
    except (urllib.error.URLError, OSError, ValueError):
        return None, None


def _item(key, status, version=None, details=None):
    item = dict(label=LABELS[key], status=status, version=version, details=details or {})
    if status != "ok":
        item["guidance"] = GUIDANCE[key]
    return item


def _tool_version(name, argv_flag, runner, which):
    path = which(name)
    if not path:
        return None, None
    try:
        output = runner([path, argv_flag], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return path, None
    found = re.search(r"version\s+([^\s]+)", output) or re.search(r"v?(\d+\.\d+[^\s]*)", output)
    return path, (found.group(1) if found else None)


def _gpu(runner, which):
    path = which("nvidia-smi")
    if not path:
        return _item("gpu", "missing")
    try:
        output = runner([path, "--query-gpu=driver_version,name,memory.total,memory.used", "--format=csv,noheader,nounits"], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return _item("gpu", "missing")
    parts = [part.strip() for part in output.strip().splitlines()[0].split(",")] if output.strip() else []
    if len(parts) < 4:
        return _item("gpu", "missing")
    digits = lambda text: int(re.sub(r"[^\d]", "", text) or 0)
    return _item("gpu", "ok", version=parts[0], details=dict(name=parts[1], memory_total_mib=digits(parts[2]), memory_used_mib=digits(parts[3])))


def _lmstudio(http_get):
    status, data = http_get(LMSTUDIO_URL + "/api/v0/models", timeout=2)
    if status == 200 and isinstance(data, dict):
        rows = data.get("data") if isinstance(data.get("data"), list) else []
        loaded = [row.get("id") for row in rows if isinstance(row, dict) and row.get("state") == "loaded"]
        return _item("lmstudio", "ok", details=dict(loaded_models=loaded, models=len(rows)))
    status, data = http_get(LMSTUDIO_URL + "/v1/models", timeout=2)
    if status == 200:
        rows = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), list) else []
        return _item("lmstudio", "ok", details=dict(loaded_models=[], models=len(rows)))
    return _item("lmstudio", "unreachable")


def _llama_server(http_get, which, project_root=None):
    # PATH 上有的優先；沒有時找專案內 tools/llama.cpp/（2026-09-21 使用者要求先裝好，但不改系統 PATH）
    path, source = which("llama-server"), "path"
    if not path and project_root is not None:
        local = Path(project_root) / "tools" / "llama.cpp" / ("llama-server.exe" if sys.platform == "win32" else "llama-server")
        if local.is_file():
            path, source = str(local), "project"
    status, _ = http_get(LLAMA_SERVER_URL + "/health", timeout=2)
    if status == 200:
        return _item("llama_server", "ok", details=dict(path=path, source=source if path else None, running=True))
    if path:
        return _item("llama_server", "ok", details=dict(path=path, source=source, running=False))
    return _item("llama_server", "missing")


def collect_env(runner=None, which=None, http_get=None, python_version=None, project_root=None):
    runner = runner or default_runner
    which = which or shutil.which
    http_get = http_get or default_http_get
    version = tuple(python_version or sys.version_info[:3])
    items = {"python": _item("python", "ok" if version[:2] >= PYTHON_MIN else "outdated", version=".".join(str(part) for part in version[:3]))}
    for name in ("ffmpeg", "ffprobe", "node"):
        path, found = _tool_version(name, "-version" if name in ("ffmpeg", "ffprobe") else "--version", runner, which)
        items[name] = _item(name, "ok" if path and found else "missing", version=found, details=dict(path=path) if path else {})
    items["gpu"] = _gpu(runner, which)
    items["lmstudio"] = _lmstudio(http_get)
    items["llama_server"] = _llama_server(http_get, which, Path(__file__).resolve().parents[1] if project_root is None else project_root)
    return dict(checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), items=items)
