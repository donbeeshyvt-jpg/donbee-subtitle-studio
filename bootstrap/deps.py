"""Python 套件依賴：列出已安裝、只補缺或修正版本、torch 依 GPU 選 wheel、組 pip 命令（不在此執行）。"""
import json
from pathlib import Path
import subprocess
import sys

from .lockfile import normalize_name

TORCH_FAMILY = {"torch", "torchaudio", "torchvision"}
TORCH_INDEX = {"cu128": "https://download.pytorch.org/whl/cu128", "cpu": "https://download.pytorch.org/whl/cpu"}


def base_version(version):
    """去掉 +cu128 之類的本地版本後綴，只比對基礎版本。"""
    return (version or "").split("+", 1)[0]


def plan_installs(entries, installed, groups):
    """回傳需要安裝的項目（缺少或版本不符），依鎖定清單順序；已符合者不列。"""
    normalized = {normalize_name(name): version for name, version in installed.items()}
    plan = []
    for entry in entries:
        if entry["group"] not in groups:
            continue
        current = normalized.get(entry["name"])
        if current is None:
            plan.append(dict(entry, reason="missing", installed=None))
        elif entry["version"] is not None and base_version(current) != entry["version"]:
            plan.append(dict(entry, reason="version_mismatch", installed=current))
    return plan


def select_torch_index(gpu_available):
    variant = "cu128" if gpu_available else "cpu"
    return variant, TORCH_INDEX[variant]


def pip_command(python, entries, torch_variant, project_root=None):
    """組 pip install 命令；torch 系列加後綴與 index-url，可編輯安裝以專案根解析相對路徑。"""
    argv = [str(python), "-m", "pip", "install", "--no-input"]
    if any(entry["name"] in TORCH_FAMILY for entry in entries):
        argv += ["--index-url", TORCH_INDEX[torch_variant]]
    for entry in entries:
        if entry.get("editable"):
            root = Path(project_root) if project_root else Path.cwd()
            argv += ["-e", str(root / entry["editable"])]
        elif entry["name"] in TORCH_FAMILY:
            argv.append(f"{entry['name']}=={entry['version']}+{torch_variant}")
        else:
            argv.append(entry["spec"])
    return argv


def installed_packages(python, runner=None):
    """在目標直譯器內列出已安裝套件與版本（子程序，避免污染啟動器本身）。"""
    code = ("import importlib.metadata as m, json;"
            "print(json.dumps({d.metadata['Name']: d.version for d in m.distributions() if d.metadata['Name']}))")
    run = runner or (lambda argv, timeout=120: subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=True).stdout)
    output = run([str(python), "-c", code], timeout=120)
    data = json.loads(output.strip().splitlines()[-1])
    return {normalize_name(name): version for name, version in data.items()}


def run_pip(argv, runner=None):
    """執行 pip；輸出導向 stderr（stdout 保留給 JSON 報告），失敗拋例外。"""
    if runner:
        return runner(argv, timeout=3600)
    subprocess.run(argv, check=True, stdout=sys.stderr)
    return ""
