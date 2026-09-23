"""啟動流程：檢查與說明 → 確認 → 安裝 → 本機模型準備 → 重檢 → 服務健康檢查。

用法（專案根，系統 Python 3.12）：
  python -m bootstrap --interactive   檢查、詢問方案與安裝同意
  python -m bootstrap --check-only    只檢查、不安裝
  python -m bootstrap --local --confirm --serve  準備本機方案後啟動
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
from . import setup_support
from .process import run_visible

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GROUPS = ("core", "models")


def data_dir(root):
    override = os.environ.get("STUDIO_DATA_DIR")
    return Path(override) if override else Path(root) / "data"


def models_dir(root):
    override = os.environ.get("STUDIO_MODELS_DIR")
    value = Path(override) if override else Path('models')
    return value if value.is_absolute() else Path(root) / value


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
                downloader_ready=setup_support.downloader_ready(root),
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
            log("缺少模型：" + ", ".join(action["missing"]) + "（選 --local --confirm 準備必要本機模型；或透過 models download 選個別模型）")
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


def prepare_local(python, root):
    env = dict(os.environ, PYTHONPATH=str(Path(root) / 'src'), PYTHONUNBUFFERED='1')
    try:
        run_visible([str(python), '-m', 'bootstrap.local_setup', '--models-dir', str(models_dir(root).resolve()), '--confirm'],
                    cwd=str(root), env=env, timeout=14400, label='本機模型準備')
        return 0
    except (OSError, subprocess.SubprocessError):
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="冬比字幕工作室啟動自檢")
    parser.add_argument("--check-only", action="store_true", help="只檢查不安裝")
    parser.add_argument("--serve", action="store_true", help="補齊後啟動網頁服務")
    parser.add_argument("--groups", default=",".join(DEFAULT_GROUPS), help="要補齊的套件群組（逗號分隔）")
    parser.add_argument("--include-optional-models", action="store_true")
    parser.add_argument("--json", action="store_true", help="以 JSON 輸出報告")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument('--local', action='store_true', help='安裝 WhisperX 核心並準備 large-v3 與必要本機模型')
    parser.add_argument('--confirm', action='store_true', help='已同意套件安裝、版本調整與模型下載')
    parser.add_argument('--interactive', action='store_true', help='顯示安裝計畫並詢問本機方案與安裝授權')
    parser.add_argument('--install-tools', action='store_true', help='確認後用 winget 補裝 FFmpeg／Node')
    parser.add_argument('--estimate-download', action='store_true', help='只查官方模型檔案大小（需連網），未知大小保留 null')
    args, extra = parser.parse_known_args(argv)
    root = Path(args.root).resolve()
    if not (args.confirm or args.check_only or args.interactive):
        print('先以 --local --check-only 查看缺件；確認後加 --confirm，或使用 --interactive。', file=sys.stderr)
        return 2
    groups = tuple(part.strip() for part in args.groups.split(",") if part.strip())
    entries = lockfile.load_lock(root / "requirements.lock")
    state = collect_state(root, groups, args.include_optional_models)
    log = (lambda *parts: print(*parts, file=sys.stderr)) if args.json else print
    if args.interactive and not args.check_only and not args.confirm:
        if not sys.stdin.isatty():
            log('非互動終端請先 --check-only，再明確指定 --local --confirm（需要系統安裝另加 --install-tools）。')
            return 2
        choice = input('選擇：1 本機轉錄（WhisperX + large-v3 + 必要模型）；2 工作台與套件（略過模型權重）；0 取消 [1/2/0]：').strip()
        if choice not in ('1', '2'):
            return 2
        args.local = choice == '1'
        setup_support.describe(state, root, args.local, log)
        if args.local:
            for item in setup_support.estimate_models(root, state['models_missing']):
                log(item['id'] + '：' + ('約 %.2f GiB' % (item['download_bytes'] / 2**30) if item['download_bytes'] else '下載量未知'))
        if input('同意上述套件安裝／版本調整與所選模型下載？[y/N]：').strip().lower() != 'y':
            return 2
        args.confirm = True
        if state['tools_missing']:
            args.install_tools = input('另外同意透過 winget 安裝缺少的 FFmpeg／Node？[y/N]：').strip().lower() == 'y'
    elif args.local or args.install_tools:
        setup_support.describe(state, root, args.local, log)
    if args.estimate_download:
        state['download_estimates'] = setup_support.estimate_models(root, state['models_missing'])
        for item in state['download_estimates']:
            log(json.dumps(item, ensure_ascii=False))
    if not args.check_only:
        if args.local:
            base = models_dir(root).resolve()
            for name, target in [('HF_HUB_CACHE', base / 'hf'), ('TORCH_HOME', base / 'torch')]:
                if os.environ.get(name) and Path(os.environ[name]).resolve() != target:
                    log(name + ' 與模型目錄不一致；先確認設定，不進行安裝。')
                    return 2
        if not state['python_ok']:
            log(checks.GUIDANCE['python'])
            return 2
        if args.serve:
            # 不在服務仍執行時安裝或替換它正在使用的套件；不停止其他程序。
            import socket
            port = int(extra[extra.index('--port') + 1]) if '--port' in extra else 8765
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=1):
                    log(f'連接埠 {port} 已使用；請先確認既有服務，不進行安裝或啟動第二份。')
                    return 2
            except OSError:
                pass
        if state['tools_missing'] and args.install_tools and args.confirm:
            try:
                setup_support.install_tools(state['tools_missing'])
                state = collect_state(root, groups, args.include_optional_models)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                log('系統工具安裝失敗：' + type(error).__name__ + '；請依指引安裝後重開終端。')
                return 1
        if state['tools_missing'] and (args.local or args.serve):
            for name in state['tools_missing']:
                log(checks.GUIDANCE[name])
            return 2
        if args.local and 'models' not in groups:
            log('本機方案必須包含 --groups core,models。')
            return 2
    plan = build_plan(entries, state)
    try:
        results = execute(plan, root, state, entries, check_only=args.check_only, log=log)
        if not args.check_only and (args.local or args.serve):
            setup_support.prepare_downloader(root)
            refreshed = collect_state(root, groups, args.include_optional_models)
            if not refreshed['venv_ok'] or refreshed['tools_missing'] or deps.plan_installs(entries, refreshed['installed'], groups):
                log('安裝後重檢未通過；請檢查缺件與版本衝突，不啟動工作台。')
                return 1
            state = refreshed
            if args.local and prepare_local(state['venv_python'], root):
                return 1
            if args.local:
                state = collect_state(root, groups, args.include_optional_models)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        log('安裝失敗：' + type(error).__name__ + '；未啟動工作台。')
        return 1
    report = dict(checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), fast_path=plan["fast_path"], network_needed=plan["network_needed"],
                  environment=state["env"], venv_python=state["venv_python"], groups=list(groups), tools_missing=state["tools_missing"],
                  models_missing=state["models_missing"], actions=results, local_selected=args.local,
                  download_estimates=state.get('download_estimates'), downloader_ready=state.get('downloader_ready'))
    write_report(root, report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        log("快速路徑" if plan["fast_path"] else f"已處理 {len(results)} 項動作", "；環境報告寫入", str(data_dir(root) / "bootstrap-last.json"))
    if args.serve and not args.check_only:
        return serve(venv.venv_python(root), root, extra)
    return 0
