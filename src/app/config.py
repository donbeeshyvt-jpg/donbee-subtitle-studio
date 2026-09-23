"""全域設定：路徑單一來源（相對專案根、環境變數可覆寫）、快取綁定、watchdog 上限（防無限迴圈）。"""
import os
from pathlib import Path

# 專案根：src/app/config.py 的上兩層
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve(env_name, default):
    override = os.environ.get(env_name)
    return str(Path(override)) if override else str(default)


def resolve_vibevoice_root():
    """既有 VibeVoice 專案根（套件安裝來源、benchmark 素材與過渡用 .venv）；環境變數 STUDIO_VIBEVOICE_ROOT 優先。"""
    return _resolve("STUDIO_VIBEVOICE_ROOT", PROJECT_ROOT / "docs" / "VibeVoice-main")


def resolve_data_dir():
    """使用者資料目錄（config.json、secrets、SQLite、工作成果）：預設專案內 data/，STUDIO_DATA_DIR 可覆寫。"""
    return _resolve("STUDIO_DATA_DIR", PROJECT_ROOT / "data")


def legacy_data_dir():
    """搬進專案前的舊資料目錄（%LOCALAPPDATA%/DongbiStudio），只用於遷移偵測；沒有 LOCALAPPDATA 時回 None。"""
    base = os.environ.get("LOCALAPPDATA")
    return str(Path(base) / "DongbiStudio") if base else None


def resolve_models_dir():
    """本機模型固定位置：預設專案內 models/，STUDIO_MODELS_DIR 可覆寫。"""
    return _resolve("STUDIO_MODELS_DIR", PROJECT_ROOT / "models")


def bind_model_caches(models_dir):
    """把 Hugging Face（models/hf）與 torch（models/torch）快取綁到專案內並建立目錄；
    使用者自設的 HF_HUB_CACHE／TORCH_HOME 優先（setdefault）。回傳實際生效的路徑。"""
    base = Path(models_dir)
    hf, torch_home, gguf = base / "hf", base / "torch", base / "gguf"
    for folder in (hf, torch_home, gguf):
        folder.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HUB_CACHE", str(hf))
    os.environ.setdefault("TORCH_HOME", str(torch_home))
    return dict(hf=os.environ["HF_HUB_CACHE"], torch=os.environ["TORCH_HOME"], gguf=str(gguf))


VIBEVOICE_ROOT = resolve_vibevoice_root()
# v1 測試與基準素材目錄；不存在時各測試自行 skip
BENCHMARK_DIR = str(Path(VIBEVOICE_ROOT) / "benchmark")
DATA_DIR = resolve_data_dir()
MODELS_DIR = resolve_models_dir()
_caches = bind_model_caches(MODELS_DIR)
HF_CACHE_DIR = _caches["hf"]
TORCH_HOME_DIR = _caches["torch"]
GGUF_DIR = _caches["gguf"]

# HF 與 SSL 環境（本機已知問題）—— 在任何 transformers/whisperx import 前設定
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

# watchdog 上限（防無限迴圈 / VibeVoice 重複迴圈卡死）
ASR_TIMEOUT_SEC = 600            # 單一區段 ASR 超時即中止
MUSIC_GATE_TIMEOUT_SEC = 300
VV_TOKENS_PER_SEC = 12           # VibeVoice 逐 chunk token 上限估算係數（~12 token/秒）

# 區段補邊（避免切到字）
REGION_PAD_SEC = 0.30

# 餵 ASR 的音訊取樣率
ASR_SAMPLE_RATE = 16000

# 逐段語言白名單
SUPPORTED_LANGS = {"ja", "zh", "en"}
