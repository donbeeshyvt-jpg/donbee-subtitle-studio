"""安裝子程序：保留即時輸出、定時階段訊息；逾時或中斷只終止本次啟動的程序樹。"""
import os
import signal
import subprocess
import sys
import time


def run_visible(argv, *, cwd=None, env=None, timeout=3600, label='安裝'):
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=sys.stderr, stderr=sys.stderr,
                               start_new_session=os.name != 'nt')
    start = time.monotonic()
    try:
        while True:
            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            try:
                code = process.wait(timeout=min(5, remaining))
                break
            except subprocess.TimeoutExpired:
                print(f'[{label}] 執行中，已過 {int(time.monotonic() - start)} 秒', file=sys.stderr, flush=True)
        if code:
            raise subprocess.CalledProcessError(code, argv)
    except BaseException:
        if process.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=15)
            else:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        raise
    return code
