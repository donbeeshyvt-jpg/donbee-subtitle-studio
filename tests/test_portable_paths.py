"""M0-P1：資料與模型目錄入專案、舊資料遷移、模型匯入計畫、vendor 上游腳本、GGUF 位置（T-P01..T-P04、T-P13、P1-e）。"""
import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def _reload_config(monkeypatch, **env):
    for key in ("STUDIO_DATA_DIR", "STUDIO_MODELS_DIR", "STUDIO_VIBEVOICE_ROOT", "HF_HUB_CACHE", "TORCH_HOME"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from app import config
    return importlib.reload(config)


# ---- T-P01 資料目錄預設與遷移 ----

def test_data_dir_defaults_to_project_and_env_override(monkeypatch, tmp_path):
    config = _reload_config(monkeypatch)
    assert Path(config.DATA_DIR) == config.PROJECT_ROOT / "data"
    from app.studio import store
    assert store.default_root() == config.PROJECT_ROOT / "data"
    config = _reload_config(monkeypatch, STUDIO_DATA_DIR=str(tmp_path / "elsewhere"))
    assert Path(config.DATA_DIR) == tmp_path / "elsewhere"
    assert store.default_root() == tmp_path / "elsewhere"
    _reload_config(monkeypatch)


def _make_legacy(legacy):
    legacy.mkdir(parents=True)
    (legacy / "config.json").write_text(json.dumps({"token": "legacy-token", "roots": {}, "providers": []}), encoding="utf-8")
    (legacy / "secrets.json").write_text(json.dumps({"api-deepseek": "sk-legacy"}), encoding="utf-8")
    db = sqlite3.connect(legacy / "studio.sqlite3")
    db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("INSERT INTO metadata VALUES ('schema_version','1')")
    db.commit()
    db.close()
    (legacy / "artifacts").mkdir()
    (legacy / "artifacts" / "a.txt").write_text("artifact", encoding="utf-8")
    (legacy / "jobs").mkdir()
    (legacy / "coordinator.lock").write_text("pid", encoding="utf-8")


def test_legacy_migration_copies_without_deleting(tmp_path):
    from app.studio import data_migration
    legacy = tmp_path / "local" / "DongbiStudio"
    data = tmp_path / "data"
    _make_legacy(legacy)
    detection = data_migration.detect_legacy(data, legacy)
    assert detection["needed"] is True
    result = data_migration.migrate(data, legacy)
    assert (data / "config.json").read_text(encoding="utf-8") == (legacy / "config.json").read_text(encoding="utf-8")
    assert (data / "secrets.json").is_file()
    assert (data / "artifacts" / "a.txt").is_file()
    assert (data / "jobs").is_dir()
    assert not (data / "coordinator.lock").exists()
    db = sqlite3.connect(data / "studio.sqlite3")
    assert db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0] == "1"
    db.close()
    record = json.loads((data / "MIGRATED_FROM.json").read_text(encoding="utf-8"))
    assert Path(record["source"]) == legacy and record["copied"]
    assert (legacy / "config.json").is_file() and (legacy / "studio.sqlite3").is_file()
    assert data_migration.detect_legacy(data, legacy)["needed"] is False
    assert result["copied"]


def test_detect_legacy_is_false_when_no_legacy_or_data_present(tmp_path):
    from app.studio import data_migration
    assert data_migration.detect_legacy(tmp_path / "data", tmp_path / "missing")["needed"] is False
    legacy = tmp_path / "legacy"
    _make_legacy(legacy)
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.json").write_text("{}", encoding="utf-8")
    assert data_migration.detect_legacy(data, legacy)["needed"] is False


def test_create_app_uses_project_data_dir_and_migrates(monkeypatch, tmp_path):
    legacy = tmp_path / "local" / "DongbiStudio"
    _make_legacy(legacy)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    _reload_config(monkeypatch, STUDIO_DATA_DIR=str(tmp_path / "data"))
    from app.studio.api import create_app
    app = create_app(None, start_workers=False)
    assert app.state.store.root == tmp_path / "data"
    assert app.state.config["token"] == "legacy-token"
    assert (tmp_path / "data" / "MIGRATED_FROM.json").is_file()
    _reload_config(monkeypatch)


# ---- T-P02 模型目錄永遠綁定 ----

def test_model_caches_always_bound_and_created(monkeypatch, tmp_path):
    config = _reload_config(monkeypatch, STUDIO_MODELS_DIR=str(tmp_path))
    assert Path(config.HF_CACHE_DIR) == tmp_path / "hf" and (tmp_path / "hf").is_dir()
    assert Path(config.TORCH_HOME_DIR) == tmp_path / "torch" and (tmp_path / "torch").is_dir()
    assert Path(config.GGUF_DIR) == tmp_path / "gguf" and (tmp_path / "gguf").is_dir()
    assert Path(os.environ["HF_HUB_CACHE"]) == tmp_path / "hf"
    assert Path(os.environ["TORCH_HOME"]) == tmp_path / "torch"
    config = _reload_config(monkeypatch, STUDIO_MODELS_DIR=str(tmp_path), HF_HUB_CACHE=str(tmp_path / "user-hf"))
    assert Path(config.HF_CACHE_DIR) == tmp_path / "user-hf", "使用者自設環境變數優先"
    _reload_config(monkeypatch)


# ---- T-P03 模型匯入計畫（假快取） ----

def _fake_hf_repo(hub, repo, size=3):
    folder = hub / ("models--" + repo.replace("/", "--"))
    (folder / "refs").mkdir(parents=True)
    (folder / "refs" / "main").write_text("abc123", encoding="utf-8")
    snapshot = folder / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("x" * size, encoding="utf-8")
    (snapshot / "model.bin").write_bytes(b"\0" * (size * 10))
    return folder


def test_import_plan_and_copy_with_fake_cache(tmp_path):
    from app.studio import model_store
    source_hf = tmp_path / "home-hf"
    source_torch = tmp_path / "home-torch"
    _fake_hf_repo(source_hf, "MIT/ast-finetuned-audioset-10-10-0.4593")
    (source_torch / "hub" / "checkpoints").mkdir(parents=True)
    (source_torch / "hub" / "checkpoints" / "wav2vec2_fairseq_base_ls960_asr_ls960.pth").write_bytes(b"\1" * 40)
    manifest = {"version": 1, "hf": [
        {"repo": "MIT/ast-finetuned-audioset-10-10-0.4593", "required": True},
        {"repo": "microsoft/VibeVoice-ASR", "required": False, "enable_when": "engine_b"},
        {"repo": "Systran/faster-whisper-large-v3", "required": True}],
        "torch": [{"file": "hub/checkpoints/wav2vec2_fairseq_base_ls960_asr_ls960.pth", "required": True}], "gguf": []}
    models = tmp_path / "models"
    plan = model_store.plan_import(manifest, models, source_hf=source_hf, source_torch=source_torch, include_optional=False)
    actions = {item["id"]: item["action"] for item in plan["items"]}
    assert actions["hf:MIT/ast-finetuned-audioset-10-10-0.4593"] == "copy"
    assert actions["hf:Systran/faster-whisper-large-v3"] == "missing_source"
    assert actions["hf:microsoft/VibeVoice-ASR"] == "skipped_optional"
    assert actions["torch:hub/checkpoints/wav2vec2_fairseq_base_ls960_asr_ls960.pth"] == "copy"
    assert plan["total_bytes"] == 3 + 30 + 6 + 40, "hf 目錄含 refs/main（6 bytes）也要複製"
    result = model_store.run_import(plan)
    copied = models / "hf" / "models--MIT--ast-finetuned-audioset-10-10-0.4593" / "snapshots" / "abc123" / "model.bin"
    assert copied.is_file() and copied.stat().st_size == 30
    assert (models / "torch" / "hub" / "checkpoints" / "wav2vec2_fairseq_base_ls960_asr_ls960.pth").stat().st_size == 40
    assert (source_hf / "models--MIT--ast-finetuned-audioset-10-10-0.4593" / "snapshots" / "abc123" / "model.bin").is_file()
    assert {item["status"] for item in result["items"] if item["action"] == "copy"} == {"copied"}
    record = json.loads((models / "IMPORTED.json").read_text(encoding="utf-8"))
    assert record["hf"]["MIT/ast-finetuned-audioset-10-10-0.4593"]["revision"] == "abc123"
    again = model_store.plan_import(manifest, models, source_hf=source_hf, source_torch=source_torch, include_optional=False)
    assert {item["action"] for item in again["items"] if item["id"].startswith("hf:MIT") or item["id"].startswith("torch:")} == {"present"}


def test_import_plan_includes_optional_when_requested(tmp_path):
    from app.studio import model_store
    source_hf = tmp_path / "home-hf"
    _fake_hf_repo(source_hf, "microsoft/VibeVoice-ASR", size=5)
    manifest = {"version": 1, "hf": [{"repo": "microsoft/VibeVoice-ASR", "required": False, "enable_when": "engine_b"}], "torch": [], "gguf": []}
    plan = model_store.plan_import(manifest, tmp_path / "models", source_hf=source_hf, source_torch=tmp_path / "none", include_optional=True)
    assert plan["items"][0]["action"] == "copy" and plan["total_bytes"] == 5 + 50 + 6


# ---- T-P04 vendor 上游腳本 ----

def test_vendor_scripts_importable_without_reference_dir(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1] / "src" / "app" / "vendor" / "vibevoice"
    for name in ("gen_srt.py", "vibevoice_asr_to_srt.py", "LICENSE", "NOTICE.md", "__init__.py"):
        assert (root / name).is_file(), name
    _reload_config(monkeypatch, STUDIO_VIBEVOICE_ROOT=str(tmp_path / "missing-reference"))
    from app import reuse
    importlib.reload(reuse)
    assert reuse.is_tag("[Music]") is True and reuse.fmt(3661.5) == "01:01:01,500"
    assert reuse._is_tag is not None, "應由 vendor 匯入上游實作，而非退回本地實作"
    import app.vendor.vibevoice.gen_srt as gen_srt
    assert gen_srt.is_tag("[唱歌]") is True
    assert importlib.util.find_spec("app.vendor.vibevoice.vibevoice_asr_to_srt") is not None
    from app import vv_worker
    importlib.reload(vv_worker)
    assert str(tmp_path / "missing-reference") not in sys.path, "vv_worker 不再依賴參考目錄的 sys.path"
    _reload_config(monkeypatch)


# ---- T-P13 GGUF 位置與 P1-e 路徑資訊 ----

def test_gguf_listing_and_capabilities_paths(monkeypatch, tmp_path):
    config = _reload_config(monkeypatch, STUDIO_MODELS_DIR=str(tmp_path / "models"), STUDIO_DATA_DIR=str(tmp_path / "data"))
    from app.studio import model_store
    (Path(config.GGUF_DIR) / "Qwen-test").mkdir(parents=True)
    (Path(config.GGUF_DIR) / "Qwen-test" / "q.gguf").write_bytes(b"\0" * 8)
    assert model_store.list_gguf(config.MODELS_DIR) == ["Qwen-test/q.gguf"]
    from app.studio.api import create_app
    client = TestClient(create_app(tmp_path / "data", start_workers=False))
    assert client.get("/v1/session").status_code == 200
    paths = client.get("/v1/capabilities").json()["paths"]
    assert Path(paths["models_dir"]) == tmp_path / "models"
    assert Path(paths["hf_cache"]) == tmp_path / "models" / "hf"
    assert Path(paths["torch_home"]) == tmp_path / "models" / "torch"
    assert paths["gguf_models"] == ["Qwen-test/q.gguf"]
    assert paths["data_dir"] and paths["project_root"] and "python" in paths
    assert paths["in_project"]["models_dir"] is False, "測試用 tmp 目錄不在專案內，旗標須為 False"
    _reload_config(monkeypatch)
