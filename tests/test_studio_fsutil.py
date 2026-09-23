"""Windows 上剛寫好的檔案可能被防毒／索引服務短暫占用（WinError 32），改名要有限次重試。

2026-09-19 根因：`test_studio_workflow.py::test_upload_probe_sequence_media_and_subtitle_export` 間歇失敗，
工作錯誤為「[WinError 32] 程序無法存取檔案，因為檔案正由另一個程序使用。: '…srt.staging' -> '…srt'」。
使用者真實匯出也會遇到同一件事，所以所有「暫存檔→正式檔」的改名都要走 `replace_with_retry`。"""
import os

import pytest


def sharing_violation():
    error = PermissionError(13, '程序無法存取檔案，因為檔案正由另一個程序使用。')
    error.winerror = 32
    return error


def test_replace_with_retry_survives_a_transient_sharing_violation(tmp_path, monkeypatch):
    from app.studio import fsutil
    monkeypatch.setattr(fsutil.time, 'sleep', lambda seconds: None)
    source, target = tmp_path / 'a.staging', tmp_path / 'a.srt'
    source.write_text('字幕', encoding='utf-8')
    calls = []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise sharing_violation()
        os.replace(src, dst)
    fsutil.replace_with_retry(source, target, replace=flaky)
    assert len(calls) == 3 and target.read_text(encoding='utf-8') == '字幕' and not source.exists()


def test_replace_with_retry_gives_up_and_never_hides_other_errors(tmp_path, monkeypatch):
    from app.studio import fsutil
    monkeypatch.setattr(fsutil.time, 'sleep', lambda seconds: None)
    calls = []

    def always_busy(src, dst):
        calls.append(1)
        raise sharing_violation()
    with pytest.raises(PermissionError):
        fsutil.replace_with_retry(tmp_path / 'a', tmp_path / 'b', attempts=4, replace=always_busy)
    assert len(calls) == 4
    calls.clear()

    def exists(src, dst):
        calls.append(1)
        raise FileExistsError(17, '檔案已存在')
    with pytest.raises(FileExistsError):
        fsutil.replace_with_retry(tmp_path / 'a', tmp_path / 'b', replace=exists)
    assert len(calls) == 1  # 不是暫時性占用：不重試，照常拋出（「同名不覆蓋」的語意不能被重試蓋掉）


def test_store_publish_and_delivery_retry_when_the_new_file_is_briefly_locked(tmp_path, monkeypatch):
    from app.studio import fsutil
    from app.studio.destinations import deliver_file
    from app.studio.store import Store
    monkeypatch.setattr(fsutil.time, 'sleep', lambda seconds: None)
    real_replace, real_rename = os.replace, os.rename
    state = {'failures': 0}

    def busy_then(real):
        def call(src, dst):
            if str(src).endswith('.staging') and state['failures'] < 2:
                state['failures'] += 1
                raise sharing_violation()
            return real(src, dst)
        return call
    monkeypatch.setattr(fsutil.os, 'replace', busy_then(real_replace))
    monkeypatch.setattr(fsutil.os, 'rename', busy_then(real_rename))
    store = Store(tmp_path / 'state')
    project = store.create('project', {'name': '占用重試'})['id']
    job = store.submit(project, {'kind': 'export', 'source_id': 's', 'ranges': [{'start_us': 0, 'end_us': 1}]})
    artifact = store.publish(project, job['id'], 'srt', '字幕'.encode(), 'srt')
    assert state['failures'] == 2 and store.artifact_path(artifact['id']).read_bytes() == '字幕'.encode()
    state['failures'] = 0
    copied = store.publish_file(project, job['id'], 'srt', store.artifact_path(artifact['id']))
    assert state['failures'] == 2 and store.artifact_path(copied['id']).is_file()
    assert not list((tmp_path / 'state' / 'artifacts').glob('*.staging'))
    out = tmp_path / 'out'
    out.mkdir()
    config = {'roots': {'chosen': str(out)}}
    for overwrite in (False, True):
        state['failures'] = 0
        saved = deliver_file(store.artifact_path(artifact['id']), config, 'chosen', 'project_1', 'job_1', f'字幕{int(overwrite)}.srt', overwrite=overwrite)
        assert state['failures'] == 2 and (out / 'subtitle_studio' / f'字幕{int(overwrite)}.srt').is_file() and saved['size_bytes'] > 0
    assert not list(out.rglob('*.staging'))


def test_delivery_reports_a_clear_error_when_the_target_is_open_in_another_program(tmp_path, monkeypatch):
    # 覆寫最新一份時，使用者可能正用別的程式開著舊的 SRT：重試後仍被占用要回清楚的錯誤，不是籠統的「無法寫入」
    from app.studio import fsutil
    from app.studio.destinations import deliver_file
    from app.studio.store import StudioError
    monkeypatch.setattr(fsutil.time, 'sleep', lambda seconds: None)

    def busy(src, dst):
        raise sharing_violation()
    monkeypatch.setattr(fsutil.os, 'replace', busy)
    src = tmp_path / 'a.srt'
    src.write_text('x', encoding='utf-8')
    out = tmp_path / 'out'
    out.mkdir()
    with pytest.raises(StudioError) as caught:
        deliver_file(src, {'roots': {'chosen': str(out)}}, 'chosen', 'project_1', 'job_1', '字幕.srt', overwrite=True)
    assert caught.value.code == 'OUTPUT_FILE_IN_USE' and '其他程式' in caught.value.message
    assert not list(out.rglob('*.staging')) and src.read_text(encoding='utf-8') == 'x'


def test_reading_back_a_fresh_file_retries_and_publish_file_hashes_while_copying(tmp_path, monkeypatch):
    # 同一根因的第二種表現：改名成功後馬上讀回來算 SHA，被防毒短暫占用 →「[Errno 13] Permission denied: …artifact_xxx.mp4」
    import builtins
    import hashlib
    from app.studio import fsutil
    from app.studio.store import Store
    monkeypatch.setattr(fsutil.time, 'sleep', lambda seconds: None)
    store = Store(tmp_path / 'state')
    project = store.create('project', {'name': '讀回重試'})['id']
    job = store.submit(project, {'kind': 'export', 'source_id': 's', 'ranges': [{'start_us': 0, 'end_us': 1}]})
    media = tmp_path / 'clip.mp4'
    media.write_bytes(b'video-bytes' * 1000)
    state = {'failures': 0, 'budget': 0}
    real_open = builtins.open

    def busy_open(path, mode='r', *args, **kwargs):
        if 'artifacts' in str(path) and str(path).endswith('.mp4') and 'r' in mode and state['failures'] < state['budget']:
            state['failures'] += 1
            error = PermissionError(13, 'Permission denied')
            error.winerror = 32
            raise error
        return real_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(fsutil, 'open', busy_open, raising=False)
    # 發布時邊複製邊算雜湊：不需要把剛改名的正式檔再讀一次
    artifact = store.publish_file(project, job['id'], 'mp4', media)
    assert artifact['sha256'] == hashlib.sha256(media.read_bytes()).hexdigest() and artifact['size_bytes'] == media.stat().st_size
    # 之後驗證讀取遇到短暫占用：重試後成功
    state['budget'] = 2
    assert store.artifact_path(artifact['id']).is_file() and state['failures'] == 2
    # 一直被占用：重試用完後拋出原錯誤（不吞掉）
    state.update(failures=0, budget=99)
    with pytest.raises(PermissionError):
        store.artifact_path(artifact['id'])
