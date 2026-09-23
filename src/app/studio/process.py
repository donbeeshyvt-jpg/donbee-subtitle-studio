"""受控程序樹：Windows Job Object 與 POSIX process group。"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import signal
import subprocess
import time


class ManagedProcess:
    def __init__(self, argv: list[str], cwd: Path | str, log_path: Path,
                 env: dict[str, str] | None = None):
        self.log = log_path.open("ab", buffering=0)
        self.handle = None
        self.process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=self.log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=os.name != "nt")
        if os.name == "nt":
            self._attach_job()

    def _attach_job(self):
        # 作業物件關閉即終止仍存活的子孫程序，避免父程序退出後留下推論。
        from ctypes import wintypes
        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount",
                        "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", Counters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateJobObjectW(None, None)
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000
        if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            if handle:
                kernel.CloseHandle(handle)
            self.stop()
            raise OSError("無法建立受控程序容器")
        if not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(int(self.process._handle))):
            kernel.CloseHandle(handle)
            self.stop()
            raise OSError("無法將工作程序加入受控容器")
        self.handle, self.kernel = handle, kernel

    @property
    def pid(self):
        return self.process.pid

    def poll(self):
        return self.process.poll()

    def stop(self):
        import psutil
        try:
            children = psutil.Process(self.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
        if os.name == "nt":
            if self.handle:
                self.kernel.CloseHandle(self.handle)
                self.handle = None
            elif self.process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(self.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
        else:
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            self.process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=1)
        _, alive = psutil.wait_procs(children, timeout=4)
        for child in alive:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass

    def close(self):
        self.stop()
        self.log.close()

    def wait(self, timeout: float, cancelled=lambda: False):
        deadline = time.monotonic() + timeout
        try:
            while self.poll() is None:
                if cancelled():
                    raise InterruptedError("工作已取消")
                if time.monotonic() >= deadline:
                    raise TimeoutError("工作超過期限")
                time.sleep(0.05)
            return self.process.returncode
        finally:
            self.close()
