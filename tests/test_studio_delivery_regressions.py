"""完整流程的設定傳遞、字幕對映與實際儲存目錄回歸。"""
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from app.studio.api import create_app
from app.studio.contracts import WorkflowRequest
from app.studio.store import Store
from app.studio.worker import Worker


def setup_source(tmp_path):
    store = Store(tmp_path / 'state')
    project = store.create('project', {'name': '冬比測試'})['id']
    source = store.create('source', {'kind': 'local', 'title': '原始影音', 'path': str(tmp_path / 'input.wav')}, project)
    store.revise(f"source:{source['id']}", 'source_metadata', {'duration_us': 2000000}, None, project, initial=None)
    return store, project, source


def test_acquire_cached_asset_forwards_policy_and_new_policy_gets_new_job(tmp_path):
    store, p, s = setup_source(tmp_path)
    payload = b'audio-content'
    path = tmp_path / 'cached.m4a'
    path.write_bytes(payload)
    span = {'start_us': 0, 'end_us': 2000000}
    settings = {'format_policy': {}, 'boundary_policy': 'source_seek', 'quality': 'source'}
    store.create('asset', {'source_id': s['id'], 'kind': 'audio', 'requested_range': span,
        'acquisition_settings': settings, 'path': str(path), 'content_hash': hashlib.sha256(payload).hexdigest(),
        'audio_track_id': 'default'}, p)
    def run(language):
        job = store.submit(p, {'kind': 'acquire', 'source_id': s['id'], 'ranges': [span],
            'language_policy': language, 'music_policy': 'off', 'follow_up': {'profile': 'draft'}})
        result = Worker(store, job).acquire()
        assert result['cache_hit']
        return store.job(result['child_job_ids'][0])
    zh, en = run('zh'), run('en')
    assert zh['body']['language_policy'] == 'zh'
    assert zh['body']['music_policy'] == 'off'
    assert en['id'] != zh['id']
    assert run('en')['id'] == en['id']


def test_download_copy_lands_in_subtitle_studio_named_by_title_and_range(tmp_path):
    # 使用者 2026-09-19：下載／輸出的副本跟字幕一樣放進 subtitle_studio，檔名看得懂（素材名＋時間範圍），不帶內部代碼
    from app.studio.worker import range_label
    assert range_label({'start_us': 6600000000, 'end_us': 7200000000}) == '01h50m00s-02h00m00s'
    assert range_label(None) == ''
    store, p, s = setup_source(tmp_path)
    payload = b'audio-content'
    path = tmp_path / 'cached.m4a'
    path.write_bytes(payload)
    out = tmp_path / '下載'
    out.mkdir()
    (store.root / 'config.json').write_text(json.dumps({'roots': {'chosen': str(out)}}), encoding='utf-8')
    span = {'start_us': 0, 'end_us': 2000000}
    settings = {'format_policy': {}, 'boundary_policy': 'accurate', 'quality': 'source'}
    store.create('asset', {'source_id': s['id'], 'kind': 'audio', 'requested_range': span,
        'acquisition_settings': settings, 'path': str(path), 'content_hash': hashlib.sha256(payload).hexdigest(),
        'audio_track_id': 'default'}, p)
    job = store.submit(p, {'kind': 'acquire', 'source_id': s['id'], 'ranges': [span], 'boundary_policy': 'accurate', 'output_root_id': 'chosen'})
    result = Worker(store, job).acquire()
    saved = Path(result['saved_files'][0]['path'])
    assert saved == out.resolve() / 'subtitle_studio' / '原始影音_00h00m00s-00h00m02s.m4a'
    assert saved.read_bytes() == payload


def test_plan_analysis_policy_contract():
    plan = WorkflowRequest.model_validate({'source_id': 'source', 'analysis': {'language_policy': 'zh', 'music_policy': 'off'}})
    assert plan.model_dump()['analysis']['language_policy'] == 'zh'


@pytest.mark.parametrize('grouping', ['merge', 'separate'])
def test_subtitle_only_manifest_has_each_reordered_source_mapping(tmp_path, grouping):
    store, p, s = setup_source(tmp_path)
    items = [{'id': 'b', 'kind': 'clip', 'start_us': 1000000, 'end_us': 2000000},
             {'id': 'a', 'kind': 'clip', 'start_us': 0, 'end_us': 1000000},
             {'id': 'again', 'kind': 'clip', 'start_us': 0, 'end_us': 1000000}]
    sequence = store.create('sequence', {'source_id': s['id'], 'items': items}, p)
    cues = [{'id': 'first', 'start_us': 0, 'end_us': 900000, 'text': '第一句。', 'words': []},
            {'id': 'second', 'start_us': 1000000, 'end_us': 1900000, 'text': '第二句。', 'words': []}]
    tr = store.create('transcript', {'source_id': s['id'], 'cues': cues}, p)
    job = store.submit(p, {'kind': 'export', 'sequence_revision': sequence['id'], 'transcript_revision': tr['id'],
        'formats': ['srt'], 'grouping': grouping, 'subtitle_timebase': 'clip' if grouping == 'separate' else 'sequence'})
    result = Worker(store, job).export()
    mappings = result['manifest']['mappings']
    assert [m['item_id'] for m in mappings] == ['b', 'a', 'again']
    assert [m['output_start_us'] for m in mappings] == ([0, 0, 0] if grouping == 'separate' else [0, 1000000, 2000000])
    assert mappings[0]['requested_range']['start_us'] == 1000000
    if grouping == 'separate':
        assert [a['provenance']['item_id'] for a in result['artifacts']] == ['b', 'a', 'again']


def test_source_timebase_artifact_mapping_agrees_with_srt(tmp_path):
    store,p,s=setup_source(tmp_path)
    sequence=store.create('sequence',{'source_id':s['id'],'items':[
        {'id':'clip','kind':'clip','start_us':1000000,'end_us':2000000}]},p)
    tr=store.create('transcript',{'source_id':s['id'],'cues':[
        {'id':'cue','start_us':1100000,'end_us':1900000,'text':'來源座標','words':[]}]},p)
    job=store.submit(p,{'kind':'export','sequence_revision':sequence['id'],'transcript_revision':tr['id'],
        'formats':['srt'],'subtitle_timebase':'source'})
    result=Worker(store,job).export()
    artifact=result['artifacts'][0]
    assert '00:00:01,100' in store.artifact_path(artifact['id']).read_text(encoding='utf-8')
    assert artifact['provenance']['mappings'][0]['output_start_us']==1000000
    assert artifact['provenance']['timebase']=='source'


def test_unknown_output_root_rejected_before_job_queued(tmp_path):
    app = create_app(tmp_path, start_workers=False)
    with TestClient(app) as client:
        client.get('/v1/session')
        p = client.post('/v1/projects', json={'name': 'test'}).json()['project_id']
        source = client.post(f'/v1/projects/{p}/sources', json={'kind': 'youtube', 'url': 'https://youtu.be/Yn2mE6_tMC8'}).json()['source_id']
        response = client.post(f'/v1/projects/{p}/jobs', json={'kind': 'acquire', 'source_id': source, 'output_root_id': 'missing'})
        assert response.status_code == 403
        assert not client.get(f'/v1/projects/{p}/jobs').json()['items']


def test_subtitle_export_writes_artifact_and_manifest_to_selected_directory(tmp_path):
    store,p,s=setup_source(tmp_path)
    out=tmp_path/'chosen'
    out.mkdir()
    (store.root/'config.json').write_text(json.dumps({'roots':{'chosen':str(out)}}))
    seq=store.create('sequence',{'source_id':s['id'],'items':[{'id':'a','kind':'clip','start_us':0,'end_us':2000000}]},p)
    tr=store.create('transcript',{'source_id':s['id'],'cues':[{'id':'c','start_us':0,'end_us':1000000,'text':'完整字幕','words':[]}]},p)
    job=store.submit(p,{'kind':'export','sequence_revision':seq['id'],'transcript_revision':tr['id'],
        'formats':['srt'],'output_root_id':'chosen'})
    result=Worker(store,job).run()
    assert len(result['saved_files'])==2
    for saved in result['saved_files']:
        path=Path(saved['path'])
        assert path.resolve().is_relative_to(out.resolve())
        original=store.artifact_path(saved['artifact_id'])
        assert path.read_bytes()==original.read_bytes()


def test_acquire_cache_matches_equivalent_settings_from_panel_download_and_workflow(tmp_path):
    """面板「加入下載」存的設定含 None 欄位與畫質上限；流程快照的音訊取得沒帶 format_policy。兩者對音訊而言相同 → 必須命中快取。"""
    store, p, s = setup_source(tmp_path)
    payload = b'audio-content'
    path = tmp_path / 'cached.webm'
    path.write_bytes(payload)
    span = {'start_us': 0, 'end_us': 2000000}
    panel_settings = {'format_policy': {'allow_transcode': False, 'audio_bitrate_kbps': None, 'audio_track_id': None, 'container': 'source', 'max_fps': None, 'max_height': 720},
                      'boundary_policy': 'source_seek', 'quality': 'source'}
    store.create('asset', {'source_id': s['id'], 'kind': 'audio', 'requested_range': span, 'acquisition_settings': panel_settings,
        'path': str(path), 'content_hash': hashlib.sha256(payload).hexdigest(), 'audio_track_id': 'default'}, p)
    def run(body):
        job = store.submit(p, {'kind': 'acquire', 'source_id': s['id'], 'ranges': [span], 'asset_kind': 'audio', **body})
        return Worker(store, job).acquire()
    assert run({'boundary_policy': 'source_seek'})['cache_hit']                       # 流程子工作：沒帶 format_policy／quality
    assert run({'boundary_policy': 'source_seek', 'format_policy': {'container': 'source', 'max_height': 1080}})['cache_hit']  # 音訊不看畫質
    with pytest.raises(Exception):
        run({'boundary_policy': 'accurate'})                                          # 邊界策略不同＝不同素材，不得命中（本測試沒有真下載）
    with pytest.raises(Exception):
        run({'boundary_policy': 'source_seek', 'format_policy': {'container': 'mp3', 'allow_transcode': True}})  # 容器不同＝不同素材


def test_workflow_audio_acquire_carries_plan_format_and_quality(tmp_path, monkeypatch):
    """流程快照的取得設定（format_policy／quality）必須傳到音訊取得子工作，否則快取簽章與實際下載設定都會走樣。"""
    store, p, s = setup_source(tmp_path)
    plan = WorkflowRequest.model_validate({'source_id': s['id'], 'ranges': [{'start_us': 0, 'end_us': 1000000}],
        'acquisition': {'boundary_policy': 'source_seek', 'quality': 'source', 'format_policy': {'container': 'm4a', 'audio_bitrate_kbps': 128}},
        'analysis': {'mode': 'none'}, 'subtitles': {'policy': 'none'}, 'deliverables': {'formats': ['audio']}}).model_dump()
    plan_id = store.create('plan', plan, p)['id']
    job = store.submit(p, {'kind': 'workflow', 'plan_id': plan_id}, 'workflow')
    worker = Worker(store, job)
    submitted = []
    def child(request, resource):
        submitted.append(request)
        raise RuntimeError('stop after first child')
    monkeypatch.setattr(worker, 'child', child)
    monkeypatch.setattr(worker, 'wait_children', lambda ids: [{'duration_us': 1000000}])
    with pytest.raises(RuntimeError):
        worker.workflow()
    probe = submitted[0]
    assert probe['kind'] == 'probe'
    # 第二個子工作是音訊取得；用同樣的替身再跑到它
    submitted.clear()
    calls = {'n': 0}
    def child2(request, resource):
        submitted.append(request)
        calls['n'] += 1
        if calls['n'] == 1:
            return 'probe_job'
        raise RuntimeError('stop at acquire')
    monkeypatch.setattr(worker, 'child', child2)
    with pytest.raises(RuntimeError):
        worker.workflow()
    acquire = submitted[-1]
    assert acquire['kind'] == 'acquire' and acquire['asset_kind'] == 'audio'
    assert acquire['boundary_policy'] == 'source_seek' and acquire['quality'] == 'source'
    assert acquire['format_policy']['container'] == 'm4a' and acquire['format_policy']['audio_bitrate_kbps'] == 128


def test_export_can_take_explicit_ranges_without_a_sequence(tmp_path):
    """字幕頁「匯出 SRT」整段素材：不改第 1 頁的剪輯清單，直接給 source_id＋ranges。"""
    from app.studio.contracts import JobRequest
    request = JobRequest(kind='export', source_id='s', ranges=[{'start_us': 0, 'end_us': 2000000}], formats=['srt'])
    assert request.sequence_revision is None and request.ranges[0].end_us == 2000000
    with pytest.raises(Exception):
        JobRequest(kind='export', formats=['srt'])  # 兩者都沒有 → 拒絕
    store, p, s = setup_source(tmp_path)
    cues = [{'id': 'first', 'start_us': 0, 'end_us': 900000, 'text': '第一句。', 'words': []},
            {'id': 'second', 'start_us': 1000000, 'end_us': 1900000, 'text': '第二句。', 'words': []}]
    tr = store.create('transcript', {'source_id': s['id'], 'cues': cues}, p)
    job = store.submit(p, {'kind': 'export', 'source_id': s['id'], 'ranges': [{'start_us': 0, 'end_us': 2000000}], 'transcript_revision': tr['id'],
                          'formats': ['srt'], 'grouping': 'merge', 'subtitle_timebase': 'sequence', 'alignment_policy': 'allow_segment'})
    result = Worker(store, job).export()
    assert [a['kind'] for a in result['artifacts']] == ['srt']
    text = Path(store.artifact_path(result['artifacts'][0]['id'])).read_text(encoding='utf-8')
    assert '第一句' in text and '第二句' in text
    assert result['manifest']['sequence_revision'] is None and result['manifest']['mappings'][0]['requested_range'] == {'start_us': 0, 'end_us': 2000000}


def test_downloads_from_the_project_folder_are_named_after_the_material(tmp_path):
    """2026-09-21 真瀏覽器：輸出位置用預設「專案資料目錄」時，下載拿到 artifact_56c5b50f….srt，看不出是哪支影片哪一段。
    與輸出到資料夾同一個命名規則：<素材名>[_NN].srt、<素材名>.export_manifest.json。"""
    from urllib.parse import unquote
    store,p,s=setup_source(tmp_path)
    seq=store.create('sequence',{'source_id':s['id'],'items':[{'id':'a','kind':'clip','start_us':0,'end_us':1000000},
                                                               {'id':'b','kind':'clip','start_us':1000000,'end_us':2000000}]},p)
    tr=store.create('transcript',{'source_id':s['id'],'cues':[{'id':'c','start_us':0,'end_us':900000,'text':'第一段','words':[]},
                                                              {'id':'d','start_us':1100000,'end_us':1900000,'text':'第二段','words':[]}]},p)
    job=store.submit(p,{'kind':'export','sequence_revision':seq['id'],'transcript_revision':tr['id'],'formats':['srt'],
                        'grouping':'separate','subtitle_timebase':'clip'})
    result=Worker(store,job).run()
    store.finish(job['id'],'succeeded',result)
    names=result['download_names']
    srt=[a['id'] for a in result['artifacts']]
    assert [names[i] for i in srt]==['原始影音_01.srt','原始影音_02.srt']
    assert names[result['manifest_artifact']['id']]=='原始影音.export_manifest.json'
    with TestClient(create_app(store.root,start_workers=False)) as client:
        client.get('/v1/session')
        header=client.get(f'/v1/artifacts/{srt[1]}/content').headers['content-disposition']
    assert '原始影音_02.srt' in unquote(header)
