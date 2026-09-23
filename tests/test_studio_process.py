"""真程序樹 deadline／取消測試，不能只停止等待。"""
import sys
import time
import pytest
from app.studio.process import ManagedProcess


def test_deadline_terminates_child_tree(tmp_path):
    import psutil
    script = tmp_path / "hang.py"
    pidfile = tmp_path / "child.pid"
    script.write_text("import subprocess,sys,time,pathlib\n"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(p.pid))\ntime.sleep(60)\n")
    process = ManagedProcess([sys.executable, str(script), str(pidfile)], tmp_path, tmp_path / "log.txt")
    deadline = time.monotonic() + 5
    while not pidfile.exists() and time.monotonic() < deadline:
        time.sleep(.05)
    assert pidfile.exists()
    child = int(pidfile.read_text())
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        process.wait(.2)
    assert time.monotonic() - started < 5
    assert not psutil.pid_exists(process.pid)
    assert not psutil.pid_exists(child)


def test_cancel_and_success(tmp_path):
    process = ManagedProcess([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path, tmp_path / "cancel.log")
    with pytest.raises(InterruptedError):
        process.wait(10, cancelled=lambda: True)
    process = ManagedProcess([sys.executable, "-c", "print('complete')"], tmp_path, tmp_path / "success.log")
    assert process.wait(5) == 0
