"""允許目錄內的輸出；暫存完成並校驗後才公開檔案。

輸出位置規則（使用者 2026-09-18／19）：不論是匯入檔旁自動建立的位置、還是使用者用視窗選的資料夾，
檔案一律平放在 `subtitle_studio` 資料夾裡；選到的資料夾本身就叫 subtitle_studio 時不再多套一層。
字幕與紀錄（overwrite=True）覆寫成最新一份；影音預設不覆蓋，整批共用同一個不重複尾碼（free_variant）。"""
from __future__ import annotations

import errno
import hashlib
import os
from pathlib import Path
import re
import shutil

from .fsutil import is_sharing_violation, open_with_retry, replace_with_retry
from .store import StudioError


def output_root(config, root_id):
    value = config.get('roots', {}).get(root_id)
    if not isinstance(value, str) or not value:
        raise StudioError('OUTPUT_ROOT_UNAVAILABLE', '尚未配置此儲存目錄', 403)
    path = Path(value).resolve()
    if not path.is_dir():
        raise StudioError('OUTPUT_ROOT_UNAVAILABLE', '儲存目錄不存在或無法使用', 403)
    return path


STUDIO_FOLDER = 'subtitle_studio'


def delivery_folder(config, root_id):
    """這個輸出位置實際放檔案的資料夾：<位置>/subtitle_studio（位置本身就叫 subtitle_studio 時就是它自己）。"""
    base = output_root(config, root_id)
    return base if base.name.lower() == STUDIO_FOLDER else base / STUDIO_FOLDER


def free_variant(folder, build, limit=500):
    """整批輸出共用一個不重複的尾碼：'' → ' (2)' → ' (3)'…；build(variant) 回傳這批要用的檔名。"""
    folder = Path(folder)
    for counter in range(1, limit):
        variant = '' if counter == 1 else f' ({counter})'
        if not any((folder / safe_filename(name)).exists() for name in build(variant)):
            return variant
    raise StudioError('OUTPUT_WRITE_FAILED', '輸出資料夾內同名檔案過多，請整理後再輸出', 409)


def safe_filename(value):
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', str(value)).strip(' .')[:150].rstrip(' .')
    if not name:
        name = '冬比輸出'
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name, re.I):
        name = '_' + name
    return name


def deliver_file(source, config, root_id, project_id, job_id, filename, overwrite=False):
    base = output_root(config, root_id)
    for identity in (project_id, job_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]+', identity):
            raise StudioError('INVALID_REQUEST', '工作識別碼無效', 422)
    # 一律平放在 subtitle_studio 裡（這個資料夾是本程式自己建立的）；不再有 <專案名>/<日期時間> 分層
    folder = delivery_folder(config, root_id)
    if not folder.resolve().is_relative_to(base):
        raise StudioError('ACCESS_DENIED', '輸出位置不在允許目錄內', 403)
    source = Path(source)
    temporary = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        folder = folder.resolve()
        if not folder.is_relative_to(base):
            raise StudioError('ACCESS_DENIED', '輸出目錄連結已變更', 403)
        if shutil.disk_usage(folder).free < source.stat().st_size:
            raise StudioError('DISK_FULL', '儲存目錄空間不足', 507)
        wanted = safe_filename(filename)
        target = folder / wanted
        stem, suffix = os.path.splitext(wanted)
        counter = 2
        while target.exists() and not overwrite:  # 同名不覆蓋：字幕 (2).srt、字幕 (3).srt…
            target = folder / f"{stem} ({counter}){suffix}"
            counter += 1
        temporary = target.with_suffix(target.suffix + '.staging')
        with open_with_retry(source, 'rb') as src, temporary.open('xb') as dest:
            shutil.copyfileobj(src, dest, 1024 * 1024)
            dest.flush()
            os.fsync(dest.fileno())
        # 寫完再讀回來比對（防寫入損毀）；剛寫好的檔可能被防毒短暫占用，讀取要重試
        with open_with_retry(source, 'rb') as src, open_with_retry(temporary, 'rb') as dest:
            expected = hashlib.file_digest(src, 'sha256').hexdigest()
            actual = hashlib.file_digest(dest, 'sha256').hexdigest()
        if actual != expected:
            raise StudioError('ARTIFACT_CORRUPT', '儲存檔案校驗失敗', 409)
        # Windows rename 拒絕既有目的檔；POSIX 以建立連結達成相同行為。overwrite（字幕與紀錄）才原子覆寫成最新一份。
        if overwrite:
            replace_with_retry(temporary, target)
        elif os.name == 'nt':
            replace_with_retry(temporary, target, replace=os.rename)
        else:
            os.link(temporary, target)
            temporary.unlink()
        return {'path': str(target), 'root_id': root_id, 'relative_path': target.relative_to(base).as_posix(),
                'filename': target.name, 'sha256': actual, 'size_bytes': target.stat().st_size}
    except OSError as error:
        if is_sharing_violation(error):
            # 重試後仍被占用：多半是使用者正用別的程式開著同名舊檔（覆寫最新一份時）
            raise StudioError('OUTPUT_FILE_IN_USE', '輸出檔案正被其他程式使用，請關閉該檔案後再輸出一次', 409) from error
        code = 'DISK_FULL' if error.errno == errno.ENOSPC or getattr(error, 'winerror', None) == 112 else 'OUTPUT_WRITE_FAILED'
        raise StudioError(code, '儲存空間不足' if code == 'DISK_FULL' else '無法寫入儲存目錄', 507 if code == 'DISK_FULL' else 409) from error
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
