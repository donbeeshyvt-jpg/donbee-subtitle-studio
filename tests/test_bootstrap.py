"""M0-P2：啟動自檢與依賴安裝（T-P08 快速路徑、T-P09 只補缺、T-P10 環境檢查與 doctor --env、環境 API）。全部以替身執行，不真裝套件、不連網。"""
import json
from pathlib import Path
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bootstrap import checks, deps, lockfile, run as bootstrap_run  # noqa: E402


LOCK_TEXT = """# requirements.lock — 測試用
# group: core
fastapi==0.138.1
httpx==0.28.1
# group: models
torch==2.11.0
torchaudio==2.11.0
faster-whisper==1.2.1
# group: engine_b
bitsandbytes==0.49.2
-e ./docs/VibeVoice-main
# group: dev
pytest==9.1.1
"""


# ---- T-P09 鎖定清單與只補缺 ----

def test_parse_lock_groups_and_editable():
    entries = lockfile.parse_lock(LOCK_TEXT)
    by_name = {entry["name"]: entry for entry in entries}
    assert by_name["fastapi"] == {"name": "fastapi", "version": "0.138.1", "group": "core", "editable": None, "spec": "fastapi==0.138.1"}
    assert by_name["torch"]["group"] == "models"
    assert by_name["vibevoice"]["editable"] == "./docs/VibeVoice-main" and by_name["vibevoice"]["group"] == "engine_b"
    assert lockfile.groups(entries) == ["core", "models", "engine_b", "dev"]


def test_plan_installs_only_missing_or_mismatched():
    entries = lockfile.parse_lock(LOCK_TEXT)
    installed = {"fastapi": "0.138.1", "httpx": "0.27.0", "torch": "2.11.0+cu128", "torchaudio": "2.11.0+cu128", "pytest": "9.1.1"}
    plan = deps.plan_installs(entries, installed, groups=("core", "models"))
    assert [item["name"] for item in plan] == ["httpx", "faster-whisper"]
    assert plan[0]["reason"] == "version_mismatch" and plan[1]["reason"] == "missing"
    assert deps.plan_installs(entries, {**installed, "httpx": "0.28.1", "faster-whisper": "1.2.1"}, groups=("core", "models")) == []
    optional = deps.plan_installs(entries, installed, groups=("engine_b",))
    assert {item["name"] for item in optional} == {"bitsandbytes", "vibevoice"}


def test_select_torch_index_by_gpu():
    assert deps.select_torch_index(gpu_available=True) == ("cu128", "https://download.pytorch.org/whl/cu128")
    assert deps.select_torch_index(gpu_available=False) == ("cpu", "https://download.pytorch.org/whl/cpu")


def test_pip_commands_shape_argv_without_running(tmp_path):
    python = str(tmp_path / "python.exe")
    entries = lockfile.parse_lock(LOCK_TEXT)
    by_name = {entry["name"]: entry for entry in entries}
    plain = deps.pip_command(python, [by_name["httpx"]], torch_variant="cu128")
    assert plain[:5] == [python, "-m", "pip", "install", "--no-input"] and plain[-1] == "httpx==0.28.1"
    torch_cmd = deps.pip_command(python, [by_name["torch"], by_name["torchaudio"]], torch_variant="cu128")
    assert "--index-url" in torch_cmd and "https://download.pytorch.org/whl/cu128" in torch_cmd
    assert "torch==2.11.0+cu128" in torch_cmd and "torchaudio==2.11.0+cu128" in torch_cmd
    editable = deps.pip_command(python, [by_name["vibevoice"]], torch_variant="cu128", project_root=tmp_path)
    assert editable[-2:] == ["-e", str(tmp_path / "docs" / "VibeVoice-main")]


# ---- T-P10 環境檢查與指引 ----

def fake_runner(outputs):
    def run(argv, timeout=10):
        key = Path(argv[0]).stem.lower()  # 替身以執行檔名（去副檔名）對應輸出
        if key not in outputs:
            raise FileNotFoundError(argv[0])
        return outputs[key]
    return run


def test_collect_env_reports_status_versions_and_guidance():
    outputs = {"ffmpeg": "ffmpeg version 8.1-full_build Copyright", "ffprobe": "ffprobe version 8.1-full_build",
               "nvidia-smi": "610.62, NVIDIA GeForce RTX 4070 Ti, 12282 MiB, 3543 MiB"}
    which = lambda name: {"ffmpeg": "C:/tools/ffmpeg.exe", "ffprobe": "C:/tools/ffprobe.exe", "nvidia-smi": "C:/nvidia-smi.exe"}.get(name)
    http = lambda url, timeout=2: (200, {"data": [{"id": "m", "state": "loaded"}]}) if "1234" in url else (None, None)
    report = checks.collect_env(runner=fake_runner(outputs), which=which, http_get=http, python_version=(3, 12, 10),
                                project_root=Path('/nonexistent-project'))  # 不受本機專案裡真的裝了 llama-server 影響
    items = report["items"]
    assert items["python"]["status"] == "ok" and items["python"]["version"] == "3.12.10"
    assert items["ffmpeg"]["status"] == "ok" and items["ffmpeg"]["version"].startswith("8.1")
    assert items["node"]["status"] == "missing" and "winget" in items["node"]["guidance"]
    assert items["gpu"]["status"] == "ok" and items["gpu"]["details"]["memory_total_mib"] == 12282
    assert items["lmstudio"]["status"] == "ok" and items["lmstudio"]["details"]["loaded_models"] == ["m"]
    assert items["llama_server"]["status"] == "missing" and items["llama_server"]["guidance"]
    assert report["checked_at"] and all("guidance" in item for item in items.values() if item["status"] != "ok")
    assert all(item["label"] for item in items.values()), "每項要有繁體中文名稱"


def test_collect_env_python_too_old_and_gpu_missing():
    report = checks.collect_env(runner=fake_runner({}), which=lambda name: None, http_get=lambda url, timeout=2: (None, None), python_version=(3, 10, 0))
    assert report["items"]["python"]["status"] == "outdated" and "3.12" in report["items"]["python"]["guidance"]
    assert report["items"]["gpu"]["status"] == "missing" and report["items"]["lmstudio"]["status"] == "unreachable"


# ---- T-P08 快速路徑與計畫 ----

def _state(**overrides):
    state = dict(python_ok=True, venv_python="python.exe", venv_ok=True, installed={"fastapi": "0.138.1", "httpx": "0.28.1",
                 "torch": "2.11.0+cu128", "torchaudio": "2.11.0+cu128", "faster-whisper": "1.2.1"},
                 tools_missing=[], models_missing=[], gpu_available=True, groups=("core", "models"))
    state.update(overrides)
    return state


def test_build_plan_fast_path_has_no_actions_and_no_network():
    entries = lockfile.parse_lock(LOCK_TEXT)
    plan = bootstrap_run.build_plan(entries, _state())
    assert plan["actions"] == [] and plan["network_needed"] is False and plan["fast_path"] is True


def test_build_plan_lists_guides_installs_and_venv():
    entries = lockfile.parse_lock(LOCK_TEXT)
    plan = bootstrap_run.build_plan(entries, _state(tools_missing=["ffmpeg"], installed={"fastapi": "0.138.1"}, venv_ok=False, models_missing=["hf:MIT/ast"]))
    kinds = [action["kind"] for action in plan["actions"]]
    assert kinds[0] == "create_venv" and "pip" in kinds and "guide" in kinds and "models" in kinds
    pip_action = next(action for action in plan["actions"] if action["kind"] == "pip")
    assert {item["name"] for item in pip_action["items"]} == {"httpx", "torch", "torchaudio", "faster-whisper"}
    guide = next(action for action in plan["actions"] if action["kind"] == "guide")
    assert guide["tool"] == "ffmpeg" and "winget" in guide["guidance"]
    assert plan["network_needed"] is True and plan["fast_path"] is False
    assert not any(action["kind"] == "install_system" for action in plan["actions"]), "環境類軟體只指引不安裝"


# ---- doctor --env 與環境 API ----

def test_doctor_env_prints_local_environment(monkeypatch, capsys):
    from app.studio import cli
    fake = {"checked_at": "2026-09-16T00:00:00+00:00", "items": {"python": {"label": "Python", "status": "ok", "version": "3.12.10"}}}
    monkeypatch.setattr(cli, "environment_report", lambda: fake)
    def handler(request):
        return httpx.Response(200, json={"tools": {}, "paths": {}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as api:
        assert cli.main(["doctor", "--env", "--json"], client=api) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["environment"]["items"]["python"]["status"] == "ok"


def test_environment_api_reports_and_rechecks(monkeypatch, tmp_path):
    from app.studio import api as api_module
    calls = []
    def fake_report():
        calls.append(1)
        return {"checked_at": f"t{len(calls)}", "items": {"ffmpeg": {"label": "FFmpeg", "status": "missing", "guidance": "winget install Gyan.FFmpeg"}}}
    monkeypatch.setattr(api_module, "environment_report", fake_report)
    client = TestClient(api_module.create_app(tmp_path, start_workers=False))
    assert client.get("/v1/session").status_code == 200
    first = client.get("/v1/environment").json()
    assert first["items"]["ffmpeg"]["status"] == "missing" and first["checked_at"] == "t1"
    assert client.get("/v1/environment").json()["checked_at"] == "t1", "未要求重新檢查時回快取"
    second = client.post("/v1/environment/recheck").json()
    assert second["checked_at"] == "t2" and len(calls) == 2
    assert "secret" not in json.dumps(second)


def test_execute_installs_torch_last_and_repins_when_dependencies_downgrade_it(monkeypatch, tmp_path):
    """whisperx／pyannote 的依賴會把 torch 降成 CPU 版：其他套件先裝、torch 最後裝，裝完再比對一次並釘回。"""
    entries = lockfile.parse_lock(LOCK_TEXT)
    commands = []
    monkeypatch.setattr(bootstrap_run.deps, "run_pip", lambda argv, runner=None: commands.append(argv))
    listings = iter([{"torch": "2.8.0", "torchaudio": "2.8.0", "httpx": "0.28.1", "fastapi": "0.138.1", "faster-whisper": "1.2.1"},
                     {"torch": "2.11.0+cu128", "torchaudio": "2.11.0+cu128", "httpx": "0.28.1", "fastapi": "0.138.1", "faster-whisper": "1.2.1"}])
    monkeypatch.setattr(bootstrap_run.deps, "installed_packages", lambda python, runner=None: next(listings))
    state = _state(installed={"fastapi": "0.138.1"})
    plan = bootstrap_run.build_plan(entries, state)
    results = bootstrap_run.execute(plan, tmp_path, state, entries)
    assert "httpx==0.28.1" in commands[0] and not any(arg.startswith("torch==") for arg in commands[0])
    assert "torch==2.11.0+cu128" in commands[1]
    assert "torch==2.11.0+cu128" in commands[2] and "--index-url" in commands[2]
    assert len(commands) == 3
    pip_result = next(result for result in results if result["kind"] == "pip")
    assert pip_result["repinned"] == ["torch", "torchaudio"]


def test_environment_packages_mark_optional_groups_instead_of_missing():
    from app.studio import environment
    from bootstrap.lockfile import OPTIONAL_GROUPS
    assert OPTIONAL_GROUPS == ("engine_b", "v1")
    report = environment._packages(installed={"fastapi": "0.138.1"})
    by_name = {item["name"]: item for item in report["items"]}
    assert by_name["fastapi"]["status"] == "ok" and by_name["fastapi"]["optional"] is False
    assert by_name["httpx"]["status"] == "missing" and by_name["httpx"]["optional"] is False
    assert by_name["pywebview"]["status"] == "optional_missing" and by_name["pywebview"]["optional"] is True
    assert by_name["vibevoice"]["status"] == "optional_missing"
    assert environment._packages(installed={"pywebview": "6.2.1", "fastapi": "0.138.1"})["items"] and \
        {i["name"]: i["status"] for i in environment._packages(installed={"pywebview": "6.2.1"})["items"]}["pywebview"] == "ok"


def test_llama_server_installed_inside_the_project_is_found_without_touching_path(tmp_path):
    """2026-09-21 使用者：「llama.cpp 先擱置但可以先裝」→ 裝在專案 tools/llama.cpp/（不改系統 PATH）。
    環境檢查要找得到專案裡的執行檔；PATH 上有的仍優先。"""
    local = tmp_path / "tools" / "llama.cpp" / ("llama-server.exe" if sys.platform == "win32" else "llama-server")
    local.parent.mkdir(parents=True)
    local.write_bytes(b"exe")
    none = lambda name: None
    offline = lambda url, timeout=2: (None, None)
    item = checks.collect_env(runner=fake_runner({}), which=none, http_get=offline, python_version=(3, 12, 10),
                              project_root=tmp_path)["items"]["llama_server"]
    assert item["status"] == "ok" and item["details"]["path"] == str(local) and item["details"]["source"] == "project"
    assert item["details"]["running"] is False
    on_path = checks.collect_env(runner=fake_runner({}), which=lambda name: "C:/bin/llama-server.exe" if name == "llama-server" else None,
                                 http_get=offline, python_version=(3, 12, 10), project_root=tmp_path)["items"]["llama_server"]
    assert on_path["details"]["path"] == "C:/bin/llama-server.exe" and on_path["details"]["source"] == "path"
    empty = checks.collect_env(runner=fake_runner({}), which=none, http_get=offline, python_version=(3, 12, 10),
                               project_root=tmp_path / "nothing")["items"]["llama_server"]
    assert empty["status"] == "missing"
