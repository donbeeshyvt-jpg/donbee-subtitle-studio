"""啟動流程：環境檢查 → .venv 與套件只補缺 → 模型比對 → JSON 回報；全部齊備時走快速路徑（不呼叫 pip、不連網）。

用法（專案根，系統 Python 3.12）：
  python -m bootstrap                 檢查並補齊套件（需要時建立 .venv）
  python -m bootstrap --check-only    只檢查、不安裝
  python -m bootstrap --serve         補齊後以 .venv 啟動網頁服務
  python -m bootstrap --groups core,models,dev --json
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from . import checks, deps, lockfile, models, venv

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GROUPS = ("core", "models", "dev")


def data_dir(root):
    override = os.environ.get("STUDIO_DATA_DIR")
    return Path(override) if override else Path(root) / "data"


def models_dir(root):
    override = os.environ.get("STUDIO_MODELS_DIR")
    return Path(override) if override else Path(root) / "models"


def build_plan(entries, state):
    """純函式：依狀態列出動作；環境類軟體只給指引（guide），不安裝系統軟體。"""
    actions = []
    if not state.get("python_ok", True):
        actions.append(dict(kind="guide", tool="python", guidance=checks.GUIDANCE["python"]))
    if not state.get("venv_ok"):
        actions.append(dict(kind="create_venv"))
    # 已安裝清單由 collect_state 提供（.venv 不存在時為空），這裡只依狀態計算，不再猜測
    pending = deps.plan_installs(entries, state.get("installed", {}), state.get("groups", DEFAULT_GROUPS))
    if pending:
        variant, _ = deps.select_torch_index(state.get("gpu_available", False))
        actions.append(dict(kind="pip", items=pending, torch_variant=variant))
    for tool in state.get("tools_missing", []):
        actions.append(dict(kind="guide", tool=tool, guidance=checks.GUIDANCE.get(tool, "")))
    if state.get("models_missing"):
        actions.append(dict(kind="models", missing=list(state["models_missing"])))
    network = any(action["kind"] in ("pip", "models") for action in actions)
    return dict(actions=actions, network_needed=network, fast_path=not actions)


def collect_state(root, groups, include_optional_models=False):
    env = checks.collect_env()
    python_ok = env["items"]["python"]["status"] == "ok"
    ok = venv.venv_ok(root)
    python = venv.venv_python(root)
    installed = deps.installed_packages(python) if ok else {}
    tools_missing = [name for name in ("ffmpeg", "ffprobe", "node") if env["items"][name]["status"] != "ok"]
    manifest_path = Path(root) / "models.manifest.json"
    missing = models.missing_models(models.load_manifest(manifest_path), models_dir(root), include_optional_models) if manifest_path.is_file() else []
    return dict(env=env, python_ok=python_ok, venv_ok=ok, venv_python=str(python), installed=installed, tools_missing=tools_missing,
                models_missing=missing, gpu_available=env["items"]["gpu"]["status"] == "ok", groups=groups)


def torch_drift(entries, installed, groups):
    """安裝後若 torch 系列被其他套件的依賴改版（例如降成 CPU wheel），回傳需釘回的鎖定項目。"""
    return [item for item in deps.plan_installs(entries, installed, groups) if item["name"] in deps.TORCH_FAMILY]


def repin_torch(python, root, entries, state, torch_variant, log=print):
    """比對安裝結果，torch 系列不符鎖定版本時用 GPU／CPU 對應 index 再裝一次；回傳釘回的套件名。"""
    drift = torch_drift(entries, deps.installed_packages(python), state["groups"])
    if not drift:
        return []
    pinned = [entry for entry in entries if entry["name"] in deps.TORCH_FAMILY and entry["group"] in state["groups"]]
    argv = deps.pip_command(python, pinned, torch_variant, project_root=root)
    log("torch 系列被其他套件改版，釘回鎖定版本：" + " ".join(argv[5:]))
    deps.run_pip(argv)
    return [item["name"] for item in drift]


def execute(plan, root, state, entries, check_only=False, log=print):
    """依計畫執行：建 .venv、pip 只補缺；指引只印出；模型缺項只回報（下載於 M0-P3）。"""
    results = []
    python = Path(state["venv_python"])
    for action in plan["actions"]:
        if action["kind"] == "create_venv":
            if check_only:
                results.append(dict(action, status="skipped_check_only"))
                continue
            python = venv.ensure_venv(root)
            state["venv_ok"] = True
            results.append(dict(action, status="created", python=str(python)))
            installed = deps.installed_packages(python)
            pending = deps.plan_installs(entries, installed, state["groups"])
            if pending and not any(a["kind"] == "pip" for a in plan["actions"]):
                plan["actions"].append(dict(kind="pip", items=pending, torch_variant=deps.select_torch_index(state["gpu_available"])[0]))
        elif action["kind"] == "pip":
            if check_only:
                results.append(dict(action, status="skipped_check_only"))
                continue
            torch_items = [item for item in action["items"] if item["name"] in deps.TORCH_FAMILY]
            other_items = [item for item in action["items"] if item["name"] not in deps.TORCH_FAMILY]
            # whisperx／pyannote 的依賴可能把 torch 換成 CPU 版：先裝其他，torch 系列最後裝，裝完再比對一次釘回。
            for batch in (other_items, torch_items):
                if not batch:
                    continue
                argv = deps.pip_command(python, batch, action["torch_variant"], project_root=root)
                log("安裝套件：" + " ".join(argv[5:]))
                deps.run_pip(argv)
            repinned = repin_torch(python, root, entries, state, action["torch_variant"], log)
            results.append(dict(kind="pip", status="installed", names=[item["name"] for item in action["items"]], repinned=repinned))
        elif action["kind"] == "guide":
            log(f"[{action['tool']}] {action['guidance']}")
            results.append(dict(action, status="guided"))
        elif action["kind"] == "models":
            log("缺少模型：" + ", ".join(action["missing"]) + "（M0-P3 起可自動下載；目前請執行 scripts/import-local-models.py 或手動放入 models/）")
            results.append(dict(action, status="reported"))
    return results


def write_report(root, report):
    folder = data_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "bootstrap-last.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


def serve(python, root, extra_args):
    env = dict(os.environ, PYTHONPATH=str(Path(root) / "src"), STUDIO_MODEL_PYTHON=str(python), PYTHONDONTWRITEBYTECODE="1")
    downloader = Path(root) / ".venv-download" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if downloader.is_file():
        env.setdefault("STUDIO_YTDLP_PYTHON", str(downloader))
    argv = [str(python), "-m", "app", "serve", "--host", "127.0.0.1", "--port", "8765", "--open-browser", *extra_args]
    return subprocess.call(argv, cwd=str(root), env=env)


def main(argv=None):
    parser = argparse.ArgumentParser(description="冬比字幕工作室啟動自檢")
    parser.add_argument("--check-only", action="store_true", help="只檢查不安裝")
    parser.add_argument("--serve", action="store_true", help="補齊後啟動網頁服務")
    parser.add_argument("--groups", default=",".join(DEFAULT_GROUPS), help="要補齊的套件群組（逗號分隔）")
    parser.add_argument("--include-optional-models", action="store_true")
    parser.add_argument("--json", action="store_true", help="以 JSON 輸出報告")
    parser.add_argument("--root", default=str(ROOT))
    args, extra = parser.parse_known_args(argv)
    root = Path(args.root)
    groups = tuple(part.strip() for part in args.groups.split(",") if part.strip())
    entries = lockfile.load_lock(root / "requirements.lock")
    state = collect_state(root, groups, args.include_optional_models)
    plan = build_plan(entries, state)
    log = (lambda *parts: print(*parts, file=sys.stderr)) if args.json else print
    results = execute(plan, root, state, entries, check_only=args.check_only, log=log)
    report = dict(checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), fast_path=plan["fast_path"], network_needed=plan["network_needed"],
                  environment=state["env"], venv_python=state["venv_python"], groups=list(groups), tools_missing=state["tools_missing"],
                  models_missing=state["models_missing"], actions=results)
    write_report(root, report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        log("快速路徑" if plan["fast_path"] else f"已處理 {len(results)} 項動作", "；環境報告寫入", str(data_dir(root) / "bootstrap-last.json"))
    if args.serve and not args.check_only:
        return serve(venv.venv_python(root), root, extra)
    return 0
