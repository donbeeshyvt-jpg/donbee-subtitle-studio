"""專案 .venv 的建立與檢查（用系統 Python 建立；不動任何既有環境）。"""
import os
from pathlib import Path
import subprocess
import sys


def venv_python(root):
    root = Path(root)
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def venv_ok(root):
    python = venv_python(root)
    return python.is_file() and (Path(root) / ".venv" / "pyvenv.cfg").is_file()


def ensure_venv(root, base_python=None, runner=None):
    """不存在或損壞時以系統 Python 建立 .venv；回傳其 python 路徑。"""
    if venv_ok(root):
        return venv_python(root)
    base = str(base_python or sys.executable)
    argv = [base, "-m", "venv", str(Path(root) / ".venv")]
    if runner:
        runner(argv, timeout=600)
    else:
        subprocess.run(argv, check=True)
    if not venv_ok(root):
        raise RuntimeError("VENV_CREATE_FAILED")
    return venv_python(root)
