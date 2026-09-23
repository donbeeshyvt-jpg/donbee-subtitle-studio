"""指定目錄發布保留原檔、限制路徑並清除失敗暫存。"""
import errno
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.studio.store import StudioError


def test_copy_to_allowed_root_is_verified_and_never_overwrites(tmp_path):
    from app.studio.destinations import deliver_file
    src = tmp_path / 'input.srt'
    src.write_bytes('字幕'.encode())
    out = tmp_path / 'out'
    out.mkdir()
    config = {'roots': {'chosen': str(out)}}
    result = deliver_file(src, config, 'chosen', 'project_1', 'job_1', '../../CON:<影片>.srt')
    saved = Path(result['path'])
    assert saved.resolve().is_relative_to(out.resolve())
    assert saved.read_bytes() == src.read_bytes()
    assert result['sha256'] == hashlib.sha256(src.read_bytes()).hexdigest()
    again = deliver_file(src, config, 'chosen', 'project_1', 'job_1', '../../CON:<影片>.srt')
    assert again['path'] != result['path']
    assert src.exists() and saved.exists()
    assert not list(out.rglob('*.staging'))


def test_every_output_folder_delivers_into_subtitle_studio_with_the_given_name(tmp_path):
    # 使用者 2026-09-19：不論輸出位置是匯入檔旁、還是自己用視窗選的資料夾，一律放進 subtitle_studio；
    # 不再有 <專案名>/<日期時間_工作短碼>/ 這種分層，檔名照呼叫端給的（跟著素材檔名）。
    from app.studio.destinations import deliver_file, delivery_folder
    src = tmp_path / 'input.srt'
    src.write_text('第一版', encoding='utf-8')
    newer = tmp_path / 'newer.srt'
    newer.write_text('第二版', encoding='utf-8')
    out = tmp_path / '天照堂素材'
    out.mkdir()
    config = {'roots': {'chosen': str(out)}}
    assert delivery_folder(config, 'chosen') == out.resolve() / 'subtitle_studio'
    named = deliver_file(src, config, 'chosen', 'project_1', 'job_abcdef', '字幕.srt')
    assert Path(named['relative_path']).as_posix() == 'subtitle_studio/字幕.srt'
    assert [p.name for p in out.iterdir()] == ['subtitle_studio']
    # 預設不覆蓋（影音）：字幕 (2).srt
    twice = deliver_file(src, config, 'chosen', 'project_1', 'job_abcdef', '字幕.srt')
    assert Path(twice['relative_path']).as_posix() == 'subtitle_studio/字幕 (2).srt'
    # 字幕與紀錄：覆寫成最新一份
    latest = deliver_file(newer, config, 'chosen', 'project_1', 'job_2', '字幕.srt', overwrite=True)
    assert Path(latest['path']) == out.resolve() / 'subtitle_studio' / '字幕.srt'
    assert (out / 'subtitle_studio' / '字幕.srt').read_text(encoding='utf-8') == '第二版'
    assert not list(out.rglob('*.staging'))
    # 選到的資料夾本身就叫 subtitle_studio（匯入檔旁自動建立的那個）：不再多套一層
    side = tmp_path / 'EP7' / 'subtitle_studio'
    side.mkdir(parents=True)
    config = {'roots': {'side': str(side)}, 'root_kinds': {'side': 'sidecar'}}
    assert delivery_folder(config, 'side') == side.resolve()
    direct = deliver_file(src, config, 'side', 'project_1', 'job_1', '字幕.srt')
    assert Path(direct['relative_path']).as_posix() == '字幕.srt'


def test_free_variant_gives_one_shared_suffix_for_a_whole_export(tmp_path):
    # 影音輸出不覆蓋舊檔：同一次輸出的所有檔案共用同一個尾碼，影片、字幕、清單才對得起來
    from app.studio.destinations import free_variant
    folder = tmp_path / 'subtitle_studio'
    folder.mkdir()
    build = lambda variant: [f'EP7{variant}_01.mp4', f'EP7{variant}_02.mp4', f'EP7{variant}.media_manifest.json']
    assert free_variant(folder, build) == ''
    (folder / 'EP7_02.mp4').write_bytes(b'x')
    assert free_variant(folder, build) == ' (2)'
    (folder / 'EP7 (2).media_manifest.json').write_bytes(b'x')
    assert free_variant(folder, build) == ' (3)'
    assert free_variant(tmp_path / 'not-created-yet', build) == ''


def test_unknown_root_and_unsafe_internal_ids_rejected(tmp_path):
    from app.studio.destinations import deliver_file
    src = tmp_path / 'a.txt'
    src.write_text('x')
    with pytest.raises(StudioError) as e:
        deliver_file(src, {'roots': {}}, 'unknown', 'p', 'j', 'a.txt')
    assert e.value.code == 'OUTPUT_ROOT_UNAVAILABLE'
    with pytest.raises(StudioError):
        deliver_file(src, {'roots': {'out': str(tmp_path)}}, 'out', '../escape', 'j', 'a.txt')


def test_disk_full_never_publishes_partial_file(tmp_path, monkeypatch):
    from app.studio import destinations
    src = tmp_path / 'a.txt'
    src.write_text('keep')
    out = tmp_path / 'out'
    out.mkdir()
    def fail(src, dest, *args):
        dest.write(b'partial')
        raise OSError(errno.ENOSPC, 'disk full')
    monkeypatch.setattr(destinations.shutil, 'copyfileobj', fail)
    with pytest.raises(StudioError) as e:
        destinations.deliver_file(src, {'roots': {'out': str(out)}}, 'out', 'p', 'j', 'a.txt')
    assert e.value.code == 'DISK_FULL'
    assert not [p for p in out.rglob('*') if p.is_file()]
    assert src.read_text() == 'keep'
