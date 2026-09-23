"""單服務排程：有界下載／CPU 並行與單 GPU 程序租用。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time

from .process import ManagedProcess
from .store import Store, StudioError


class Coordinator:
    def __init__(self, store: Store, python: str | None = None):
        self.store = store
        self.python = python or os.environ.get("STUDIO_MODEL_PYTHON", sys.executable)
        self.stopping = threading.Event()
        self.threads = []
        self.lock = None
        # 資源暫停條件：例如即時字幕（M7）進行中，GPU 工作先排隊，不搶顯示卡
        self.holds = {}

    def held(self, resource):
        check = self.holds.get(resource)
        return bool(check and check())

    def claim_next(self, resource):
        """暫停條件成立時不派工；否則取下一個排隊中的工作（狀態改 running）。"""
        if self.held(resource):
            return None
        return self.store.claim(resource)

    ALREADY_RUNNING = ("SERVICE_ALREADY_RUNNING",
                       "已有另一個冬比字幕工作室服務正在使用同一個資料目錄（data/coordinator.lock）。請改用已開啟的視窗，或先關閉它再啟動；"
                       "要同時跑兩個請用 STUDIO_DATA_DIR 指定不同的資料目錄。")

    @staticmethod
    def _acquire(handle):
        """對 lock 檔的第 1 個位元組取得非阻塞鎖；被占用時拋 OSError／PermissionError。"""
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            if not handle.read(1):
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @classmethod
    def available(cls, root):
        """同一資料目錄是否還沒有其他服務持有 coordinator 鎖（試鎖後立即釋放）。"""
        handle = (Path(root) / "coordinator.lock").open("a+b")
        try:
            cls._acquire(handle)
        except (OSError, PermissionError):
            return False
        finally:
            handle.close()
        return True

    def start(self):
        self.lock = (self.store.root / "coordinator.lock").open("a+b")
        try:
            self._acquire(self.lock)
        except (OSError, PermissionError):
            self.lock.close()
            self.lock = None
            raise StudioError(*self.ALREADY_RUNNING, 409) from None
        with self.store.connect() as db:
            rows = db.execute("SELECT id FROM jobs WHERE status IN ('running','cancelling')").fetchall()
        for row in rows:
            self.store.finish(row[0], "interrupted", error={"code": "SERVICE_RESTARTED", "message": "服務曾中斷，請由已完成成果重試"})
        for resource, count in (("download", 2), ("cpu", 2), ("gpu", 1), ("provider", 2), ("workflow", 2)):
            for index in range(count):
                thread = threading.Thread(target=self._loop, args=(resource,), daemon=True,
                                          name=f"studio-{resource}-{index}")
                thread.start()
                self.threads.append(thread)

    def close(self):
        self.stopping.set()
        for thread in self.threads:
            thread.join(timeout=6)
        if self.lock:
            self.lock.close()
            self.lock = None

    def _loop(self, resource):
        while not self.stopping.is_set():
            job = self.claim_next(resource)
            if job:
                self._execute(job)
            else:
                self.stopping.wait(.2)

    def _execute(self, job):
        job_id = job["id"]
        work = self.store.root / "jobs" / job_id
        work.mkdir(exist_ok=True)
        result_path = work / "result.json"
        gate = work / "start.ready"
        env = dict(os.environ)
        src = Path(__file__).resolve().parents[2]
        env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.setdefault("OMP_NUM_THREADS", "2")
        env.setdefault("MKL_NUM_THREADS", "2")
        downloader = src.parent / ".venv-download" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if downloader.exists():
            env.setdefault("STUDIO_YTDLP_PYTHON", str(downloader))
        process = None
        try:
            process = ManagedProcess([self.python, "-m", "app.studio.worker", "--root", str(self.store.root),
                                      "--job", job_id], src, work / "worker.log", env)
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET worker_pid=? WHERE id=?", (process.pid, job_id))
            # worker 等待此閘門，確保 Windows 程序容器已建立才載入模型或啟動子程序。
            gate.touch()
            heartbeat = 0
            while process.poll() is None:
                current = self.store.job(job_id)
                if current["status"] == "cancelling":
                    process.stop()
                    self.store.finish(job_id, "cancelled")
                    return
                if self.stopping.is_set():
                    process.stop()
                    self.store.finish(job_id, "interrupted", error={"code": "SERVICE_STOPPED", "message": "服務已停止"})
                    return
                now = time.time()
                if now >= job["deadline"]:
                    process.stop()
                    self.store.finish(job_id, "failed", error={"code": "DEADLINE_EXCEEDED", "message": "工作超過執行期限"})
                    return
                if now - heartbeat >= 2:
                    self.store.event(job_id, "stage.heartbeat", {"stage": current["stage"]})
                    heartbeat = now
                time.sleep(.1)
            if result_path.exists():
                outcome = json.loads(result_path.read_text(encoding="utf-8"))
                self.store.finish(job_id, "succeeded" if outcome.get("ok") and process.poll() == 0 else "failed",
                                  result=outcome.get("result"), error=outcome.get("error"))
            else:
                self.store.finish(job_id, "failed", error={"code": "WORKER_FAILED", "message": "工作程序異常結束", "exit_code": process.poll()})
        except Exception as exc:
            self.store.finish(job_id, "failed", error={"code": "SUPERVISOR_FAILED", "message": str(exc)})
        finally:
            if process:
                process.close()
