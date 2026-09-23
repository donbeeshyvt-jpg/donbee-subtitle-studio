"""使用者 2026-09-18 第六輪：輸出跟著匯入檔案走。
- 資料夾／檔案用原生視窗選（不用打字）；
- 依路徑匯入本機檔（不複製、不改原檔），同資料夾自動建立 `subtitle_studio`，預設輸出到那裡；
- 該資料夾採「平放＋覆寫最新」：SRT、逐字稿 JSON、修改紀錄 JSON、匯出清單；
- 轉錄完成→自動逐詞對齊→自動輸出字幕與紀錄。"""
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.studio.api import create_app
from app.studio.store import Store
from app.studio.worker import Worker


def test_native_dialog_endpoints_return_the_chosen_path(tmp_path, monkeypatch):
    from app.studio import dialogs
    chosen = tmp_path / '成品'
    chosen.mkdir()
    calls = []

    def fake(kind, title=None, initial=None):
        calls.append((kind, title))
        return str(chosen) if kind == 'folder' else str(chosen / 'a.wav')
    monkeypatch.setattr(dialogs, 'native_dialog', fake)
    with TestClient(create_app(tmp_path / 'state', start_workers=False)) as c:
        c.get('/v1/session')
        folder = c.post('/v1/dialogs/folder', json={'title': '選擇輸出資料夾'})
        assert folder.status_code == 200 and folder.json() == {'path': str(chosen), 'cancelled': False}
        file = c.post('/v1/dialogs/file', json={})
        assert file.json()['path'].endswith('a.wav')
        monkeypatch.setattr(dialogs, 'native_dialog', lambda *a, **k: None)
        assert c.post('/v1/dialogs/folder', json={}).json() == {'path': None, 'cancelled': True}

        def broken(*a, **k):
            raise dialogs.DialogUnavailable('no display')
        monkeypatch.setattr(dialogs, 'native_dialog', broken)
        unavailable = c.post('/v1/dialogs/folder', json={})
        assert unavailable.status_code == 501 and unavailable.json()['error']['code'] == 'DIALOG_UNAVAILABLE'
    assert calls[0] == ('folder', '選擇輸出資料夾')


def test_import_local_file_by_path_keeps_the_original_and_prepares_sidecar_folder(tmp_path):
    media_dir = tmp_path / '剪輯案' / 'EP7'
    media_dir.mkdir(parents=True)
    original = media_dir / '剪好的音訊.wav'
    original.write_bytes(b'RIFF' + b'\0' * 64)
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    with TestClient(create_app(tmp_path / 'state', start_workers=False)) as c:
        c.get('/v1/session')
        project = c.post('/v1/projects', json={'name': '依路徑匯入'}).json()['project_id']
        created = c.post(f'/v1/projects/{project}/sources/local-file', json={'path': str(original)})
        assert created.status_code == 201, created.text
        source = created.json()
        assert source['kind'] == 'local' and source['title'] == '剪好的音訊.wav' and source['job_id']
        assert source['origin_dir'] == str(media_dir.resolve())
        sidecar = media_dir / 'subtitle_studio'
        assert sidecar.is_dir()
        roots = c.get('/v1/capabilities').json()['output_roots']
        root = next(r for r in roots if r['id'] == source['default_output_root_id'])
        assert root['path'] == str(sidecar.resolve()) and root['kind'] == 'sidecar'
        assert root['target'] == str(sidecar.resolve())  # 本身就叫 subtitle_studio：不再多套一層
        # 原檔不複製、不改動
        assert hashlib.sha256(original.read_bytes()).hexdigest() == digest
        assert not list((tmp_path / 'state' / 'uploads').glob('*')) if (tmp_path / 'state' / 'uploads').exists() else True
        assert c.post(f'/v1/projects/{project}/sources/local-file', json={'path': str(media_dir / 'missing.wav')}).status_code == 422
        bad = media_dir / 'note.txt'
        bad.write_text('x')
        assert c.post(f'/v1/projects/{project}/sources/local-file', json={'path': str(bad)}).status_code == 422


def test_sidecar_root_delivers_flat_and_overwrites_the_latest(tmp_path):
    from app.studio.destinations import deliver_file
    out = tmp_path / 'subtitle_studio'
    out.mkdir()
    config = {'roots': {'side': str(out)}, 'root_kinds': {'side': 'sidecar'}}
    first = tmp_path / 'a.srt'
    first.write_text('第一版', encoding='utf-8')
    second = tmp_path / 'b.srt'
    second.write_text('第二版', encoding='utf-8')
    one = deliver_file(first, config, 'side', 'project_1', 'job_1', '剪好的音訊.srt', overwrite=True)
    assert Path(one['path']) == out / '剪好的音訊.srt'
    two = deliver_file(second, config, 'side', 'project_1', 'job_2', '剪好的音訊.srt', overwrite=True)
    assert Path(two['path']) == out / '剪好的音訊.srt' and (out / '剪好的音訊.srt').read_text(encoding='utf-8') == '第二版'
    assert not list(out.glob('*.staging')) and len(list(out.iterdir())) == 1


def _transcript_project(tmp_path):
    store = Store(tmp_path / 'state')
    project = store.create('project', {'name': 'EP7'})['id']
    source = store.create('source', {'kind': 'local', 'title': '剪好的音訊.wav'}, project)
    store.revise(f"source:{source['id']}", 'source_metadata', {'duration_us': 2000000}, None, project, initial=None)
    cues = [{'id': 'c1', 'start_us': 0, 'end_us': 900000, 'text': '第一句', 'words': []},
            {'id': 'c2', 'start_us': 1000000, 'end_us': 1900000, 'text': '第二句', 'words': []}]
    base = store.revise(f"transcript:{source['id']}:default", 'transcript', {'source_id': source['id'], 'audio_track_id': 'default',
        'cues': cues, 'asset_ids': [], 'settings': {'engine': 'faster-whisper'}}, None, project, initial=None)
    return store, project, source, base


def test_export_with_records_writes_srt_transcript_and_history_next_to_the_media(tmp_path):
    store, project, source, base = _transcript_project(tmp_path)
    out = tmp_path / 'media' / 'subtitle_studio'
    out.mkdir(parents=True)
    (store.root / 'config.json').write_text(json.dumps({'roots': {'side': str(out)}, 'root_kinds': {'side': 'sidecar'}, 'providers': []}), encoding='utf-8')
    job = store.submit(project, {'kind': 'export', 'source_id': source['id'], 'ranges': [{'start_us': 0, 'end_us': 2000000}],
        'transcript_revision': base['id'], 'formats': ['srt'], 'grouping': 'merge', 'subtitle_timebase': 'sequence',
        'alignment_policy': 'allow_segment', 'output_root_id': 'side', 'include_records': True})
    result = Worker(store, job).export()
    names = sorted(Path(f['path']).name for f in result['saved_files'])
    assert names == ['剪好的音訊.export_manifest.json', '剪好的音訊.history.json', '剪好的音訊.srt', '剪好的音訊.transcript.json']
    history = json.loads((out / '剪好的音訊.history.json').read_text(encoding='utf-8'))
    assert history['base']['revision'] == base['id'] and history['steps'] == []
    transcript = json.loads((out / '剪好的音訊.transcript.json').read_text(encoding='utf-8'))
    assert [c['text'] for c in transcript['cues']] == ['第一句', '第二句'] and transcript['revision'] == base['id']
    assert '第一句' in (out / '剪好的音訊.srt').read_text(encoding='utf-8')


def test_export_to_a_chosen_folder_uses_subtitle_studio_and_the_media_name(tmp_path):
    # 使用者 2026-09-19 回報：自己選資料夾後輸出，跑出「<專案名>/<日期時間>/<專案名>_01_srt.srt」，跟說好的不同。
    # 正確：<選的資料夾>/subtitle_studio/<素材檔名>.srt／.transcript.json／.history.json／.export_manifest.json，再輸出一次覆寫成最新。
    store, project, source, base = _transcript_project(tmp_path)
    chosen = tmp_path / '天照堂-圖片素材與PR模板'
    chosen.mkdir()
    (store.root / 'config.json').write_text(json.dumps({'roots': {'picked': str(chosen)}, 'root_kinds': {'picked': 'folder'}, 'providers': []}), encoding='utf-8')
    body = {'kind': 'export', 'source_id': source['id'], 'ranges': [{'start_us': 0, 'end_us': 2000000}],
        'transcript_revision': base['id'], 'formats': ['srt'], 'grouping': 'merge', 'subtitle_timebase': 'sequence',
        'alignment_policy': 'allow_segment', 'output_root_id': 'picked', 'include_records': True}
    result = Worker(store, store.submit(project, body)).export()
    assert [p.name for p in chosen.iterdir()] == ['subtitle_studio']
    expected = ['剪好的音訊.export_manifest.json', '剪好的音訊.history.json', '剪好的音訊.srt', '剪好的音訊.transcript.json']
    assert sorted(p.name for p in (chosen / 'subtitle_studio').iterdir()) == expected
    assert sorted(Path(f['path']).name for f in result['saved_files']) == expected
    Worker(store, store.submit(project, {**body, 'keep_punctuation': True})).export()
    assert sorted(p.name for p in (chosen / 'subtitle_studio').iterdir()) == expected  # 覆寫成最新，不會長出 (2)


def test_delivery_names_follow_the_media_name_not_the_project(tmp_path):
    from app.studio.worker import delivery_names, title_stem
    assert title_stem('剪好的音訊.wav') == '剪好的音訊'
    assert title_stem('【歌回】v1.5 最終版') == '【歌回】v1.5 最終版'  # YouTube 標題不是檔名，不能把最後一個點之後切掉
    assert len(title_stem('長' * 300)) <= 80
    assert title_stem('') == '冬比輸出'
    subtitle = [{'id': 'a1', 'kind': 'srt'}, {'id': 'a2', 'kind': 'transcript_record'}, {'id': 'a3', 'kind': 'transcript_history'}, {'id': 'a4', 'kind': 'export_manifest'}]
    assert delivery_names('EP7', subtitle, ['.srt', '.json', '.json', '.json']) == ['EP7.srt', 'EP7.transcript.json', 'EP7.history.json', 'EP7.export_manifest.json']
    # 影音（各片段獨立＋附字幕）：同片段的影片與字幕編號一致；清單另名，不蓋掉字幕頁的清單；整批共用尾碼
    media = [{'id': 'm1', 'kind': 'mp4'}, {'id': 'm2', 'kind': 'mp4'}, {'id': 's1', 'kind': 'srt'}, {'id': 's2', 'kind': 'srt'}, {'id': 'x', 'kind': 'export_manifest'}]
    assert delivery_names('EP7', media, ['.mp4', '.mp4', '.srt', '.srt', '.json'], variant=' (2)', media_job=True) == [
        'EP7 (2)_01.mp4', 'EP7 (2)_02.mp4', 'EP7 (2)_01.srt', 'EP7 (2)_02.srt', 'EP7 (2).media_manifest.json']
    # 同副檔名的不同種類不能撞名
    both = [{'id': 't', 'kind': 'transcript'}, {'id': 'p', 'kind': 'project'}]
    assert len(set(delivery_names('EP7', both, ['.json', '.json']))) == 2


def test_alignment_chains_the_automatic_subtitle_export(tmp_path, monkeypatch):
    """轉錄時設定了自動輸出：analyze 把設定帶給 align 子工作；align 成功後自動排 export（整段素材、含紀錄）。"""
    from app.studio.contracts import JobRequest
    request = JobRequest(kind='analyze', source_id='s', auto_export={'output_root_id': 'side', 'sentences_per_cue': 2, 'keep_punctuation': True})
    assert request.auto_export['sentences_per_cue'] == 2
    store, project, source, base = _transcript_project(tmp_path)
    asset = store.create('asset', {'source_id': source['id'], 'kind': 'audio', 'path': str(tmp_path / 'a.wav'), 'duration_us': 2000000,
        'source_map': [{'source_start_us': 0, 'source_end_us': 2000000, 'asset_start_us': 0}], 'map_revision': 'm', 'content_hash': 'x'}, project)
    job = store.submit(project, {'kind': 'align', 'transcript_revision': base['id'], 'source_id': source['id'],
        'then_export': {'output_root_id': 'side', 'sentences_per_cue': 2, 'keep_punctuation': True, 'asset_id': asset['id']}}, 'gpu')
    worker = Worker(store, job)
    monkeypatch.setattr(worker, 'refine_align', lambda: {'alignment_revision': 'alignment_1', 'artifacts': [], 'warnings': []})
    result = worker.run()
    exports = [j for j in store.jobs(project, 50)['items'] if j['kind'] == 'export']
    assert len(exports) == 1 and result['export_job_id'] == exports[0]['id']
    body = exports[0]['body']
    assert body['formats'] == ['srt'] and body['include_records'] is True and body['output_root_id'] == 'side'
    assert body['alignment_revision'] == 'alignment_1' and body['transcript_revision'] == base['id']
    assert body['ranges'] == [{'start_us': 0, 'end_us': 2000000}] and body['sentences_per_cue'] == 2 and body['keep_punctuation'] is True


def test_youtube_outputs_are_named_by_the_video_title_not_the_url(tmp_path):
    # 2026-09-19 真跑發現：YouTube 來源的 title 存的是網址，輸出檔名變成 https___www.youtube.com_watch_v=…；要用探測到的影片標題
    store = Store(tmp_path / 'state')
    project = store.create('project', {'name': '歌回精選'})['id']
    url = 'https://www.youtube.com/watch?v=Yn2mE6_tMC8'
    source = store.create('source', {'kind': 'youtube', 'url': url, 'title': url}, project)
    cues = [{'id': 'c1', 'start_us': 0, 'end_us': 900000, 'text': '第一句', 'words': []}]
    base = store.revise(f"transcript:{source['id']}:default", 'transcript', {'source_id': source['id'], 'audio_track_id': 'default',
        'cues': cues, 'asset_ids': [], 'settings': {}}, None, project, initial=None)
    chosen = tmp_path / '輸出'
    chosen.mkdir()
    (store.root / 'config.json').write_text(json.dumps({'roots': {'picked': str(chosen)}, 'providers': []}), encoding='utf-8')
    body = {'kind': 'export', 'source_id': source['id'], 'ranges': [{'start_us': 0, 'end_us': 2000000}], 'transcript_revision': base['id'],
        'formats': ['srt'], 'grouping': 'merge', 'subtitle_timebase': 'sequence', 'alignment_policy': 'allow_segment', 'output_root_id': 'picked'}
    # 還沒探測到標題：退回專案名，不用網址
    first = Worker(store, store.submit(project, body)).export()
    assert sorted(Path(f['path']).name for f in first['saved_files']) == ['歌回精選.export_manifest.json', '歌回精選.srt']
    # 探測後：用影片標題（不合法字元換掉、不把最後一個點之後當副檔名切掉）
    store.revise(f"source:{source['id']}", 'source_metadata', {'title': '【歌回】晚安台 v1.5: 最終版', 'duration_us': 2000000}, None, project, initial=None)
    second = Worker(store, store.submit(project, {**body, 'keep_punctuation': True})).export()
    assert sorted(Path(f['path']).name for f in second['saved_files']) == ['【歌回】晚安台 v1.5_ 最終版.export_manifest.json', '【歌回】晚安台 v1.5_ 最終版.srt']
