"""SQLite 交易、不可變版本、冪等工作與原子成果發布。"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import shutil
import time
from typing import Any

from app import config as app_config

from .fsutil import open_with_retry, replace_with_retry
from .domain import new_id


class StudioError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details=None):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def default_root() -> Path:
    # 路徑單一來源：預設專案內 data/，STUDIO_DATA_DIR 可覆寫（呼叫時解析，讓測試與啟動器改環境變數立即生效）
    return Path(app_config.resolve_data_dir())


class Store:
    # 重試上限（F03）：同一件事失敗到第 5 次就不再排隊，請先看錯誤訊息處理原因，避免無限重試迴圈
    MAX_ATTEMPTS = 5
    # 宣告的最大等待（F07）：claim 的 aging 是 priority + 等待秒數/30，
    # 所以 priority 0 的工作等滿 3000 秒（50 分鐘）就一定贏過 priority 100 的新工作
    MAX_WAIT_SEC = 3000

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else default_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "studio.sqlite3"
        (self.root / "artifacts").mkdir(exist_ok=True)
        (self.root / "jobs").mkdir(exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO metadata VALUES ('schema_version','1');
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, project_id TEXT,
                    body TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS entity_scope ON entities(kind, project_id, created_at);
                CREATE TABLE IF NOT EXISTS heads (
                    scope TEXT PRIMARY KEY, revision TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL,
                    body TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
                    resource TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 50,
                    dispatch_paused INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                    updated_at REAL NOT NULL, deadline REAL, attempt INTEGER NOT NULL DEFAULT 1,
                    parent_id TEXT, result TEXT, error TEXT, worker_pid INTEGER);
                CREATE INDEX IF NOT EXISTS job_queue ON jobs(status, resource, priority);
                CREATE TABLE IF NOT EXISTS idempotency (
                    scope TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL,
                    result_id TEXT NOT NULL, PRIMARY KEY(scope,key));
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    type TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS job_events ON events(job_id,id);
            """)
            version = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]
            if version != "1":
                raise StudioError("SCHEMA_VERSION_UNSUPPORTED", "資料庫版本不受支援", 409)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, kind: str, body: dict, project_id: str | None = None, entity_id: str | None = None):
        entity_id = entity_id or new_id(kind)
        result = {**body, "id": entity_id}
        with self.connect() as db:
            db.execute("INSERT INTO entities VALUES(?,?,?,?,?)",
                       (entity_id, kind, project_id, canonical(result), time.time()))
        return result

    def get(self, entity_id: str, kind: str | None = None):
        with self.connect() as db:
            row = db.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
        if row is None or (kind and row["kind"] != kind):
            raise StudioError("NOT_FOUND", "找不到指定資料", 404)
        return json.loads(row["body"])

    def list(self, kind: str, project_id: str | None = None, limit: int = 50, cursor: int = 0):
        if not 1 <= limit <= 200 or cursor < 0:
            raise StudioError("INVALID_REQUEST", "分頁引數無效", 422)
        query, args = "SELECT body FROM entities WHERE kind=?", [kind]
        if project_id is not None:
            query += " AND project_id=?"
            args.append(project_id)
        query += " ORDER BY created_at,id LIMIT ? OFFSET ?"
        with self.connect() as db:
            rows = db.execute(query, [*args, limit + 1, cursor]).fetchall()
        return {"items": [json.loads(r["body"]) for r in rows[:limit]],
                "next_cursor": cursor + limit if len(rows) > limit else None}

    def head(self, scope: str, initial="seq_00"):
        with self.connect() as db:
            row = db.execute("SELECT revision FROM heads WHERE scope=?", (scope,)).fetchone()
        return row[0] if row else initial

    def revise(self, scope: str, kind: str, body: dict, base_revision: str,
               project_id: str, initial="seq_00"):
        revision = new_id(kind)
        result = {**body, "id": revision, "revision": revision, "parent_id": base_revision}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM heads WHERE scope=?", (scope,)).fetchone()
            current = row[0] if row else initial
            if current != base_revision:
                raise StudioError("REVISION_CONFLICT", "資料已更新，請重新載入後合併修改", 409,
                                  {"current_revision": current})
            db.execute("INSERT INTO entities VALUES(?,?,?,?,?)",
                       (revision, kind, project_id, canonical(result), time.time()))
            db.execute("INSERT INTO heads VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET revision=excluded.revision",
                       (scope, revision))
        return result

    def submit(self, project_id: str, body: dict, resource="cpu", key: str | None = None,
               parent_id: str | None = None, attempt=1):
        self.get(project_id, "project")
        encoded = canonical(body)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        job_id, now = new_id("job"), time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if key:
                old = db.execute("SELECT * FROM idempotency WHERE scope=? AND key=?", (project_id, key)).fetchone()
                if old:
                    if old["digest"] != digest:
                        raise StudioError("IDEMPOTENCY_CONFLICT", "同一識別碼不可提交不同內容", 409)
                    return self.job(old["result_id"])
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0] >= 500:
                raise StudioError("QUEUE_FULL", "工作佇列已滿", 429)
            db.execute("""INSERT INTO jobs(id,project_id,kind,body,status,stage,resource,created_at,
                          updated_at,parent_id,attempt) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                       (job_id, project_id, body["kind"], encoded, "queued", "queued", resource, now, now, parent_id, attempt))
            if parent_id:
                parent = db.execute("SELECT priority,dispatch_paused,status FROM jobs WHERE id=?", (parent_id,)).fetchone()
                if parent:
                    db.execute("UPDATE jobs SET priority=?,dispatch_paused=? WHERE id=?", (parent[0], parent[1], job_id))
                    if parent[2] in {"cancelling", "cancelled", "interrupted", "failed"}:
                        db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job_id,))
            if key:
                db.execute("INSERT INTO idempotency VALUES(?,?,?,?)", (project_id, key, digest, job_id))
            self._event(db, job_id, "job.queued", {})
        return self.job(job_id)

    def job(self, job_id: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise StudioError("NOT_FOUND", "找不到工作", 404)
        result = dict(row)
        for name in ("body", "result", "error"):
            result[name] = json.loads(result[name]) if result[name] else None
        result["job_id"] = result["id"]
        result["dispatch_paused"] = bool(result["dispatch_paused"])
        return result

    def jobs(self, project_id: str, limit=50, cursor=0):
        with self.connect() as db:
            rows = db.execute("SELECT id FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                              (project_id, limit + 1, cursor)).fetchall()
        return {"items": [self.job(r[0]) for r in rows[:limit]],
                "next_cursor": cursor + limit if len(rows) > limit else None}

    @staticmethod
    def _event(db, job_id, event_type, payload):
        db.execute("INSERT INTO events(job_id,type,payload,created_at) VALUES(?,?,?,?)",
                   (job_id, event_type, canonical(payload), time.time()))

    def event(self, job_id, event_type, payload):
        with self.connect() as db:
            self._event(db, job_id, event_type, payload)

    def events(self, job_id, after=0, limit=200):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM events WHERE job_id=? AND id>? ORDER BY id LIMIT ?",
                              (job_id, after, limit)).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def claim(self, resource):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT id,body FROM jobs WHERE status='queued' AND resource=? AND dispatch_paused=0
                              ORDER BY priority + ((? - created_at)/30.0) DESC, created_at LIMIT 1""",
                             (resource, time.time())).fetchone()
            if row is None:
                return None
            body = json.loads(row["body"])
            now = time.time()
            db.execute("UPDATE jobs SET status='running',stage='starting',updated_at=?,deadline=? WHERE id=?",
                       (now, now + body.get("timeout_sec", 1800), row["id"]))
            self._event(db, row["id"], "job.started", {})
        return self.job(row["id"])

    def finish(self, job_id, status, result=None, error=None):
        if status not in {"succeeded", "failed", "cancelled", "interrupted"}:
            raise ValueError("非法終態")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row[0] not in {"running", "cancelling", "queued"}:
                return False
            if row[0] == "cancelling":
                status, result = "cancelled", None
            db.execute("UPDATE jobs SET status=?,stage=?,result=?,error=?,updated_at=?,worker_pid=NULL WHERE id=?",
                       (status, status, canonical(result), canonical(error), time.time(), job_id))
            self._event(db, job_id, f"job.{status}", {"result": result, "error": error})
        return True

    def cancel(self, job_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise StudioError("NOT_FOUND", "找不到工作", 404)
            if row[0] in {"queued", "running"}:
                state = "cancelled" if row[0] == "queued" else "cancelling"
                db.execute("UPDATE jobs SET status=?,updated_at=? WHERE id=?", (state, time.time(), job_id))
                self._event(db, job_id, f"job.{state}", {})
                children = db.execute("WITH RECURSIVE family(id) AS (SELECT id FROM jobs WHERE parent_id=? UNION ALL SELECT j.id FROM jobs j JOIN family f ON j.parent_id=f.id) SELECT id FROM family", (job_id,)).fetchall()
                for child in children:
                    db.execute("UPDATE jobs SET status=CASE status WHEN 'queued' THEN 'cancelled' ELSE 'cancelling' END,updated_at=? WHERE id=? AND status IN ('queued','running')", (time.time(), child[0]))
        return self.job(job_id)

    def control(self, job_id, priority=None, dispatch_paused=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise StudioError("NOT_FOUND", "找不到工作", 404)
            if row[0] not in {"queued", "running"}:
                raise StudioError("JOB_TERMINAL", "工作已結束或正在取消", 409)
            if priority is not None:
                if type(priority) is not int or not 0 <= priority <= 100:
                    raise StudioError("INVALID_REQUEST", "優先順序必須為 0–100", 422)
                db.execute("UPDATE jobs SET priority=? WHERE id=?", (priority, job_id))
                db.execute("UPDATE jobs SET priority=? WHERE parent_id=? AND status='queued'", (priority, job_id))
            if dispatch_paused is not None:
                if type(dispatch_paused) is not bool:
                    raise StudioError("INVALID_REQUEST", "暫停設定必須為布林值", 422)
                db.execute("UPDATE jobs SET dispatch_paused=? WHERE id=?", (int(dispatch_paused), job_id))
                db.execute("UPDATE jobs SET dispatch_paused=? WHERE parent_id=? AND status='queued'", (int(dispatch_paused), job_id))
        return self.job(job_id)

    def publish(self, project_id: str, job_id: str, kind: str, data: bytes, suffix: str, provenance=None):
        if not suffix.isalnum():
            raise ValueError("成果副檔名無效")
        artifact_id = new_id("artifact")
        path = self.root / "artifacts" / f"{artifact_id}.{suffix}"
        temporary = path.with_suffix(path.suffix + ".staging")
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        replace_with_retry(temporary, path)  # Windows：剛寫好的檔可能被防毒短暫占用
        artifact = self.create("artifact", {"artifact_id": artifact_id, "job_id": job_id, "kind": kind,
            "filename": path.name, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
            "provenance": provenance or {}, "content_url": f"/v1/artifacts/{artifact_id}/content"},
            project_id, artifact_id)
        self.event(job_id, "artifact.ready", {"artifact_id": artifact_id, "kind": kind})
        return artifact

    def delete_project(self, project_id: str):
        """刪掉一個專案：它的資料列、工作、事件與成果檔（不可復原）。

        還有工作排隊中／執行中／取消中就先擋下（PROJECT_BUSY），請先取消或等它結束。
        只碰資料目錄內屬於這個專案的東西；使用者的原始影音（source 只記路徑）與輸出資料夾不在這裡，一律不動。
        """
        self.get(project_id, "project")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT COUNT(*) FROM jobs WHERE project_id=? AND status IN ('queued','running','cancelling')",
                                (project_id,)).fetchone()[0]
            if active:
                raise StudioError("PROJECT_BUSY", f"這個專案還有 {active} 個工作在排隊或執行中，請先取消或等它結束再刪除", 409)
            artifacts = [json.loads(row[0]) for row in
                         db.execute("SELECT body FROM entities WHERE kind='artifact' AND project_id=?", (project_id,))]
            jobs = [row[0] for row in db.execute("SELECT id FROM jobs WHERE project_id=?", (project_id,))]
            revisions = [row[0] for row in db.execute("SELECT id FROM entities WHERE project_id=? OR id=?", (project_id, project_id))]
            for batch in (jobs[i:i + 500] for i in range(0, len(jobs), 500)):
                db.execute(f"DELETE FROM events WHERE job_id IN ({','.join('?' * len(batch))})", batch)
            for batch in (revisions[i:i + 500] for i in range(0, len(revisions), 500)):
                # heads 指向這個專案的任何版本（sequence／transcript／alignment…）都要跟著刪
                db.execute(f"DELETE FROM heads WHERE revision IN ({','.join('?' * len(batch))})", batch)
            db.execute("DELETE FROM heads WHERE scope LIKE ?", (f"%{project_id}%",))
            db.execute("DELETE FROM jobs WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM idempotency WHERE scope=?", (project_id,))
            db.execute("DELETE FROM entities WHERE project_id=? OR id=?", (project_id, project_id))
        freed, removed = 0, 0
        for meta in artifacts:
            path = self.root / "artifacts" / meta.get("filename", "")
            if meta.get("filename") and path.is_file():
                freed += path.stat().st_size
                path.unlink(missing_ok=True)
                removed += 1
        for job_id in jobs:
            folder = self.root / "jobs" / job_id
            if folder.is_dir():
                freed += sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
                shutil.rmtree(folder, ignore_errors=True)
        return {"project_id": project_id, "removed_entities": len(revisions), "removed_jobs": len(jobs),
                "removed_artifacts": removed, "freed_bytes": freed}

    ACTIVE_JOB_STATES = ("queued", "running", "cancelling")
    STALE_STAGING_SEC = 600

    def cleanup(self, dry_run=False):
        """清掉資料目錄裡沒人要的暫存：已結束工作的工作目錄、沒有紀錄的成果檔、殘留的 .staging。

        絕不刪：任何還有 artifact 紀錄的成果檔、排隊中／執行中／取消中工作的工作目錄，
        以及資料目錄以外的東西（使用者的原始素材、輸出資料夾都不在這裡）。
        dry_run=True 只計算不刪除，介面可以先給使用者看會清掉什麼。
        """
        with self.connect() as db:
            rows = db.execute("SELECT id,status FROM jobs").fetchall()
        active = {row[0] for row in rows if row[1] in self.ACTIVE_JOB_STATES}
        with self.connect() as db:
            files = {json.loads(row[0])["filename"] for row in
                     db.execute("SELECT body FROM entities WHERE kind='artifact'").fetchall()}
        report = {"removed_job_dirs": 0, "removed_artifacts": 0, "removed_staging": 0,
                  "freed_bytes": 0, "kept_artifacts": len(files), "kept_job_dirs": 0, "dry_run": bool(dry_run)}
        jobs_root = self.root / "jobs"
        if jobs_root.is_dir():
            for folder in jobs_root.iterdir():
                if not folder.is_dir():
                    continue
                if folder.name in active:
                    report["kept_job_dirs"] += 1
                    continue
                size = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
                report["removed_job_dirs"] += 1
                report["freed_bytes"] += size
                if not dry_run:
                    shutil.rmtree(folder, ignore_errors=True)
        artifacts_root = self.root / "artifacts"
        now = time.time()
        if artifacts_root.is_dir():
            for path in artifacts_root.iterdir():
                if not path.is_file() or path.name in files:
                    continue
                staging = path.name.endswith(".staging")
                # 剛寫到一半的 .staging 可能正在發布中：只清明顯過期的
                if staging and now - path.stat().st_mtime < self.STALE_STAGING_SEC:
                    continue
                report["removed_staging" if staging else "removed_artifacts"] += 1
                report["freed_bytes"] += path.stat().st_size
                if not dry_run:
                    path.unlink(missing_ok=True)
        return report

    def artifact_path(self, artifact_id, verify=True):
        meta = self.get(artifact_id, "artifact")
        path = self.root / "artifacts" / meta["filename"]
        if not path.is_file():
            raise StudioError("ARTIFACT_MISSING", "成果檔案遺失", 409)
        if verify:
            with open_with_retry(path, "rb") as stream:  # 剛發布的檔案可能被防毒短暫占用
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != meta["sha256"]:
                raise StudioError("ARTIFACT_CORRUPT", "成果檔案校驗失敗", 409)
        return path

    def publish_file(self, project_id, job_id, kind, source, provenance=None):
        source = Path(source)
        artifact_id = new_id("artifact")
        path = self.root / "artifacts" / f"{artifact_id}{source.suffix}"
        temporary = path.with_suffix(path.suffix + ".staging")
        # 邊複製邊算雜湊：不必把剛改名的正式檔馬上再讀一次（Windows 上那一刻常被防毒占用 → Permission denied）
        hasher, size = hashlib.sha256(), 0
        with open_with_retry(source, "rb") as src, temporary.open("xb") as dest:
            while block := src.read(1024 * 1024):
                hasher.update(block)
                dest.write(block)
                size += len(block)
            dest.flush()
            os.fsync(dest.fileno())
        replace_with_retry(temporary, path)  # Windows：剛寫好的檔可能被防毒短暫占用
        digest = hasher.hexdigest()
        artifact = self.create("artifact", {"artifact_id": artifact_id, "job_id": job_id, "kind": kind,
            "filename": path.name, "sha256": digest, "size_bytes": size,
            "provenance": provenance or {}, "content_url": f"/v1/artifacts/{artifact_id}/content"}, project_id, artifact_id)
        self.event(job_id, "artifact.ready", {"artifact_id": artifact_id, "kind": kind})
        return artifact
