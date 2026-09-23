"""M5-1：故障矩陣 F01–F07（docs/TEST_PLAN_V2.md 第 3 節）。

真程序、真 SQLite、真檔案；模型一律不載入。掛起的工作程序用一個真的 `python -c "time.sleep(...)"` 代替
（`ManagedProcess` 仍是真的，殺程序樹與等待都照實走），這樣才能在沒有 GPU、沒有模型的機器上重現掛起。
"""
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.studio import coordinator as coordinator_module
from app.studio.api import create_app
from app.studio.coordinator import Coordinator
from app.studio.process import ManagedProcess
from app.studio.store import Store, StudioError

HANG = "import time; time.sleep(120)"


def fake_workers(monkeypatch, script=HANG, pids=None):
    """把協調器要跑的 worker 換成一個真的程序（預設是掛住不動），回傳被啟動的 pid 清單。"""
    started = pids if pids is not None else []
    real = coordinator_module.ManagedProcess

    def spawn(argv, cwd, log_path, env=None):
        process = real([sys.executable, "-c", script], cwd, log_path, env)
        started.append(process.pid)
        return process

    monkeypatch.setattr(coordinator_module, "ManagedProcess", spawn)
    return started


def wait_terminal(store, job_id, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = store.job(job_id)
        if job["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
            return job
        time.sleep(.05)
    return store.job(job_id)


# ---- F01：掛起的工作在期限內被整棵樹終止，隊列繼續運作 ----

def test_f01_hanging_job_hits_the_deadline_within_five_seconds_and_the_queue_keeps_running(tmp_path, monkeypatch):
    import psutil
    store = Store(tmp_path / "state")
    project = store.create("project", {"name": "F01"})["id"]
    pids = fake_workers(monkeypatch)
    hang = store.submit(project, {"kind": "probe", "source_id": "s", "timeout_sec": 1}, "gpu")
    coordinator = Coordinator(store)
    coordinator.start()
    try:
        started = time.monotonic()
        finished = wait_terminal(store, hang["id"])
        elapsed = time.monotonic() - started
        assert finished["status"] == "failed" and finished["error"]["code"] == "DEADLINE_EXCEEDED"
        # 規格（TEST_PLAN F01）：期限到之後 ≤5 秒終止程序樹。從期限那一刻算，不含工作本身的 1 秒期限與啟動程序的時間
        # （2026-09-21 全套一起跑時，從協調器啟動算到結束是 5.06 秒，把 1 秒期限也算進去了）
        after_deadline = finished["updated_at"] - finished["deadline"]
        print(f"F01 期限後終止耗時 {after_deadline:.2f} 秒；從啟動算 {elapsed:.2f} 秒")
        assert after_deadline < 5
        assert finished["worker_pid"] is None
        assert pids and not psutil.pid_exists(pids[0])  # 受管理的程序樹已經不在
        # GPU 讓出來了：下一個工作照樣派得出去
        following = store.submit(project, {"kind": "probe", "source_id": "s2", "timeout_sec": 1}, "gpu")
        assert wait_terminal(store, following["id"])["status"] in {"failed", "cancelled"}
        assert len(pids) == 2
    finally:
        coordinator.close()


# ---- F02：成功與取消同時到達 ----

def test_f02_cancel_wins_the_race_and_a_finished_job_keeps_its_artifact(tmp_path):
    store = Store(tmp_path / "state")
    project = store.create("project", {"name": "F02"})["id"]
    racing = store.submit(project, {"kind": "probe", "source_id": "s"}, "cpu")
    assert store.claim("cpu")["id"] == racing["id"]
    store.cancel(racing["id"])  # 使用者按取消的同時，工作程序剛好寫完結果
    assert store.finish(racing["id"], "succeeded", result={"ok": True}) is True
    settled = store.job(racing["id"])
    assert settled["status"] == "cancelled" and settled["result"] is None  # 終態唯一，不發布取消的半成品
    assert store.finish(racing["id"], "failed", error={"code": "LATE"}) is False
    assert store.job(racing["id"])["status"] == "cancelled"

    done = store.submit(project, {"kind": "probe", "source_id": "s2"}, "cpu")
    store.claim("cpu")
    artifact = store.publish(project, done["id"], "subtitle", "1\n".encode("utf-8"), "srt")
    store.finish(done["id"], "succeeded", result={"artifact_id": artifact["id"]})
    # 已結束的工作再按取消：維持原終態（取消是冪等的，不會回頭改狀態），成果也不刪
    assert store.cancel(done["id"])["status"] == "succeeded"
    assert store.job(done["id"])["result"] == {"artifact_id": artifact["id"]}
    assert store.artifact_path(artifact["id"]).is_file()


# ---- F03：工作程序異常退出有診斷碼；重試有上限 ----

def test_f03_worker_crash_reports_a_diagnosis_and_retry_is_bounded(tmp_path, monkeypatch):
    store = Store(tmp_path / "state")
    project = store.create("project", {"name": "F03"})["id"]
    fake_workers(monkeypatch, script="import sys; sys.exit(3)")
    crashed = store.submit(project, {"kind": "probe", "source_id": "s"}, "cpu")
    coordinator = Coordinator(store)
    coordinator.start()
    try:
        job = wait_terminal(store, crashed["id"])
    finally:
        coordinator.close()
    assert job["status"] == "failed"
    assert job["error"]["code"] == "WORKER_FAILED" and job["error"]["exit_code"] == 3

    app = create_app(tmp_path / "state", start_workers=False)
    client = TestClient(app)
    assert client.get("/v1/session").status_code == 200
    job_id, attempt = crashed["id"], 1
    while attempt < Store.MAX_ATTEMPTS:
        response = client.post(f"/v1/jobs/{job_id}/retry")
        assert response.status_code == 202
        attempt = response.json()["attempt"]
        job_id = response.json()["job_id"]
        store.finish(job_id, "failed", error={"code": "WORKER_FAILED"})
    assert attempt == Store.MAX_ATTEMPTS
    refused = client.post(f"/v1/jobs/{job_id}/retry")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "JOB_RETRY_LIMIT"


# ---- F04：單 GPU 互斥、CPU 有界並行 ----

def test_f04_one_gpu_job_at_a_time_while_cpu_runs_two(tmp_path, monkeypatch):
    store = Store(tmp_path / "state")
    project = store.create("project", {"name": "F04"})["id"]
    fake_workers(monkeypatch)
    gpu = [store.submit(project, {"kind": "probe", "source_id": f"g{i}", "timeout_sec": 60}, "gpu")["id"] for i in range(3)]
    cpu = [store.submit(project, {"kind": "probe", "source_id": f"c{i}", "timeout_sec": 60}, "cpu")["id"] for i in range(3)]
    coordinator = Coordinator(store)
    coordinator.start()
    try:
        def running(ids):
            return [i for i in ids if store.job(i)["status"] == "running"]
        deadline = time.monotonic() + 8
        peak_cpu = 0
        while time.monotonic() < deadline:
            assert len(running(gpu)) <= 1  # 重型工作互斥：同一時刻只有一個
            peak_cpu = max(peak_cpu, len(running(cpu)))
            time.sleep(.1)
        assert peak_cpu == 2  # CPU 有界並行：剛好兩條，不是全部一起跑
        # 即時字幕占用顯示卡時（M7 hold），GPU 工作排隊不搶
        coordinator.holds["gpu"] = lambda: True
        assert coordinator.claim_next("gpu") is None
    finally:
        coordinator.holds.clear()
        coordinator.close()


# ---- F05：磁碟不足、SQLite busy、發布中崩潰 ----

def test_f05_disk_full_is_reported_without_touching_the_original(tmp_path, monkeypatch):
    from app.studio import destinations
    store = Store(tmp_path / "state")
    project = store.create("project", {"name": "F05"})["id"]
    job = store.submit(project, {"kind": "probe", "source_id": "s"}, "cpu")
    store.claim("cpu")
    artifact = store.publish(project, job["id"], "subtitle", "1\n字幕\n".encode("utf-8"), "srt")
    source = store.artifact_path(artifact["id"])
    folder = tmp_path / "out"
    folder.mkdir()
    config = {"roots": {"chosen": str(folder)}}
    monkeypatch.setattr(destinations.shutil, "disk_usage", lambda _: shutil._ntuple_diskusage(1, 1, 0))
    with pytest.raises(StudioError) as failure:
        destinations.deliver_file(source, config, "chosen", "project_1", "job_1", source.name)
    assert failure.value.code == "DISK_FULL" and failure.value.status == 507
    assert source.read_bytes() == "1\n字幕\n".encode("utf-8")  # 原稿不動
    assert list((folder / "subtitle_studio").iterdir()) == []  # 不留半份檔案


def test_f05_concurrent_writers_do_not_hit_sqlite_busy(tmp_path):
    store = Store(tmp_path / "state")
    project = store.create("project", {"name": "F05-busy"})["id"]
    errors, made = [], []

    def writer(index):
        try:
            for step in range(10):
                made.append(store.submit(project, {"kind": "probe", "source_id": f"s{index}-{step}"}, "cpu")["id"])
        except sqlite3.OperationalError as error:  # 「database is locked」就是失敗
            errors.append(str(error))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert errors == []
    assert len(set(made)) == 40


def test_f05_a_crash_during_publish_serves_nothing_and_leaves_the_original(tmp_path):
    store = Store(tmp_path / "state")
    project = store.create("project", {"name": "F05-crash"})["id"]
    job = store.submit(project, {"kind": "probe", "source_id": "s"}, "cpu")
    store.claim("cpu")
    good = store.publish(project, job["id"], "subtitle", "好的成果".encode("utf-8"), "srt")
    # 發布中崩潰＝寫完暫存檔就斷電，沒有 rename、沒有資料列
    (store.root / "artifacts" / "artifact_halfway.srt.staging").write_bytes("半份".encode("utf-8"))
    app = create_app(tmp_path / "state", start_workers=False)
    client = TestClient(app)
    assert client.get("/v1/session").status_code == 200  # 取得連線憑證，才是真的在測 404 而不是 401
    assert client.get("/v1/artifacts/artifact_halfway/content").status_code == 404
    assert client.get(f"/v1/artifacts/{good['id']}/content").content == "好的成果".encode("utf-8")
    assert [a["artifact_id"] for a in store.list("artifact", project)["items"]] == [good["id"]]


# ---- F06：清理與匯出同時跑 ----

def test_f06_cleanup_keeps_artifacts_in_use_and_running_jobs(tmp_path):
    store = Store(tmp_path / "state")
    project = store.create("project", {"name": "F06"})["id"]
    done = store.submit(project, {"kind": "probe", "source_id": "s"}, "cpu")
    store.claim("cpu")
    keep = store.publish(project, done["id"], "subtitle", "要保留".encode("utf-8"), "srt")
    store.finish(done["id"], "succeeded", result={"artifact_id": keep["id"]})
    finished_work = store.root / "jobs" / done["id"]
    finished_work.mkdir(parents=True, exist_ok=True)
    (finished_work / "worker.log").write_bytes(b"log")
    # 正在匯出的工作：工作目錄不能被清掉
    exporting = store.submit(project, {"kind": "export", "source_id": "s"}, "cpu")
    store.claim("cpu")
    running_work = store.root / "jobs" / exporting["id"]
    running_work.mkdir(parents=True, exist_ok=True)
    (running_work / "result.json.part").write_bytes(b"writing")
    orphan = store.root / "artifacts" / "artifact_nobody.srt"
    orphan.write_bytes("沒有紀錄的孤兒".encode("utf-8"))
    stale = store.root / "artifacts" / "artifact_halfway.srt.staging"
    stale.write_bytes("半份".encode("utf-8"))
    old_enough = time.time() - Store.STALE_STAGING_SEC - 60  # 20 分鐘前崩潰留下的，不是正在寫的
    os.utime(stale, (old_enough, old_enough))
    uploads = store.root.parent / "uploads"
    uploads.mkdir(exist_ok=True)
    original = uploads / "使用者原始素材.wav"
    original.write_bytes(b"RIFF")

    writing = store.root / "artifacts" / "artifact_publishing.srt.staging"
    writing.write_bytes("正在發布".encode("utf-8"))  # 剛寫下的暫存＝可能正在發布，不能清

    report = store.cleanup()
    assert writing.is_file()
    assert store.artifact_path(keep["id"]).is_file()  # 使用中的成果留著
    assert running_work.is_dir()  # 正在跑的工作目錄留著
    assert original.is_file()  # 使用者原始素材不碰
    assert not finished_work.exists() and not orphan.exists() and not stale.exists()
    assert report["removed_job_dirs"] == 1 and report["removed_artifacts"] == 1 and report["removed_staging"] == 1
    assert report["freed_bytes"] > 0 and report["kept_artifacts"] == 1

    app = create_app(tmp_path / "state", start_workers=False)
    client = TestClient(app)
    assert client.get("/v1/session").status_code == 200
    preview = client.post("/v1/maintenance/cleanup", json={"dry_run": True})
    assert preview.status_code == 200 and preview.json()["removed_job_dirs"] == 0  # 已經清過，沒東西可清


# ---- F07：aging 與批次上限 ----

def test_f07_a_long_waiting_job_wins_over_newer_higher_priority_work(tmp_path):
    store = Store(tmp_path / "state")
    project = store.create("project", {"name": "F07"})["id"]
    waiting = store.submit(project, {"kind": "probe", "source_id": "old"}, "cpu")
    with store.connect() as db:  # 已經等了 40 分鐘
        db.execute("UPDATE jobs SET created_at=? WHERE id=?", (time.time() - 2400, waiting["id"]))
    for index in range(3):
        newcomer = store.submit(project, {"kind": "probe", "source_id": f"new{index}"}, "cpu")
        store.control(newcomer["id"], priority=100)
    assert store.claim("cpu")["id"] == waiting["id"]  # aging 生效，等久的先跑
    assert Store.MAX_WAIT_SEC == 3000  # 宣告的最大等待：priority 0 的工作等 3000 秒就贏過 priority 100 的新工作


def test_f07_batch_sizes_and_audio_length_are_capped(tmp_path):
    from app.studio.contracts import JobRequest, SequenceRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        JobRequest(kind="analyze", source_id="s", ranges=[{"start_us": 0, "end_us": 1}] * 101)
    with pytest.raises(ValidationError):
        SequenceRequest(items=[{"source_id": "s", "start_us": 0, "end_us": 1}] * 10001)
    with pytest.raises(ValidationError):
        JobRequest(kind="analyze", source_id="s", timeout_sec=10**9)


# ---- M5-3：單 GPU 互斥與有界並行的實際 PID／租用時間線 ----

def test_m5_3_timeline_records_pids_and_leases_for_gpu_cpu_and_download(tmp_path, monkeypatch):
    """每個工作都要查得到 worker PID 與租用起訖；GPU 的租用區間彼此不重疊，CPU／下載各自最多兩個同時。"""
    store = Store(tmp_path / "state")
    project = store.create("project", {"name": "M5-3"})["id"]
    pids = fake_workers(monkeypatch, script="import time; time.sleep(1.2)")
    wanted = {"gpu": 3, "cpu": 3, "download": 3}
    submitted = {resource: [store.submit(project, {"kind": "probe", "source_id": f"{resource}{i}", "timeout_sec": 30}, resource)["id"]
                            for i in range(count)] for resource, count in wanted.items()}
    seen = {job_id: {} for ids in submitted.values() for job_id in ids}
    coordinator = Coordinator(store)
    coordinator.start()
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            for job_id, record in seen.items():
                job = store.job(job_id)
                if job["worker_pid"]:
                    record["pid"] = job["worker_pid"]  # 派工中才看得到 PID（結束會清掉）
                record["status"] = job["status"]
            if all(record["status"] not in {"queued", "running", "cancelling"} for record in seen.values()):
                break
            time.sleep(.02)
    finally:
        coordinator.close()
    # 租用區間用服務自己記的事件時間（job.started → 終態），不受輪詢延遲影響
    for job_id, record in seen.items():
        events = store.events(job_id, 0)
        starts = [e["created_at"] for e in events if e["type"] == "job.started"]
        ends = [e["created_at"] for e in events if e["type"].startswith("job.") and e["type"] != "job.started"
                and e["type"].split(".")[1] in {"succeeded", "failed", "cancelled", "interrupted"}]
        if starts and ends:
            record["start"], record["end"] = starts[0], ends[-1]
    assert all("end" in record for record in seen.values()), "工作沒有全部結束"
    assert all(record.get("pid") for record in seen.values()), "每個工作都要記到 worker PID"
    assert len({record["pid"] for record in seen.values()}) == len(seen), "每個工作是各自的程序"

    def overlaps(ids):
        """同一時刻最多幾個在跑：掃描線（進場 +1、離場 -1），不是「與某一段重疊的段數」。"""
        marks = sorted([(seen[i]["start"], 1) for i in ids] + [(seen[i]["end"], -1) for i in ids])
        peak = running = 0
        for _, delta in marks:
            running += delta
            peak = max(peak, running)
        return peak

    assert overlaps(submitted["gpu"]) == 1, "單 GPU：租用區間不可重疊"
    assert overlaps(submitted["cpu"]) <= 2 and overlaps(submitted["download"]) <= 2, "CPU／下載有界並行（各 2）"
    assert overlaps(submitted["cpu"]) == 2, "CPU 應該真的並行兩個，不是排成一條"
