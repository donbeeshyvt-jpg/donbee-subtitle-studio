"""舊資料目錄（%LOCALAPPDATA%/DongbiStudio）到專案 data/ 的一次性複製遷移。

只複製、不刪除來源；SQLite 以 backup API 取得一致快照；完成後寫 MIGRATED_FROM.json 供介面與 doctor 顯示。
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3

FILES = ("config.json", "secrets.json")
DIRS = ("artifacts", "jobs")
DB = "studio.sqlite3"
MARKERS = ("config.json", DB)


def detect_legacy(data_dir, legacy_dir):
    """舊目錄有資料且新目錄尚無資料時才需要遷移。"""
    data_dir = Path(data_dir)
    legacy = Path(legacy_dir) if legacy_dir else None
    if legacy is None or not legacy.is_dir() or not any((legacy / marker).is_file() for marker in MARKERS):
        return dict(needed=False, reason="no_legacy", legacy_dir=str(legacy) if legacy else None)
    if any((data_dir / marker).exists() for marker in MARKERS):
        return dict(needed=False, reason="data_present", legacy_dir=str(legacy))
    return dict(needed=True, reason="legacy_found", legacy_dir=str(legacy))


def _copy_database(source, target):
    # 用 backup API 複製，避免直接複製到一半的 WAL 檔造成不一致
    src = sqlite3.connect(str(source))
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def migrate(data_dir, legacy_dir):
    data_dir = Path(data_dir)
    legacy = Path(legacy_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    copied, skipped = [], []
    for name in FILES:
        source = legacy / name
        if source.is_file():
            shutil.copy2(source, data_dir / name)
            copied.append(name)
        else:
            skipped.append(name)
    if (legacy / DB).is_file():
        _copy_database(legacy / DB, data_dir / DB)
        copied.append(DB)
    else:
        skipped.append(DB)
    for name in DIRS:
        source = legacy / name
        if source.is_dir():
            shutil.copytree(source, data_dir / name, dirs_exist_ok=True)
            copied.append(name + "/")
        else:
            skipped.append(name + "/")
    record = dict(source=str(legacy), migrated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  copied=copied, skipped=skipped, note="來源未刪除；確認無誤後可自行移除舊目錄")
    (data_dir / "MIGRATED_FROM.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record


def migrate_if_needed(data_dir, legacy_dir):
    detection = detect_legacy(data_dir, legacy_dir)
    if detection["needed"]:
        return migrate(data_dir, legacy_dir)
    return None
