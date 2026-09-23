"""M0-P3：模型清單比對（T-P05）、下載計畫 dry-run（T-P06）、選用模型確認流程與 API（P3-c）。下載器一律以替身注入，不連網。"""
import json
from pathlib import Path
import time

from fastapi.testclient import TestClient

from app.studio import model_store


def _fake_hf_repo(hub, repo, size=3, revision="abc123"):
    folder = hub / model_store.repo_folder(repo)
    (folder / "refs").mkdir(parents=True)
    (folder / "refs" / "main").write_text(revision, encoding="utf-8")
    snapshot = folder / "snapshots" / revision
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("x" * size, encoding="utf-8")
    (snapshot / "model.bin").write_bytes(b"\0" * (size * 10))
    return folder


MANIFEST = {"version": 1, "hf": [
    {"repo": "MIT/ast-finetuned-audioset-10-10-0.4593", "required": True},
    {"repo": "Systran/faster-whisper-large-v3", "required": True, "revision": "zzz999"},
    {"repo": "microsoft/VibeVoice-ASR", "required": False, "enable_when": "engine_b"}],
    "torch": [{"file": "hub/checkpoints/wav2vec2_fairseq_base_ls960_asr_ls960.pth", "required": True,
               "url": "https://download.pytorch.org/torchaudio/models/wav2vec2_fairseq_base_ls960_asr_ls960.pth"}],
    "gguf": [{"dir": "Qwen2.5-1.5B-Instruct-GGUF", "required": False}]}


# ---- T-P05 清單比對 ----

def test_check_models_reports_missing_size_and_revision_problems(tmp_path):
    models = tmp_path / "models"
    _fake_hf_repo(models / "hf", "MIT/ast-finetuned-audioset-10-10-0.4593")
    _fake_hf_repo(models / "hf", "Systran/faster-whisper-large-v3", revision="abc123")
    (models / "IMPORTED.json").write_text(json.dumps({"hf": {"MIT/ast-finetuned-audioset-10-10-0.4593": {"revision": "abc123", "bytes": 999999}}, "torch": {}}), encoding="utf-8")
    result = model_store.check_models(MANIFEST, models)
    status = {item["id"]: item["status"] for item in result["items"]}
    assert status["hf:MIT/ast-finetuned-audioset-10-10-0.4593"] == "size_mismatch"
    assert status["hf:Systran/faster-whisper-large-v3"] == "revision_mismatch"
    assert status["hf:microsoft/VibeVoice-ASR"] == "skipped_optional"
    assert status["torch:hub/checkpoints/wav2vec2_fairseq_base_ls960_asr_ls960.pth"] == "missing"
    assert status["gguf:Qwen2.5-1.5B-Instruct-GGUF"] == "skipped_optional"
    assert result["offline_ready"] is False
    assert set(result["required_missing"]) == {"hf:MIT/ast-finetuned-audioset-10-10-0.4593", "hf:Systran/faster-whisper-large-v3",
                                               "torch:hub/checkpoints/wav2vec2_fairseq_base_ls960_asr_ls960.pth"}


def test_check_models_offline_ready_when_everything_present(tmp_path):
    models = tmp_path / "models"
    _fake_hf_repo(models / "hf", "MIT/ast-finetuned-audioset-10-10-0.4593")
    _fake_hf_repo(models / "hf", "Systran/faster-whisper-large-v3", revision="zzz999")
    target = models / "torch" / "hub" / "checkpoints" / "wav2vec2_fairseq_base_ls960_asr_ls960.pth"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\1" * 10)
    result = model_store.check_models(MANIFEST, models)
    assert result["offline_ready"] is True and result["required_missing"] == []
    optional = model_store.check_models(MANIFEST, models, include_optional=True)
    assert {item["id"]: item["status"] for item in optional["items"]}["hf:microsoft/VibeVoice-ASR"] == "missing"
    assert optional["offline_ready"] is True, "選用模型缺少不影響離線可用"


# ---- T-P06 下載計畫 dry-run ----

def test_plan_download_prefers_copy_and_lists_download_without_network(tmp_path):
    models = tmp_path / "models"
    source_hf = tmp_path / "home-hf"
    _fake_hf_repo(source_hf, "MIT/ast-finetuned-audioset-10-10-0.4593")
    plan = model_store.plan_download(MANIFEST, models, source_hf=source_hf, source_torch=tmp_path / "none")
    actions = {item["id"]: item["action"] for item in plan["items"]}
    assert actions["hf:MIT/ast-finetuned-audioset-10-10-0.4593"] == "copy"
    assert actions["hf:Systran/faster-whisper-large-v3"] == "download"
    assert actions["torch:hub/checkpoints/wav2vec2_fairseq_base_ls960_asr_ls960.pth"] == "download"
    assert "hf:microsoft/VibeVoice-ASR" not in actions, "選用未啟用時不列入"
    only = model_store.plan_download(MANIFEST, models, source_hf=source_hf, source_torch=tmp_path / "none", include_optional=True,
                                     only=["hf:microsoft/VibeVoice-ASR"])
    assert [item["id"] for item in only["items"]] == ["hf:microsoft/VibeVoice-ASR"] and only["items"][0]["action"] == "download"


def test_run_download_uses_injected_downloaders_and_records_import(tmp_path):
    models = tmp_path / "models"
    plan = model_store.plan_download(MANIFEST, models, source_hf=tmp_path / "none", source_torch=tmp_path / "none")
    calls = []
    def hf_download(repo, cache_dir, revision=None):
        calls.append(("hf", repo, str(cache_dir), revision))
        _fake_hf_repo(Path(cache_dir), repo, revision=revision or "dl001")
    def file_download(url, target, progress=None):
        calls.append(("file", url, str(target)))
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"\2" * 20)
    events = []
    result = model_store.run_download(plan, hf_download=hf_download, file_download=file_download, progress=lambda event: events.append(event))
    assert {item["status"] for item in result["items"]} == {"downloaded"}
    assert ("hf", "Systran/faster-whisper-large-v3", str(models / "hf"), "zzz999") in calls
    assert any(call[0] == "file" and call[1].endswith(".pth") for call in calls)
    record = json.loads((models / "IMPORTED.json").read_text(encoding="utf-8"))
    assert record["hf"]["Systran/faster-whisper-large-v3"]["source"] == "download"
    assert model_store.check_models(MANIFEST, models)["offline_ready"] is True
    assert events and events[0]["id"] and events[0]["stage"] == "start"


# ---- P3-c API：狀態、確認後下載、不重複執行 ----

def client(tmp_path):
    from app.studio.api import create_app
    app = create_app(tmp_path, start_workers=False)
    result = TestClient(app)
    assert result.get("/v1/session").status_code == 200
    return result, app


def test_models_status_and_download_requires_confirmation(monkeypatch, tmp_path):
    from app.studio import api as api_module
    monkeypatch.setenv("STUDIO_MODELS_DIR", str(tmp_path / "models"))
    from app import config
    import importlib
    importlib.reload(config)
    monkeypatch.setattr(api_module, "load_manifest", lambda: MANIFEST)
    c, app = client(tmp_path / "data")
    status = c.get("/v1/models/status").json()
    assert status["offline_ready"] is False and "hf:MIT/ast-finetuned-audioset-10-10-0.4593" in status["required_missing"]
    assert c.post("/v1/models/download", json={"ids": ["hf:microsoft/VibeVoice-ASR"], "confirm": False}).status_code == 422
    assert c.post("/v1/models/download", json={"ids": ["hf:unknown"], "confirm": True}).status_code == 422
    calls = []
    def fake_run(plan, hf_download=None, file_download=None, progress=None):
        for item in plan["items"]:
            progress({"id": item["id"], "stage": "start"})
            calls.append(item["id"])
        return dict(models_dir=plan["models_dir"], items=[dict(item, status="downloaded") for item in plan["items"]])
    monkeypatch.setattr(api_module.model_store, "run_download", fake_run)
    started = c.post("/v1/models/download", json={"ids": ["hf:microsoft/VibeVoice-ASR"], "confirm": True})
    assert started.status_code == 202 and started.json()["status"] in {"running", "done"}
    app.state.model_download_thread.join(5)
    progress = c.get("/v1/models/status").json()["download"]
    assert progress["status"] == "done" and calls == ["hf:microsoft/VibeVoice-ASR"]
    importlib.reload(config)


def test_models_download_rejects_concurrent_runs(monkeypatch, tmp_path):
    from app.studio import api as api_module
    monkeypatch.setenv("STUDIO_MODELS_DIR", str(tmp_path / "models"))
    from app import config
    import importlib
    importlib.reload(config)
    monkeypatch.setattr(api_module, "load_manifest", lambda: MANIFEST)
    c, app = client(tmp_path / "data")
    def slow_run(plan, hf_download=None, file_download=None, progress=None):
        time.sleep(0.5)
        return dict(models_dir=plan["models_dir"], items=[dict(item, status="downloaded") for item in plan["items"]])
    monkeypatch.setattr(api_module.model_store, "run_download", slow_run)
    assert c.post("/v1/models/download", json={"ids": ["hf:MIT/ast-finetuned-audioset-10-10-0.4593"], "confirm": True}).status_code == 202
    assert c.post("/v1/models/download", json={"ids": ["hf:MIT/ast-finetuned-audioset-10-10-0.4593"], "confirm": True}).status_code == 409
    app.state.model_download_thread.join(5)
    importlib.reload(config)
